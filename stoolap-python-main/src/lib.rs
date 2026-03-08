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

mod database;
mod error;
mod statement;
mod transaction;
mod value;

use pyo3::prelude::*;

/// Native Stoolap database bindings for Python.
#[pymodule]
fn _stoolap(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<database::Database>()?;
    m.add_class::<transaction::Transaction>()?;
    m.add_class::<statement::PreparedStatement>()?;
    m.add_class::<value::PyVector>()?;
    m.add("StoolapError", m.py().get_type::<error::StoolapError>())?;
    Ok(())
}
