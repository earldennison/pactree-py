use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::path::Path;

use crate::bindings::convert::*;
use crate::bindings::errors::{pact_err_to_py, py_invalid_input};
use crate::bindings::views::PyPactFileProjection;

pub fn select_pact_tree_json(pact_json: &str, selector: &str) -> pact::PactResult<String> {
    pact::selector::select_json(pact_json, selector)
}

pub fn pact_mutation_request_validate_json(request_json: &str) -> pact::PactResult<String> {
    pact::validate_mutation_request_json(request_json)
}

pub fn pact_file_inspect_json(path: &str) -> pact::PactResult<String> {
    pact::file_format::file_inspect_json(Path::new(path))
}

pub fn pact_file_info_json(path: &str) -> pact::PactResult<String> {
    pact::file_format::file_info_json(Path::new(path))
}

pub fn pact_file_validate_json(path: &str) -> pact::PactResult<String> {
    pact::file_format::file_validate_json(Path::new(path))
}

pub fn pact_file_fork_json(
    source_path: &str,
    output_path: Option<&str>,
    mode: &str,
    branch_id: Option<&str>,
    revision: Option<u64>,
    blob_inline_limit: usize,
) -> pact::PactResult<String> {
    let fork_mode = match mode {
        "soft" => pact::file_format::PactForkMode::Soft,
        "overlay" | "delta" => pact::file_format::PactForkMode::Delta { base_pinned: true },
        _ => pact::file_format::PactForkMode::Hard,
    };
    pact::file_format::file_fork_json(
        Path::new(source_path),
        output_path.map(Path::new),
        fork_mode,
        branch_id,
        revision,
        None,
        blob_inline_limit,
    )
}

pub fn pact_file_open_json(
    path: &str,
    branch_id: Option<&str>,
    revision: Option<u64>,
    blob_inline_limit: usize,
) -> pact::PactResult<String> {
    pact::file_format::file_open_json(Path::new(path), branch_id, revision, blob_inline_limit)
}

pub fn pact_file_open_head_fast_json(path: &str) -> pact::PactResult<String> {
    pact::file_format::file_open_head_fast_json(Path::new(path))
}

pub fn pact_file_batch_open_head_fast_json(paths: &[String]) -> pact::PactResult<String> {
    let path_refs = paths.iter().map(|path| Path::new(path)).collect::<Vec<_>>();
    pact::file_format::file_batch_open_head_fast_json(&path_refs)
}

pub fn pact_file_slice_json(
    source_path: &str,
    output_path: &str,
    spec_json: &str,
) -> pact::PactResult<String> {
    pact::file_format::file_slice_json(Path::new(source_path), Path::new(output_path), spec_json)
}

pub fn pact_file_import_check_json(path: &str) -> pact::PactResult<String> {
    pact::file_format::file_import_check_json(Path::new(path))
}

pub fn pact_file_bundle_verify_json(path: &str) -> pact::PactResult<String> {
    pact::file_format::file_bundle_verify_json(Path::new(path))
}

pub fn pact_file_bundle_export_full_json(
    path: &str,
    output_path: &str,
) -> pact::PactResult<String> {
    pact::file_format::file_bundle_export_full_json(Path::new(path), Path::new(output_path))
}

pub fn pact_file_bundle_prune_json(path: &str) -> pact::PactResult<String> {
    pact::file_format::file_bundle_prune_json(Path::new(path))
}

#[pyfunction(name = "_select_pact_tree")]
fn select_pact_tree(py: Python<'_>, pact_json: String, selector: String) -> PyResult<String> {
    py.detach(|| select_pact_tree_json(&pact_json, &selector))
        .map_err(pact_err_to_py)
}

#[pyfunction(name = "_pact_mutation_request_validate")]
fn pact_mutation_request_validate(py: Python<'_>, request_json: String) -> PyResult<String> {
    py.detach(|| pact_mutation_request_validate_json(&request_json))
        .map_err(pact_err_to_py)
}

#[pyfunction(name = "_pact_file_inspect")]
fn pact_file_inspect(py: Python<'_>, path: String) -> PyResult<Py<PyAny>> {
    let json = py
        .detach(|| pact_file_inspect_json(&path))
        .map_err(pact_err_to_py)?;
    let value: serde_json::Value = serde_json::from_str(&json).map_err(|err| {
        PyValueError::new_err(format!("failed to decode pact_file_inspect: {err}"))
    })?;
    to_py_object(py, &value)
}

#[pyfunction(name = "_pact_file_info")]
fn pact_file_info(py: Python<'_>, path: String) -> PyResult<Py<PyAny>> {
    let json = py
        .detach(|| pact_file_info_json(&path))
        .map_err(pact_err_to_py)?;
    let value: serde_json::Value = serde_json::from_str(&json)
        .map_err(|err| PyValueError::new_err(format!("failed to decode pact_file_info: {err}")))?;
    to_py_object(py, &value)
}

#[pyfunction(name = "_pact_file_validate")]
fn pact_file_validate(py: Python<'_>, path: String) -> PyResult<Py<PyAny>> {
    let json = py
        .detach(|| pact_file_validate_json(&path))
        .map_err(pact_err_to_py)?;
    let value: serde_json::Value = serde_json::from_str(&json).map_err(|err| {
        PyValueError::new_err(format!("failed to decode pact_file_validate: {err}"))
    })?;
    to_py_object(py, &value)
}

#[pyfunction(name = "_pact_file_bundle_verify")]
fn pact_file_bundle_verify(py: Python<'_>, path: String) -> PyResult<Py<PyAny>> {
    let json = py
        .detach(|| pact_file_bundle_verify_json(&path))
        .map_err(pact_err_to_py)?;
    let value: serde_json::Value = serde_json::from_str(&json).map_err(|err| {
        PyValueError::new_err(format!("failed to decode pact_file_bundle_verify: {err}"))
    })?;
    to_py_object(py, &value)
}

#[pyfunction(name = "_pact_file_bundle_export_full")]
fn pact_file_bundle_export_full(
    py: Python<'_>,
    path: String,
    output_path: String,
) -> PyResult<Py<PyAny>> {
    let json = py
        .detach(|| pact_file_bundle_export_full_json(&path, &output_path))
        .map_err(pact_err_to_py)?;
    let value: serde_json::Value = serde_json::from_str(&json).map_err(|err| {
        PyValueError::new_err(format!(
            "failed to decode pact_file_bundle_export_full: {err}"
        ))
    })?;
    to_py_object(py, &value)
}

#[pyfunction(name = "_pact_file_bundle_prune")]
fn pact_file_bundle_prune(py: Python<'_>, path: String) -> PyResult<Py<PyAny>> {
    let json = py
        .detach(|| pact_file_bundle_prune_json(&path))
        .map_err(pact_err_to_py)?;
    let value: serde_json::Value = serde_json::from_str(&json).map_err(|err| {
        PyValueError::new_err(format!("failed to decode pact_file_bundle_prune: {err}"))
    })?;
    to_py_object(py, &value)
}

#[pyfunction(name = "_pact_file_projection")]
#[pyo3(signature = (path, branch_id = None, revision = None, blob_inline_limit = 1024, view = None, component_id = None))]
fn pact_file_projection(
    py: Python<'_>,
    path: String,
    branch_id: Option<String>,
    revision: Option<u64>,
    blob_inline_limit: usize,
    view: Option<String>,
    component_id: Option<String>,
) -> PyResult<Py<PyPactFileProjection>> {
    let projection = py
        .detach(|| {
            pact::file_format::file_projection(
                std::path::Path::new(&path),
                branch_id.as_deref(),
                revision,
                blob_inline_limit,
                view.as_deref(),
                component_id.as_deref(),
            )
        })
        .map_err(pact_err_to_py)?;
    Py::new(py, PyPactFileProjection::from_projection(&projection))
}

#[pyfunction(name = "_pact_file_open")]
#[pyo3(signature = (path, branch_id = None, revision = None, blob_inline_limit = 1024))]
fn pact_file_open(
    py: Python<'_>,
    path: String,
    branch_id: Option<String>,
    revision: Option<u64>,
    blob_inline_limit: usize,
) -> PyResult<Py<PyAny>> {
    let json = py
        .detach(|| pact_file_open_json(&path, branch_id.as_deref(), revision, blob_inline_limit))
        .map_err(pact_err_to_py)?;
    let value: serde_json::Value = serde_json::from_str(&json)
        .map_err(|err| PyValueError::new_err(format!("failed to decode pact_file_open: {err}")))?;
    to_py_object(py, &value)
}

#[pyfunction(name = "_pact_file_open_head_fast")]
fn pact_file_open_head_fast(py: Python<'_>, path: String) -> PyResult<Py<PyAny>> {
    let json = py
        .detach(|| pact_file_open_head_fast_json(&path))
        .map_err(pact_err_to_py)?;
    let value: serde_json::Value = serde_json::from_str(&json).map_err(|err| {
        PyValueError::new_err(format!("failed to decode pact_file_open_head_fast: {err}"))
    })?;
    to_py_object(py, &value)
}

#[pyfunction(name = "_pact_file_batch_open_head_fast")]
fn pact_file_batch_open_head_fast(py: Python<'_>, paths: Vec<String>) -> PyResult<Py<PyAny>> {
    let json = py
        .detach(|| pact_file_batch_open_head_fast_json(&paths))
        .map_err(pact_err_to_py)?;
    let value: serde_json::Value = serde_json::from_str(&json).map_err(|err| {
        PyValueError::new_err(format!(
            "failed to decode pact_file_batch_open_head_fast: {err}"
        ))
    })?;
    to_py_object(py, &value)
}

#[pyfunction(name = "_pact_file_text_search")]
fn pact_file_text_search(
    py: Python<'_>,
    path: String,
    query: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let parsed: pact::file_format::PactTextSearchQuery = pythonize::depythonize(query)
        .map_err(|err| py_invalid_input(format!("invalid text-search query: {err}")))?;
    let json = pact::file_format::file_text_search_json(Path::new(&path), parsed)
        .map_err(pact_err_to_py)?;
    let value: serde_json::Value = serde_json::from_str(&json).map_err(|err| {
        PyValueError::new_err(format!("failed to decode pact_file_text_search: {err}"))
    })?;
    to_py_object(py, &value)
}

#[pyfunction(name = "_pact_file_slice")]
fn pact_file_slice(
    py: Python<'_>,
    source_path: String,
    output_path: String,
    spec_json: String,
) -> PyResult<Py<PyAny>> {
    let json = py
        .detach(|| pact_file_slice_json(&source_path, &output_path, &spec_json))
        .map_err(pact_err_to_py)?;
    let value: serde_json::Value = serde_json::from_str(&json)
        .map_err(|err| PyValueError::new_err(format!("failed to decode pact_file_slice: {err}")))?;
    to_py_object(py, &value)
}

#[pyfunction(name = "_pact_file_import_check")]
fn pact_file_import_check(py: Python<'_>, path: String) -> PyResult<Py<PyAny>> {
    let json = py
        .detach(|| pact_file_import_check_json(&path))
        .map_err(pact_err_to_py)?;
    let value: serde_json::Value = serde_json::from_str(&json).map_err(|err| {
        PyValueError::new_err(format!("failed to decode pact_file_import_check: {err}"))
    })?;
    to_py_object(py, &value)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(select_pact_tree, module)?)?;
    module.add_function(wrap_pyfunction!(pact_mutation_request_validate, module)?)?;
    module.add_function(wrap_pyfunction!(pact_file_open, module)?)?;
    module.add_function(wrap_pyfunction!(pact_file_open_head_fast, module)?)?;
    module.add_function(wrap_pyfunction!(pact_file_batch_open_head_fast, module)?)?;
    module.add_function(wrap_pyfunction!(pact_file_projection, module)?)?;
    module.add_function(wrap_pyfunction!(pact_file_inspect, module)?)?;
    module.add_function(wrap_pyfunction!(pact_file_info, module)?)?;
    module.add_function(wrap_pyfunction!(pact_file_validate, module)?)?;
    module.add_function(wrap_pyfunction!(pact_file_bundle_verify, module)?)?;
    module.add_function(wrap_pyfunction!(pact_file_bundle_export_full, module)?)?;
    module.add_function(wrap_pyfunction!(pact_file_bundle_prune, module)?)?;
    module.add_function(wrap_pyfunction!(pact_file_slice, module)?)?;
    module.add_function(wrap_pyfunction!(pact_file_import_check, module)?)?;
    module.add_function(wrap_pyfunction!(pact_file_text_search, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests;
