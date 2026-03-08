// Copyright 2025 Stoolap Contributors
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString};
use std::sync::Arc;

use stoolap::api::Database as ApiDatabase;

use crate::error::to_py;
use crate::statement::PreparedStatement;
use crate::transaction::Transaction;
use crate::value::{parse_params, value_to_py, BindParams};

/// A Stoolap database connection.
///
/// Open with `Database.open(path)`. Use `:memory:` for in-memory databases.
#[pyclass]
pub struct Database {
    db: Arc<ApiDatabase>,
}

#[pymethods]
impl Database {
    /// Open a database connection.
    ///
    /// Accepts:
    /// - `:memory:` or empty string for in-memory database
    /// - `memory://` for in-memory database
    /// - `./mydb` or `file:///path/to/db` for file-based database
    #[staticmethod]
    fn open(path: &str) -> PyResult<Self> {
        let dsn = translate_path(path);
        let db = ApiDatabase::open(&dsn).map_err(to_py)?;
        Ok(Self { db: Arc::new(db) })
    }

    /// Execute a DDL/DML statement. Returns the number of rows affected.
    ///
    /// Parameters can be a list/tuple (positional: $1, $2, ...) or dict (named: :key).
    #[pyo3(signature = (sql, params=None))]
    fn execute(&self, py: Python<'_>, sql: &str, params: Option<&Bound<'_, PyAny>>) -> PyResult<i64> {
        let bind = parse_params(params)?;
        py.allow_threads(|| match bind {
            BindParams::Positional(p) => self.db.execute(sql, p).map_err(to_py),
            BindParams::Named(named) => {
                self.db.execute_named(sql, to_named_params(&named)).map_err(to_py)
            }
        })
    }

    /// Execute one or more SQL statements separated by semicolons.
    #[pyo3(signature = (sql,))]
    fn exec(&self, py: Python<'_>, sql: &str) -> PyResult<()> {
        let sql = sql.to_string();
        py.allow_threads(|| {
            for stmt in split_sql_statements(&sql) {
                let trimmed = stmt.trim();
                if trimmed.is_empty() {
                    continue;
                }
                self.db.execute(trimmed, ()).map_err(to_py)?;
            }
            Ok(())
        })
    }

    /// Query rows as a list of dicts.
    ///
    /// Each row is a dict with column names as keys.
    #[pyo3(signature = (sql, params=None))]
    fn query(&self, py: Python<'_>, sql: &str, params: Option<&Bound<'_, PyAny>>) -> PyResult<PyObject> {
        let bind = parse_params(params)?;
        let rows_result = py.allow_threads(|| match bind {
            BindParams::Positional(p) => self.db.query(sql, p).map_err(to_py),
            BindParams::Named(named) => {
                self.db.query_named(sql, to_named_params(&named)).map_err(to_py)
            }
        })?;
        rows_to_dicts(py, rows_result)
    }

    /// Query a single row as a dict. Returns None if no rows.
    #[pyo3(signature = (sql, params=None))]
    fn query_one(
        &self,
        py: Python<'_>,
        sql: &str,
        params: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<PyObject> {
        let bind = parse_params(params)?;
        let rows_result = py.allow_threads(|| match bind {
            BindParams::Positional(p) => self.db.query(sql, p).map_err(to_py),
            BindParams::Named(named) => {
                self.db.query_named(sql, to_named_params(&named)).map_err(to_py)
            }
        })?;
        first_row_to_dict(py, rows_result)
    }

    /// Query rows in raw columnar format.
    ///
    /// Returns a dict with 'columns' (list of str) and 'rows' (list of lists).
    #[pyo3(signature = (sql, params=None))]
    fn query_raw(
        &self,
        py: Python<'_>,
        sql: &str,
        params: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<PyObject> {
        let bind = parse_params(params)?;
        let rows_result = py.allow_threads(|| match bind {
            BindParams::Positional(p) => self.db.query(sql, p).map_err(to_py),
            BindParams::Named(named) => {
                self.db.query_named(sql, to_named_params(&named)).map_err(to_py)
            }
        })?;
        rows_to_raw(py, rows_result)
    }

    /// Execute the same SQL with multiple parameter sets.
    ///
    /// Automatically wraps in a transaction. Returns total rows affected.
    #[pyo3(signature = (sql, params_list))]
    fn execute_batch(
        &self,
        py: Python<'_>,
        sql: &str,
        params_list: &Bound<'_, PyList>,
    ) -> PyResult<i64> {
        let sql = sql.to_string();

        // Parse all param sets on the Python thread (need GIL)
        let mut all_params = Vec::with_capacity(params_list.len());
        for item in params_list.iter() {
            let bind = parse_params(Some(&item))?;
            match bind {
                BindParams::Positional(p) => all_params.push(p),
                BindParams::Named(_) => {
                    return Err(pyo3::exceptions::PyTypeError::new_err(
                        "execute_batch only supports positional parameters (list/tuple)",
                    ));
                }
            }
        }

        // Execute without GIL
        py.allow_threads(|| {
            use stoolap::parser::Parser;
            let mut parser = Parser::new(&sql);
            let program = parser.parse_program().map_err(|e| {
                crate::error::StoolapError::new_err(e.to_string())
            })?;
            let stmt = program.statements.first().ok_or_else(|| {
                crate::error::StoolapError::new_err("No SQL statement found")
            })?;

            let mut tx = self.db.begin().map_err(to_py)?;
            let mut total = 0i64;
            for params in all_params {
                total += tx.execute_prepared(stmt, params).map_err(to_py)?;
            }
            tx.commit().map_err(to_py)?;
            Ok(total)
        })
    }

    /// Create a prepared statement.
    ///
    /// Parses SQL once and caches the execution plan.
    fn prepare(&self, sql: &str) -> PyResult<PreparedStatement> {
        PreparedStatement::new(Arc::clone(&self.db), sql)
    }

    /// Begin a transaction.
    fn begin(&self, py: Python<'_>) -> PyResult<Transaction> {
        let tx = py.allow_threads(|| self.db.begin().map_err(to_py))?;
        Ok(Transaction::from_tx(tx))
    }

    /// Close the database connection.
    fn close(&self, py: Python<'_>) -> PyResult<()> {
        py.allow_threads(|| self.db.close().map_err(to_py))
    }

    fn __repr__(&self) -> String {
        "Database(open)".to_string()
    }
}

/// Translate user-friendly paths to Stoolap DSN format.
fn translate_path(path: &str) -> String {
    let trimmed = path.trim();
    if trimmed.is_empty() || trimmed == ":memory:" {
        "memory://".to_string()
    } else if trimmed.starts_with("memory://") || trimmed.starts_with("file://") {
        trimmed.to_string()
    } else {
        format!("file://{trimmed}")
    }
}

/// Convert named params to stoolap NamedParams.
pub fn to_named_params(named: &[(String, stoolap::core::Value)]) -> stoolap::api::NamedParams {
    let mut np = stoolap::api::NamedParams::new();
    for (key, val) in named {
        np.insert(key, val.clone());
    }
    np
}

/// Convert Rows iterator to a list of Python dicts.
pub fn rows_to_dicts(py: Python<'_>, rows: stoolap::api::Rows) -> PyResult<PyObject> {
    let columns: Vec<String> = rows.columns().to_vec();
    // Pre-allocate column names as PyString once, reuse for every row
    let py_col_names: Vec<Bound<'_, PyString>> =
        columns.iter().map(|c| PyString::new(py, c)).collect();
    let result = PyList::empty(py);

    for row_result in rows {
        let row = row_result.map_err(to_py)?;
        let dict = PyDict::new(py);
        for (i, col) in py_col_names.iter().enumerate() {
            let val = match row.get_value(i) {
                Some(v) => value_to_py(py, v),
                None => py.None(),
            };
            dict.set_item(col, val)?;
        }
        result.append(dict)?;
    }

    Ok(result.into_any().unbind())
}

/// Convert Rows iterator to first row dict or None.
pub fn first_row_to_dict(py: Python<'_>, mut rows: stoolap::api::Rows) -> PyResult<PyObject> {
    let columns: Vec<String> = rows.columns().to_vec();

    if let Some(row_result) = rows.next() {
        let row = row_result.map_err(to_py)?;
        let dict = PyDict::new(py);
        // Single row — pre-allocate column names as PyString
        let py_col_names: Vec<Bound<'_, PyString>> =
            columns.iter().map(|c| PyString::new(py, c)).collect();
        for (i, col) in py_col_names.iter().enumerate() {
            let val = match row.get_value(i) {
                Some(v) => value_to_py(py, v),
                None => py.None(),
            };
            dict.set_item(col, val)?;
        }
        return Ok(dict.into_any().unbind());
    }

    Ok(py.None())
}

/// Convert Rows to raw format: { columns: [...], rows: [[...], ...] }
pub fn rows_to_raw(py: Python<'_>, rows: stoolap::api::Rows) -> PyResult<PyObject> {
    let columns: Vec<String> = rows.columns().to_vec();
    let py_columns = PyList::new(py, &columns)?;
    let py_rows = PyList::empty(py);

    for row_result in rows {
        let row = row_result.map_err(to_py)?;
        let py_row = PyList::empty(py);
        for i in 0..columns.len() {
            let val = match row.get_value(i) {
                Some(v) => value_to_py(py, v),
                None => py.None(),
            };
            py_row.append(val)?;
        }
        py_rows.append(py_row)?;
    }

    let result = PyDict::new(py);
    result.set_item("columns", py_columns)?;
    result.set_item("rows", py_rows)?;
    Ok(result.into_any().unbind())
}

/// Split SQL statements by semicolons, handling quotes and comments.
fn split_sql_statements(input: &str) -> Vec<String> {
    let mut statements = Vec::new();
    let mut current = String::new();
    let mut in_single = false;
    let mut in_double = false;
    let mut in_line_comment = false;
    let mut in_block_comment = false;

    let chars: Vec<char> = input.chars().collect();
    let mut i = 0;

    while i < chars.len() {
        let c = chars[i];

        if in_line_comment {
            if c == '\n' {
                in_line_comment = false;
                current.push(c);
            }
            i += 1;
            continue;
        }

        if !in_single && !in_double && !in_block_comment && c == '-' && i + 1 < chars.len() && chars[i + 1] == '-' {
            let next = if i + 2 < chars.len() { chars[i + 2] } else { '\0' };
            if next == '\0' || next == ' ' || next == '\t' || next == '\n' || next == '\r' {
                in_line_comment = true;
                i += 2;
                continue;
            }
        }

        if in_block_comment {
            if c == '*' && i + 1 < chars.len() && chars[i + 1] == '/' {
                in_block_comment = false;
                i += 2;
                continue;
            }
            i += 1;
            continue;
        }

        if !in_single && !in_double && c == '/' && i + 1 < chars.len() && chars[i + 1] == '*' {
            in_block_comment = true;
            i += 2;
            continue;
        }

        if !in_block_comment && !in_line_comment {
            if c == '\'' && (i == 0 || chars[i - 1] != '\\') {
                in_single = !in_single;
            } else if c == '"' && (i == 0 || chars[i - 1] != '\\') {
                in_double = !in_double;
            }
        }

        if c == ';' && !in_single && !in_double && !in_block_comment && !in_line_comment {
            statements.push(current.clone());
            current.clear();
        } else {
            current.push(c);
        }

        i += 1;
    }

    if !current.is_empty() {
        statements.push(current);
    }

    statements
}
