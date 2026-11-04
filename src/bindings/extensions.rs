use pyo3::prelude::*;

use crate::bindings::convert::to_py_object;
use crate::bindings::errors::{pact_err_to_py, py_invalid_input};

#[pyclass(module = "pactree.pactree_engine", name = "ExtensionWriter")]
pub struct PyExtensionWriter {
    inner: pact::PactExtensionWriter,
}

#[pymethods]
impl PyExtensionWriter {
    #[new]
    #[pyo3(signature = (path, branch_id=None))]
    fn new(path: String, branch_id: Option<String>) -> PyResult<Self> {
        Self::open(path, branch_id)
    }

    #[staticmethod]
    #[pyo3(signature = (path, branch_id=None))]
    fn open(path: String, branch_id: Option<String>) -> PyResult<Self> {
        let inner =
            pact::PactExtensionWriter::open(path, branch_id.as_deref()).map_err(pact_err_to_py)?;
        Ok(Self { inner })
    }

    fn append(&self, py: Python<'_>, records: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        let records =
            pythonize::depythonize::<Vec<pact::PactExtensionRecord>>(records).map_err(|error| {
                py_invalid_input(format!("invalid Pact extension records: {error}"))
            })?;
        let commit = py
            .detach(|| self.inner.append(records))
            .map_err(pact_err_to_py)?;
        to_py_object(py, &commit)
    }

    #[getter]
    fn path(&self) -> String {
        self.inner.path().display().to_string()
    }

    #[getter]
    fn branch_id(&self) -> String {
        self.inner.branch_id().to_string()
    }
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PyExtensionWriter>()?;
    Ok(())
}
