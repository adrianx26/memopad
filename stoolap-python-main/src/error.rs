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

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

// Custom Python exception for Stoolap errors.
pyo3::create_exception!(stoolap, StoolapError, PyRuntimeError);

/// Convert a stoolap::Error into a PyErr.
pub fn to_py(err: stoolap::Error) -> PyErr {
    StoolapError::new_err(err.to_string())
}
