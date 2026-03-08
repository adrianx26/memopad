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

use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::sync::GILOnceCell;
use pyo3::types::{
    PyBool, PyDateAccess, PyDateTime, PyDict, PyFloat, PyInt, PyList, PyString, PyTimeAccess,
    PyTuple, timezone_utc,
};
use chrono::{Datelike, Timelike};

use stoolap::api::ParamVec;
use stoolap::core::Value;

/// A vector of f32 values for similarity search.
///
/// Wraps a list of floats so that Stoolap stores them as a native VECTOR
/// rather than a JSON array.
///
/// Usage:
///     from stoolap import Vector
///     v = Vector([0.1, 0.2, 0.3])
///     db.execute("INSERT INTO t (embedding) VALUES ($1)", [v])
#[pyclass(name = "Vector")]
#[derive(Clone)]
pub struct PyVector {
    pub data: Vec<f32>,
}

#[pymethods]
impl PyVector {
    #[new]
    fn new(data: Vec<f32>) -> Self {
        PyVector { data }
    }

    fn __repr__(&self) -> String {
        format!("Vector({:?})", self.data)
    }

    fn __len__(&self) -> usize {
        self.data.len()
    }

    /// Return the vector as a Python list of floats.
    fn to_list(&self) -> Vec<f32> {
        self.data.clone()
    }
}

/// Cached `json.dumps` callable — avoids module lookup per JSON parameter.
static JSON_DUMPS: GILOnceCell<PyObject> = GILOnceCell::new();

fn get_json_dumps<'py>(py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
    let obj = JSON_DUMPS.get_or_try_init(py, || {
        py.import("json")?.getattr("dumps").map(|f| f.unbind())
    })?;
    Ok(obj.bind(py).clone())
}

/// Parsed bind parameters from Python.
pub enum BindParams {
    Positional(ParamVec),
    Named(Vec<(String, Value)>),
}

/// Convert a single Python object to a Stoolap Value.
pub fn py_to_value(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    // Check None first
    if obj.is_none() {
        return Ok(Value::null_unknown());
    }

    // Bool must be checked before int (bool is subclass of int in Python)
    if let Ok(b) = obj.downcast::<PyBool>() {
        return Ok(Value::Boolean(b.is_true()));
    }

    if let Ok(i) = obj.downcast::<PyInt>() {
        let val: i64 = i.extract()?;
        return Ok(Value::Integer(val));
    }

    if let Ok(f) = obj.downcast::<PyFloat>() {
        let val: f64 = f.extract()?;
        return Ok(Value::Float(val));
    }

    if let Ok(s) = obj.downcast::<PyString>() {
        let val: String = s.extract()?;
        return Ok(Value::text(&val));
    }

    // Check for datetime.datetime (fast downcast via C API)
    if let Ok(dt) = obj.downcast::<PyDateTime>() {
        return py_datetime_to_value(dt);
    }

    // Vector -> native VECTOR value
    if let Ok(v) = obj.downcast::<PyVector>() {
        let v: PyRef<'_, PyVector> = v.try_borrow()?;
        return Ok(Value::vector(v.data.clone()));
    }

    // dict/list -> JSON string
    if obj.downcast::<PyDict>().is_ok() || obj.downcast::<PyList>().is_ok() {
        let dumps = get_json_dumps(obj.py())?;
        let json_str: String = dumps.call1((obj,))?.extract()?;
        return Ok(Value::json(&json_str));
    }

    Err(PyTypeError::new_err(format!(
        "Unsupported parameter type: {}",
        obj.get_type().name()?
    )))
}

/// Convert a Python datetime to a Stoolap Timestamp value.
///
/// Extracts components directly via PyO3's C API (no Python method calls).
fn py_datetime_to_value(dt: &Bound<'_, PyDateTime>) -> PyResult<Value> {
    use chrono::{NaiveDate, TimeZone, Utc};
    use pyo3::types::PyTzInfoAccess;

    let year = dt.get_year();
    let month = dt.get_month() as u32;
    let day = dt.get_day() as u32;
    let hour = dt.get_hour() as u32;
    let minute = dt.get_minute() as u32;
    let second = dt.get_second() as u32;
    let microsecond = dt.get_microsecond();

    let tzinfo = dt.get_tzinfo();

    if tzinfo.is_none() {
        // Naive datetime -> treat as UTC
        if let Some(naive) = NaiveDate::from_ymd_opt(year, month, day)
            .and_then(|d| d.and_hms_micro_opt(hour, minute, second, microsecond))
        {
            return Ok(Value::Timestamp(Utc.from_utc_datetime(&naive)));
        }
    } else {
        // Timezone-aware -> get UTC offset and convert
        let utc_offset = dt.call_method0("utcoffset")?;
        if !utc_offset.is_none() {
            let total_seconds: f64 = utc_offset.call_method0("total_seconds")?.extract()?;
            let offset_secs = total_seconds as i64;

            if let Some(naive_local) = NaiveDate::from_ymd_opt(year, month, day)
                .and_then(|d| d.and_hms_micro_opt(hour, minute, second, microsecond))
            {
                // Subtract UTC offset to get UTC time
                let naive_utc =
                    naive_local - chrono::Duration::seconds(offset_secs);
                return Ok(Value::Timestamp(Utc.from_utc_datetime(&naive_utc)));
            }
        }
    }

    // Fallback: use Python .timestamp() method
    let ts: f64 = dt.call_method0("timestamp")?.extract()?;
    let secs = ts.floor() as i64;
    let nsecs = ((ts - ts.floor()) * 1_000_000_000.0).round() as u32;
    match chrono::DateTime::from_timestamp(secs, nsecs) {
        Some(result) => Ok(Value::Timestamp(result)),
        None => {
            let iso: String = dt.call_method0("isoformat")?.extract()?;
            Ok(Value::text(&iso))
        }
    }
}

/// Convert a Stoolap Value to a Python object.
pub fn value_to_py(py: Python<'_>, val: &Value) -> PyObject {
    match val {
        Value::Null(_) => py.None(),
        Value::Boolean(b) => b.into_pyobject(py).unwrap().to_owned().into_any().unbind(),
        Value::Integer(i) => i.into_pyobject(py).unwrap().to_owned().into_any().unbind(),
        Value::Float(f) => f.into_pyobject(py).unwrap().to_owned().into_any().unbind(),
        Value::Text(s) => s.as_str().into_pyobject(py).unwrap().to_owned().into_any().unbind(),
        Value::Timestamp(ts) => {
            let utc_tz = timezone_utc(py);
            match PyDateTime::new(
                py,
                ts.year(),
                ts.month() as u8,
                ts.day() as u8,
                ts.hour() as u8,
                ts.minute() as u8,
                ts.second() as u8,
                ts.timestamp_subsec_micros(),
                Some(&utc_tz),
            ) {
                Ok(dt) => dt.into_any().unbind(),
                Err(_) => {
                    // Fallback to ISO string
                    let iso = ts.format("%Y-%m-%dT%H:%M:%S%.fZ").to_string();
                    iso.into_pyobject(py).unwrap().to_owned().into_any().unbind()
                }
            }
        }
        Value::Extension(_) => {
            if let Some(floats) = val.as_vector_f32() {
                let list = PyList::new(py, &floats).unwrap();
                list.into_any().unbind()
            } else if let Some(s) = val.as_json() {
                s.into_pyobject(py).unwrap().to_owned().into_any().unbind()
            } else {
                format!("{}", val).into_pyobject(py).unwrap().to_owned().into_any().unbind()
            }
        }
    }
}

/// Parse Python params into BindParams.
/// Accepts: list (positional), tuple (positional), dict (named), or None.
pub fn parse_params(params: Option<&Bound<'_, PyAny>>) -> PyResult<BindParams> {
    let params = match params {
        None => return Ok(BindParams::Positional(ParamVec::new())),
        Some(p) => p,
    };

    if params.is_none() {
        return Ok(BindParams::Positional(ParamVec::new()));
    }

    // Try list first (most common)
    if let Ok(list) = params.downcast::<PyList>() {
        let mut values = ParamVec::new();
        for item in list.iter() {
            values.push(py_to_value(&item)?);
        }
        return Ok(BindParams::Positional(values));
    }

    // Tuple
    if let Ok(tuple) = params.downcast::<PyTuple>() {
        let mut values = ParamVec::new();
        for item in tuple.iter() {
            values.push(py_to_value(&item)?);
        }
        return Ok(BindParams::Positional(values));
    }

    // Dict -> named params
    if let Ok(dict) = params.downcast::<PyDict>() {
        let mut named = Vec::with_capacity(dict.len());
        for (key, val) in dict.iter() {
            let key_str: String = key.extract()?;
            let clean = key_str
                .trim_start_matches(':')
                .trim_start_matches('@')
                .trim_start_matches('$')
                .to_string();
            named.push((clean, py_to_value(&val)?));
        }
        return Ok(BindParams::Named(named));
    }

    Err(PyTypeError::new_err(
        "Parameters must be a list, tuple, or dict",
    ))
}
