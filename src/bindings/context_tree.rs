use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyList;

use crate::bindings::convert::*;
use crate::bindings::errors::{pact_err_to_py, py_invalid_input, PactError};
use crate::bindings::file_api::pact_file_fork_json;
use crate::bindings::placement::placement_arg;
use crate::bindings::views::{selected_component_to_py, PyPactView};

#[pyclass(subclass, module = "pactree.pactree_engine", name = "ContextTree")]
pub struct PyContextTree {
    inner: pact::ContextTree,
}

#[pyclass(module = "pactree.pactree_engine", name = "ReadHandle")]
pub struct PyReadHandle {
    indexed: Option<pact::PactFileReadHandle>,
    path: std::path::PathBuf,
    branch_id: Option<String>,
}

#[pymethods]
impl PyReadHandle {
    #[new]
    #[pyo3(signature = (path, branch_id=None))]
    fn new(py: Python<'_>, path: String, branch_id: Option<String>) -> PyResult<Self> {
        Self::open(py, path, branch_id)
    }

    #[staticmethod]
    #[pyo3(signature = (path, branch_id=None))]
    fn open(py: Python<'_>, path: String, branch_id: Option<String>) -> PyResult<Self> {
        let path = std::path::PathBuf::from(path);
        let selected_branch = branch_id.clone();
        let indexed = py
            .detach(|| pact::PactFileReadHandle::open(path.clone(), selected_branch.as_deref()))
            .map_err(pact_err_to_py)?;
        Ok(Self {
            indexed: Some(indexed),
            path,
            branch_id,
        })
    }

    fn component(&self, py: Python<'_>, component_id: String) -> PyResult<Py<PyAny>> {
        self.ensure_open()?;
        let indexed = self.indexed()?;
        let value = py
            .detach(|| indexed.component(&component_id))
            .map_err(pact_err_to_py)?;
        to_py_object(py, &value)
    }

    #[pyo3(signature = (revision=None))]
    fn project(&self, py: Python<'_>, revision: Option<u64>) -> PyResult<Py<PyAny>> {
        self.ensure_open()?;
        let indexed = self.indexed()?;
        let projection = py
            .detach(|| indexed.project(revision))
            .map_err(pact_err_to_py)?;
        to_py_object(py, &projection)
    }

    fn content_payload(&self, py: Python<'_>, content_id: String) -> PyResult<Py<PyAny>> {
        self.ensure_open()?;
        let indexed = self.indexed()?;
        let value = py
            .detach(|| indexed.content_payload(&content_id))
            .map_err(pact_err_to_py)?;
        to_py_object(py, &value)
    }

    fn traces(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.ensure_open()?;
        let indexed = self.indexed()?;
        let value = py.detach(|| indexed.traces()).map_err(pact_err_to_py)?;
        to_py_object(py, &value)
    }

    fn rewind_targets(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.ensure_open()?;
        let indexed = self.indexed()?;
        let value = py
            .detach(|| indexed.rewind_targets())
            .map_err(pact_err_to_py)?;
        to_py_object(py, &value)
    }

    #[pyo3(signature = (mode = "current_surface", anchor_row_id = None, anchor_seq = None, direction = "before", limit = 80))]
    fn stream(
        &self,
        py: Python<'_>,
        mode: &str,
        anchor_row_id: Option<String>,
        anchor_seq: Option<u64>,
        direction: &str,
        limit: usize,
    ) -> PyResult<Py<PyAny>> {
        self.ensure_open()?;
        let indexed = self.indexed()?;
        let value = py
            .detach(|| indexed.stream(mode, anchor_row_id.as_deref(), anchor_seq, direction, limit))
            .map_err(pact_err_to_py)?;
        to_py_object(py, &value)
    }

    #[pyo3(signature = (kind=None, index_key=None, cursor=None, direction="before", limit=80, at_revision=None))]
    fn extensions(
        &self,
        py: Python<'_>,
        kind: Option<String>,
        index_key: Option<String>,
        cursor: Option<&Bound<'_, PyAny>>,
        direction: &str,
        limit: usize,
        at_revision: Option<u64>,
    ) -> PyResult<Py<PyAny>> {
        self.ensure_open()?;
        let cursor = cursor
            .map(|value| {
                pythonize::depythonize::<pact::PactExtensionCursor>(value).map_err(|error| {
                    py_invalid_input(format!("invalid Pact extension cursor: {error}"))
                })
            })
            .transpose()?;
        let indexed = self.indexed()?;
        let page = py
            .detach(|| {
                indexed.extensions_at(
                    kind.as_deref(),
                    index_key.as_deref(),
                    cursor.as_ref(),
                    direction,
                    limit,
                    at_revision,
                )
            })
            .map_err(pact_err_to_py)?;
        to_py_object(py, &page)
    }

    #[pyo3(signature = (record_id, at_revision=None))]
    fn extension(
        &self,
        py: Python<'_>,
        record_id: String,
        at_revision: Option<u64>,
    ) -> PyResult<Py<PyAny>> {
        self.ensure_open()?;
        let indexed = self.indexed()?;
        let row = py
            .detach(|| indexed.extension_at(&record_id, at_revision))
            .map_err(pact_err_to_py)?;
        to_py_object(py, &row)
    }

    #[pyo3(signature = (output_path, revision=None))]
    fn fork(
        &self,
        py: Python<'_>,
        output_path: std::path::PathBuf,
        revision: Option<u64>,
    ) -> PyResult<Py<PyAny>> {
        self.ensure_open()?;
        let indexed = self.indexed()?;
        let info = indexed.info().map_err(pact_err_to_py)?;
        let branch_id = info["branch_id"]
            .as_str()
            .ok_or_else(|| PactError::new_err("read handle branch id is missing"))?
            .to_string();
        let source = self.path.display().to_string();
        let output_path = output_path.display().to_string();
        let json = py
            .detach(|| {
                pact_file_fork_json(
                    &source,
                    Some(&output_path),
                    "delta",
                    Some(&branch_id),
                    revision,
                    1024,
                )
            })
            .map_err(pact_err_to_py)?;
        let value: serde_json::Value = serde_json::from_str(&json).map_err(|error| {
            PyValueError::new_err(format!("failed to decode pinned fork report: {error}"))
        })?;
        to_py_object(py, &value)
    }

    fn info(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.ensure_open()?;
        let indexed = self.indexed()?;
        let value = py.detach(|| indexed.info()).map_err(pact_err_to_py)?;
        to_py_object(py, &value)
    }

    fn refresh(&mut self, py: Python<'_>) -> PyResult<()> {
        self.ensure_open()?;
        let path = self.path.clone();
        let branch_id = self.branch_id.clone();
        self.indexed = Some(
            py.detach(|| pact::PactFileReadHandle::open(path, branch_id.as_deref()))
                .map_err(pact_err_to_py)?,
        );
        Ok(())
    }

    fn close(&mut self) {
        self.indexed = None;
    }

    #[getter]
    fn closed(&self) -> bool {
        self.indexed.is_none()
    }

    fn __enter__(slf: PyRefMut<'_, Self>) -> PyRefMut<'_, Self> {
        slf
    }

    fn __exit__(
        &mut self,
        _exc_type: &Bound<'_, PyAny>,
        _exc: &Bound<'_, PyAny>,
        _tb: &Bound<'_, PyAny>,
    ) {
        self.close();
    }
}

impl PyReadHandle {
    fn ensure_open(&self) -> PyResult<()> {
        if self.indexed.is_none() {
            Err(PactError::new_err("read handle is closed"))
        } else {
            Ok(())
        }
    }

    fn indexed(&self) -> PyResult<&pact::PactFileReadHandle> {
        self.indexed
            .as_ref()
            .ok_or_else(|| PactError::new_err("read handle is closed"))
    }
}

#[pymethods]
impl PyContextTree {
    #[new]
    #[pyo3(signature = (context_tree_id=None, path=None))]
    fn new(context_tree_id: Option<String>, path: Option<String>) -> PyResult<Self> {
        let inner = if let Some(path) = path {
            pact::ContextTree::open_file(std::path::PathBuf::from(path), None)
                .map_err(pact_err_to_py)?
        } else if let Some(context_tree_id) = context_tree_id {
            pact::ContextTree::with_id(context_tree_id).map_err(pact_err_to_py)?
        } else {
            pact::ContextTree::new().map_err(pact_err_to_py)?
        };
        Ok(Self { inner })
    }

    #[staticmethod]
    fn with_id(context_tree_id: String) -> PyResult<Self> {
        let inner = pact::ContextTree::with_id(context_tree_id).map_err(pact_err_to_py)?;
        Ok(Self { inner })
    }

    #[staticmethod]
    fn open_file(path: String) -> PyResult<Self> {
        let inner = pact::ContextTree::open_file(std::path::PathBuf::from(path), None)
            .map_err(pact_err_to_py)?;
        Ok(Self { inner })
    }

    #[getter]
    fn id(&self) -> String {
        self.inner.id().to_string()
    }

    fn save_file(&mut self, path: String) -> PyResult<()> {
        self.inner
            .save_file(std::path::PathBuf::from(path))
            .map_err(pact_err_to_py)
    }

    #[pyo3(signature = (source_path, output_path=None, mode=None, revision=None))]
    fn fork(
        &mut self,
        source_path: String,
        output_path: Option<String>,
        mode: Option<String>,
        revision: Option<u64>,
    ) -> PyResult<String> {
        let mode = mode.as_deref().unwrap_or("hard");

        self.inner
            .save_file(std::path::PathBuf::from(&source_path))
            .map_err(pact_err_to_py)?;
        let branch_id = self.inner.branch_id().to_string();
        pact_file_fork_json(
            &source_path,
            output_path.as_deref(),
            mode,
            Some(&branch_id),
            revision,
            1024,
        )
        .map_err(pact_err_to_py)
    }

    fn _adopt_reference_fork(&mut self, source: PyRef<'_, PyContextTree>) -> PyResult<String> {
        let forked = source.inner.fork_reference().map_err(pact_err_to_py)?;
        let branch_id = forked.branch_id().to_string();
        self.inner = forked;
        Ok(branch_id)
    }

    fn insert(
        &mut self,
        py: Python<'_>,
        component: &Bound<'_, PyAny>,
        placement: &Bound<'_, PyAny>,
    ) -> PyResult<Py<PyAny>> {
        let component = depythonize_component(component, "insert component")?;
        let placement = placement_arg(placement)?;
        let trace = self
            .inner
            .insert(component, placement)
            .map_err(pact_err_to_py)?;
        to_py_object(py, &trace)
    }

    fn append(
        &mut self,
        py: Python<'_>,
        component: &Bound<'_, PyAny>,
        placement: &Bound<'_, PyAny>,
    ) -> PyResult<Py<PyAny>> {
        let component = depythonize_component(component, "append component")?;
        let placement = placement_arg(placement)?;
        let trace = self
            .inner
            .append(component, placement)
            .map_err(pact_err_to_py)?;
        to_py_object(py, &trace)
    }

    #[pyo3(signature = (component_id, content=None, fields=None, aux=None))]
    fn update(
        &mut self,
        py: Python<'_>,
        component_id: String,
        content: Option<String>,
        fields: Option<&Bound<'_, PyAny>>,
        aux: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let fields = depythonize_str_value_map(fields, "fields")?;
        let aux = depythonize_str_value_map(aux, "aux")?;

        let changes = pact::ComponentChanges {
            content,
            content_ref: None,
            fields,
            aux,
        };
        let trace = self
            .inner
            .update(&component_id, changes)
            .map_err(pact_err_to_py)?;
        to_py_object(py, &trace)
    }

    fn replace(
        &mut self,
        py: Python<'_>,
        component: &Bound<'_, PyAny>,
        component_id: String,
    ) -> PyResult<Py<PyAny>> {
        let component = depythonize_component(component, "replace component")?;
        let trace = self
            .inner
            .replace(component, &component_id)
            .map_err(pact_err_to_py)?;
        to_py_object(py, &trace)
    }

    #[pyo3(signature = (component, component_id=None, placement=None))]
    fn upsert(
        &mut self,
        py: Python<'_>,
        component: &Bound<'_, PyAny>,
        component_id: Option<String>,
        placement: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let component = depythonize_component(component, "upsert component")?;
        let placement = match placement {
            Some(placement) => placement_arg(placement)?,
            None => pact::Placement::from(""),
        };
        let trace = self
            .inner
            .upsert(component, component_id.as_deref(), placement)
            .map_err(pact_err_to_py)?;
        to_py_object(py, &trace)
    }

    fn prune(&mut self, py: Python<'_>, target: String) -> PyResult<Py<PyAny>> {
        let trace = self.inner.prune(&target).map_err(pact_err_to_py)?;
        to_py_object(py, &trace)
    }

    #[pyo3(signature = (target, destination, mode=None, offset=None))]
    fn move_component(
        &mut self,
        py: Python<'_>,
        target: String,
        destination: String,
        mode: Option<String>,
        offset: Option<i32>,
    ) -> PyResult<Py<PyAny>> {
        let trace = self
            .inner
            .move_component(
                target.as_str(),
                destination.as_str(),
                mode.as_deref(),
                offset,
            )
            .map_err(pact_err_to_py)?;
        to_py_object(py, &trace)
    }

    #[pyo3(signature = (role, parts, aux=None))]
    fn add_message(
        &mut self,
        py: Python<'_>,
        role: String,
        parts: &Bound<'_, PyAny>,
        aux: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let parts = depythonize_components(parts, "message parts")?;
        let aux = depythonize_str_value_map(aux, "message aux")?;
        let outcome = self
            .inner
            .add_message(&role, parts, aux)
            .map_err(pact_err_to_py)?;
        to_py_object(py, &outcome)
    }

    #[pyo3(signature = (parts=None, aux=None))]
    fn receive(
        &mut self,
        py: Python<'_>,
        parts: Option<&Bound<'_, PyAny>>,
        aux: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let aux = depythonize_str_value_map(aux, "provider response aux")?;
        let report = match parts {
            Some(parts) => {
                let parts = depythonize_components(parts, "provider response parts")?;
                self.inner.receive(parts, aux)
            }
            None => self.inner.receive_staged(aux),
        }
        .map_err(pact_err_to_py)?;
        to_py_object(py, &report)
    }

    fn unpaired_tool_call_ids(&self) -> PyResult<Vec<String>> {
        Ok(self.inner.unpaired_tool_call_ids())
    }

    fn preview(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match self
            .inner
            .project(pact::ProjectionRequest::Json {
                mode: pact::ViewMode::Current,
            })
            .map_err(pact_err_to_py)?
        {
            pact::ProjectionOutput::Json(text) => {
                let value: serde_json::Value = serde_json::from_str(&text).map_err(|err| {
                    PyValueError::new_err(format!("failed to decode ContextTree preview: {err}"))
                })?;
                to_py_object(py, &value)
            }
            _ => Err(PactError::new_err(
                "ContextTree preview returned non-json output",
            )),
        }
    }

    fn preview_view(&self, py: Python<'_>) -> PyResult<Py<PyPactView>> {
        let view = self.inner.preview().map_err(pact_err_to_py)?;
        Py::new(py, PyPactView::from_view(view))
    }

    fn preview_view_json(&self) -> PyResult<String> {
        serde_json::to_string(&self.inner.preview().map_err(pact_err_to_py)?)
            .map_err(|err| PyValueError::new_err(format!("failed to encode PactView: {err}")))
    }

    #[pyo3(signature = (scope=None))]
    fn project_xml(&self, scope: Option<String>) -> PyResult<String> {
        let mode = match scope.as_deref() {
            None | Some("current") => pact::ViewMode::Current,
            Some("archive") => pact::ViewMode::Archive,
            Some(other) => {
                return Err(PactError::new_err(format!(
                    "unknown projection scope {other:?}; use 'current' or 'archive'"
                )))
            }
        };
        match self.inner.project(pact::ProjectionRequest::Xml { mode }) {
            Ok(pact::ProjectionOutput::Xml(xml)) => Ok(xml),
            Ok(_) => Err(PactError::new_err(
                "xml projection returned wrong output kind",
            )),
            Err(err) => Err(pact_err_to_py(err)),
        }
    }

    #[pyo3(signature = (selector, include_archived=false))]
    fn select(
        &self,
        py: Python<'_>,
        selector: String,
        include_archived: bool,
    ) -> PyResult<Py<PyAny>> {
        let selected = self
            .inner
            .select(&selector, include_archived)
            .map_err(pact_err_to_py)?;
        let out = PyList::empty(py);
        for selected_component in selected {
            out.append(selected_component_to_py(py, selected_component)?)?;
        }
        Ok(out.unbind().into())
    }

    fn discard_staged(&mut self) -> PyResult<bool> {
        self.inner.discard_staged().map_err(pact_err_to_py)
    }

    fn staged_traces(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let traces = self.inner.staged_traces().map_err(pact_err_to_py)?;
        to_py_object(py, &traces)
    }

    fn stage_extensions(&mut self, records: &Bound<'_, PyAny>) -> PyResult<()> {
        let records =
            pythonize::depythonize::<Vec<pact::PactExtensionRecord>>(records).map_err(|error| {
                py_invalid_input(format!("invalid Pact extension records: {error}"))
            })?;
        self.inner.stage_extensions(records).map_err(pact_err_to_py)
    }

    fn discard_staged_trace(&mut self, trace_id: String) -> PyResult<bool> {
        self.inner.discard_trace(&trace_id).map_err(pact_err_to_py)
    }

    fn has_staged(&self) -> PyResult<bool> {
        Ok(self.inner.has_staged())
    }

    #[pyo3(signature = (boundary=None))]
    fn seal_staged(&mut self, py: Python<'_>, boundary: Option<String>) -> PyResult<Py<PyAny>> {
        let boundary = parse_boundary_kind(boundary.as_deref().unwrap_or("adhoc"))?;
        let commit = self.inner.seal_staged(boundary).map_err(pact_err_to_py)?;
        to_py_object(py, &commit)
    }

    fn message_count(&self) -> PyResult<usize> {
        let view = self.inner.preview().map_err(pact_err_to_py)?;
        Ok(view
            .message_order
            .iter()
            .filter(|message_id| message_id.as_str() != "active")
            .count())
    }

    #[pyo3(signature = (include_partial_tool_calls=false, fail_on_orphan_tool_calls=true))]
    fn build_provider_thread(
        &self,
        py: Python<'_>,
        include_partial_tool_calls: bool,
        fail_on_orphan_tool_calls: bool,
    ) -> PyResult<Py<PyAny>> {
        let opts = pact::ProviderThreadOptions {
            include_partial_tool_calls,
            fail_on_orphan_tool_calls,
        };
        match self
            .inner
            .project(pact::ProjectionRequest::ProviderThread { options: opts })
        {
            Ok(pact::ProjectionOutput::ProviderThread(thread)) => to_py_object(py, &thread),
            Ok(_) => Err(PactError::new_err(
                "provider projection returned wrong output kind",
            )),
            Err(err) => Err(pact_err_to_py(err)),
        }
    }

    #[pyo3(signature = (include_partial_tool_calls=false, fail_on_orphan_tool_calls=true))]
    fn send(
        &mut self,
        py: Python<'_>,
        include_partial_tool_calls: bool,
        fail_on_orphan_tool_calls: bool,
    ) -> PyResult<Py<PyAny>> {
        let opts = pact::ProviderThreadOptions {
            include_partial_tool_calls,
            fail_on_orphan_tool_calls,
        };
        let thread = self.inner.send(&opts).map_err(pact_err_to_py)?;
        to_py_object(py, &thread)
    }
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PyContextTree>()?;
    module.add_class::<PyReadHandle>()?;
    module.add_function(wrap_pyfunction!(open_handle, module)?)?;
    Ok(())
}

#[pyfunction(name = "open_handle")]
#[pyo3(signature = (path, branch_id=None))]
fn open_handle(py: Python<'_>, path: String, branch_id: Option<String>) -> PyResult<PyReadHandle> {
    PyReadHandle::open(py, path, branch_id)
}

fn parse_boundary_kind(value: &str) -> PyResult<pact::trace::BoundaryKind> {
    let normalized = value.trim();
    serde_json::from_value(serde_json::Value::String(normalized.to_string()))
        .map_err(|err| py_invalid_input(format!("invalid boundary kind {value:?}: {err}")))
}
