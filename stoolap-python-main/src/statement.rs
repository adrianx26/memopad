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
use pyo3::types::PyList;
use std::sync::Arc;

use stoolap::api::Database as ApiDatabase;
use stoolap::CachedPlanRef;

use crate::database::{first_row_to_dict, rows_to_dicts, rows_to_raw, to_named_params};
use crate::error::to_py;
use crate::value::{parse_params, BindParams};

/// A prepared SQL statement.
///
/// Parses SQL once and reuses the cached execution plan on every call.
#[pyclass]
pub struct PreparedStatement {
    db: Arc<ApiDatabase>,
    sql_text: String,
    plan: CachedPlanRef,
}

impl PreparedStatement {
    pub fn new(db: Arc<ApiDatabase>, sql: &str) -> PyResult<Self> {
        let plan = db.cached_plan(sql).map_err(to_py)?;
        Ok(Self {
            db,
            sql_text: sql.to_string(),
            plan,
        })
    }
}

#[pymethods]
impl PreparedStatement {
    /// Execute the prepared statement (DML). Returns rows affected.
    #[pyo3(signature = (params=None))]
    fn execute(&self, py: Python<'_>, params: Option<&Bound<'_, PyAny>>) -> PyResult<i64> {
        let bind = parse_params(params)?;
        let plan = self.plan.clone();
        py.allow_threads(|| match bind {
            BindParams::Positional(p) => self.db.execute_plan(&plan, p).map_err(to_py),
            BindParams::Named(named) => {
                self.db.execute_named_plan(&plan, to_named_params(&named)).map_err(to_py)
            }
        })
    }

    /// Query rows using the prepared statement. Returns list of dicts.
    #[pyo3(signature = (params=None))]
    fn query(&self, py: Python<'_>, params: Option<&Bound<'_, PyAny>>) -> PyResult<PyObject> {
        let bind = parse_params(params)?;
        let plan = self.plan.clone();
        let rows = py.allow_threads(|| match bind {
            BindParams::Positional(p) => self.db.query_plan(&plan, p).map_err(to_py),
            BindParams::Named(named) => {
                self.db.query_named_plan(&plan, to_named_params(&named)).map_err(to_py)
            }
        })?;
        rows_to_dicts(py, rows)
    }

    /// Query a single row. Returns dict or None.
    #[pyo3(signature = (params=None))]
    fn query_one(&self, py: Python<'_>, params: Option<&Bound<'_, PyAny>>) -> PyResult<PyObject> {
        let bind = parse_params(params)?;
        let plan = self.plan.clone();
        let rows = py.allow_threads(|| match bind {
            BindParams::Positional(p) => self.db.query_plan(&plan, p).map_err(to_py),
            BindParams::Named(named) => {
                self.db.query_named_plan(&plan, to_named_params(&named)).map_err(to_py)
            }
        })?;
        first_row_to_dict(py, rows)
    }

    /// Query rows in raw format. Returns { columns: [...], rows: [[...], ...] }.
    #[pyo3(signature = (params=None))]
    fn query_raw(&self, py: Python<'_>, params: Option<&Bound<'_, PyAny>>) -> PyResult<PyObject> {
        let bind = parse_params(params)?;
        let plan = self.plan.clone();
        let rows = py.allow_threads(|| match bind {
            BindParams::Positional(p) => self.db.query_plan(&plan, p).map_err(to_py),
            BindParams::Named(named) => {
                self.db.query_named_plan(&plan, to_named_params(&named)).map_err(to_py)
            }
        })?;
        rows_to_raw(py, rows)
    }

    /// Execute with multiple parameter sets.
    ///
    /// Auto-wraps in a transaction. Returns total rows affected.
    #[pyo3(signature = (params_list,))]
    fn execute_batch(&self, py: Python<'_>, params_list: &Bound<'_, PyList>) -> PyResult<i64> {
        // Parse all param sets while holding GIL
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

        let plan = self.plan.clone();

        // Execute without GIL
        py.allow_threads(|| {
            let stmt = plan.statement.as_ref();
            let mut tx = self.db.begin().map_err(to_py)?;
            let mut total = 0i64;
            for params in all_params {
                total += tx.execute_prepared(stmt, params).map_err(to_py)?;
            }
            tx.commit().map_err(to_py)?;
            Ok(total)
        })
    }

    /// Get the SQL text of this prepared statement.
    #[getter]
    fn sql(&self) -> &str {
        &self.sql_text
    }

    fn __repr__(&self) -> String {
        format!("PreparedStatement({:?})", self.sql_text)
    }
}
