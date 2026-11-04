pub(crate) mod append_writer;
pub(crate) mod authoring;
pub(crate) mod context_tree;
pub(crate) mod convert;
pub(crate) mod errors;
pub(crate) mod extensions;
pub(crate) mod file_api;
pub(crate) mod placement;
pub(crate) mod views;

use pyo3::prelude::*;

pub(crate) fn register_all(module: &Bound<'_, PyModule>) -> PyResult<()> {
    errors::register(module)?;
    extensions::register(module)?;
    placement::register(module)?;
    authoring::register(module)?;
    views::register(module)?;
    file_api::register(module)?;
    context_tree::register(module)?;
    append_writer::register(module)?;
    Ok(())
}
