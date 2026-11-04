use pyo3::prelude::*;

mod bindings;

#[pymodule]
fn pactree_engine(module: &Bound<'_, PyModule>) -> PyResult<()> {
    bindings::register_all(module)
}
