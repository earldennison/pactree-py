use pyo3::prelude::*;
use serde_json::Value;

use crate::bindings::convert::to_py_object;
use crate::bindings::errors::PactError;

#[derive(Clone)]
enum ComponentViewInner {
    Component(pact::components::Component),
    Selected(pact::selector::SelectedComponent),
}

impl ComponentViewInner {
    fn id(&self) -> &str {
        match self {
            Self::Component(component) => &component.id,
            Self::Selected(component) => &component.id,
        }
    }

    fn component_type(&self) -> &str {
        match self {
            Self::Component(component) => component.component_type(),
            Self::Selected(component) => &component.component_type,
        }
    }

    fn kind(&self) -> Option<String> {
        match self {
            Self::Component(component) => component.subtype.clone(),
            Self::Selected(component) => component.kind.clone(),
        }
    }

    fn role(&self) -> Option<String> {
        match self {
            Self::Component(component) => component.role.clone(),
            Self::Selected(component) => component.role.clone(),
        }
    }

    fn parent_id(&self) -> Option<String> {
        match self {
            Self::Component(component) => component.parent_id.clone(),
            Self::Selected(component) => component.parent_id.clone(),
        }
    }

    fn offset(&self) -> i64 {
        match self {
            Self::Component(component) => i64::from(component.offset),
            Self::Selected(component) => i64::from(component.offset),
        }
    }

    fn pact_depth(&self) -> Option<i64> {
        match self {
            Self::Component(component) => component.pact_depth.map(i64::from),
            Self::Selected(component) => component.pact_depth.map(i64::from),
        }
    }

    fn ttl(&self) -> Option<i64> {
        match self {
            Self::Component(component) => component.lifecycle.policy.ttl,
            Self::Selected(component) => component.ttl,
        }
    }

    fn cad(&self) -> Option<i64> {
        match self {
            Self::Component(component) => component.lifecycle.policy.cad,
            Self::Selected(component) => component.cad,
        }
    }

    fn key(&self) -> Option<String> {
        match self {
            Self::Component(component) => component.key.clone(),
            Self::Selected(component) => component.key.clone(),
        }
    }

    fn tags(&self) -> Vec<String> {
        match self {
            Self::Component(component) => component.tags.clone(),
            Self::Selected(component) => component.tags.clone(),
        }
    }

    fn child_ids(&self) -> Vec<String> {
        match self {
            Self::Component(component) => component
                .children
                .iter()
                .map(|child| child.id.clone())
                .collect(),
            Self::Selected(_) => Vec::new(),
        }
    }

    fn aux_value(&self) -> Value {
        match self {
            Self::Component(component) => {
                Value::Object(component.aux.clone().into_iter().collect())
            }
            Self::Selected(component) => Value::Object(component.aux.clone()),
        }
    }

    fn aux_string(&self, key: &str) -> Option<String> {
        self.aux_value()
            .get(key)
            .and_then(Value::as_str)
            .map(str::to_string)
    }

    fn aux_field(&self, key: &str) -> Option<Value> {
        self.aux_value().get(key).cloned()
    }

    fn content(&self) -> Option<String> {
        match self {
            Self::Component(component) => component.content().map(str::to_string),
            Self::Selected(component) => component.content.clone(),
        }
    }

    fn content_status(&self) -> Option<String> {
        match self {
            Self::Component(component) => component.content_status().map(str::to_string),
            Self::Selected(component) => component
                .aux
                .get("content_status")
                .and_then(Value::as_str)
                .map(str::to_string),
        }
    }

    fn content_preview(&self) -> Option<String> {
        self.aux_string("content_preview")
            .or_else(|| self.aux_string("preview"))
    }

    fn tool_call_id(&self) -> Option<String> {
        match self {
            Self::Component(component) => component.tool_call_id().map(str::to_string),
            Self::Selected(component) => component.tool_call_id.clone(),
        }
    }

    fn tool_name(&self) -> Option<String> {
        match self {
            Self::Component(component) => component.tool_name().map(str::to_string),
            Self::Selected(component) => component.tool_name.clone(),
        }
    }

    fn parameters(&self) -> Option<Value> {
        match self {
            Self::Component(component) => component.parameters().cloned(),
            Self::Selected(component) => component.parameters.clone(),
        }
    }

    fn success(&self) -> Option<bool> {
        match self {
            Self::Component(component) => component.success(),
            Self::Selected(component) => component.success,
        }
    }

    fn result(&self) -> Option<Value> {
        match self {
            Self::Component(component) => component.result().cloned(),
            Self::Selected(component) => component.result.clone(),
        }
    }

    fn error_message(&self) -> Option<String> {
        match self {
            Self::Component(component) => component.error_message().map(str::to_string),
            Self::Selected(component) => component.error_message.clone(),
        }
    }

    fn source_ref(&self) -> Option<Value> {
        match self {
            Self::Component(component) => component
                .source_ref()
                .and_then(|source_ref| serde_json::to_value(source_ref).ok()),
            Self::Selected(component) => component.source_ref.clone(),
        }
    }

    fn content_capture(&self) -> Option<Value> {
        match self {
            Self::Component(component) => component
                .content_capture()
                .and_then(|capture| serde_json::to_value(capture).ok()),
            Self::Selected(component) => component.aux.get("content_capture").cloned(),
        }
    }

    fn to_value(&self) -> Value {
        match self {
            Self::Component(component) => component.project_component_value(),
            Self::Selected(component) => serde_json::to_value(component).unwrap_or(Value::Null),
        }
    }
}

#[pyclass(subclass, module = "pactree.pactree_engine", name = "ComponentView")]
#[derive(Clone)]
pub(crate) struct PyComponentView {
    inner: ComponentViewInner,
}

impl PyComponentView {
    fn from_component(component: pact::components::Component) -> Self {
        Self {
            inner: ComponentViewInner::Component(component),
        }
    }

    fn from_selected(component: pact::selector::SelectedComponent) -> Self {
        Self {
            inner: ComponentViewInner::Selected(component),
        }
    }
}

#[pymethods]
impl PyComponentView {
    #[getter]
    fn id(&self) -> String {
        self.inner.id().to_string()
    }

    #[getter]
    fn node_type(&self) -> String {
        self.inner.component_type().to_string()
    }

    #[getter]
    fn component_type(&self) -> String {
        self.inner.component_type().to_string()
    }

    #[getter]
    fn kind(&self) -> Option<String> {
        self.inner.kind()
    }

    #[getter]
    fn role(&self) -> Option<String> {
        self.inner.role()
    }

    #[getter]
    fn parent_id(&self) -> Option<String> {
        self.inner.parent_id()
    }

    #[getter]
    fn offset(&self) -> i64 {
        self.inner.offset()
    }

    #[getter]
    fn pact_depth(&self) -> Option<i64> {
        self.inner.pact_depth()
    }

    #[getter]
    fn ttl(&self) -> Option<i64> {
        self.inner.ttl()
    }

    #[getter]
    fn cad(&self) -> Option<i64> {
        self.inner.cad()
    }

    #[getter]
    fn key(&self) -> Option<String> {
        self.inner.key()
    }

    #[getter]
    fn tags(&self) -> Vec<String> {
        self.inner.tags()
    }

    #[getter]
    fn child_ids(&self) -> Vec<String> {
        self.inner.child_ids()
    }

    #[getter]
    fn attrs(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        to_py_object(py, &self.inner.aux_value())
    }

    #[getter]
    fn aux(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        to_py_object(py, &self.inner.aux_value())
    }

    #[getter]
    fn content(&self) -> Option<String> {
        self.inner.content()
    }

    #[getter]
    fn content_preview(&self) -> Option<String> {
        self.inner.content_preview()
    }

    #[getter]
    fn content_status(&self) -> Option<String> {
        self.inner.content_status()
    }

    #[getter]
    fn tool_call_id(&self) -> Option<String> {
        self.inner.tool_call_id()
    }

    #[getter]
    fn tool_name(&self) -> Option<String> {
        self.inner.tool_name()
    }

    #[getter]
    fn parameters(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        opt_to_py(py, self.inner.parameters())
    }

    #[getter]
    fn success(&self) -> Option<bool> {
        self.inner.success()
    }

    #[getter]
    fn result(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        opt_to_py(py, self.inner.result())
    }

    #[getter]
    fn error_message(&self) -> Option<String> {
        self.inner.error_message()
    }

    #[getter]
    fn source_ref(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        opt_to_py(py, self.inner.source_ref())
    }

    #[getter]
    fn content_capture(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        opt_to_py(py, self.inner.content_capture())
    }

    #[getter]
    fn visibility(&self) -> Option<String> {
        self.inner.aux_string("visibility")
    }

    #[getter]
    fn encoding(&self) -> Option<String> {
        self.inner.aux_string("encoding")
    }

    #[getter]
    fn data(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        opt_to_py(py, self.inner.aux_field("data"))
    }

    #[getter]
    fn media_content(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        opt_to_py(
            py,
            self.inner
                .aux_field("media_content")
                .or_else(|| self.inner.aux_field("media")),
        )
    }

    #[getter]
    fn mime_type(&self) -> Option<String> {
        self.inner.aux_string("mime_type")
    }

    #[getter]
    fn label(&self) -> Option<String> {
        self.inner.aux_string("label")
    }

    fn to_dict(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        to_py_object(py, &self.inner.to_value())
    }

    fn to_json(&self) -> PyResult<String> {
        serde_json::to_string(&self.inner.to_value()).map_err(|error| {
            PactError::new_err(format!("failed to encode component view: {error}"))
        })
    }

    fn __getattr__(&self, py: Python<'_>, name: String) -> PyResult<Py<PyAny>> {
        if let Some(value) = self.inner.aux_field(&name) {
            return to_py_object(py, &value);
        }
        Err(pyo3::exceptions::PyAttributeError::new_err(name))
    }

    fn __repr__(&self) -> String {
        format!(
            "ComponentView(id={:?}, component_type={:?})",
            self.inner.id(),
            self.inner.component_type()
        )
    }
}

#[pyclass(extends = PyComponentView, module = "pactree.pactree_engine", name = "ContainerComponentView")]
pub(crate) struct PyContainerComponentView;

#[pyclass(extends = PyComponentView, module = "pactree.pactree_engine", name = "MessageTurnView")]
pub(crate) struct PyMessageTurnView;

#[pyclass(extends = PyComponentView, module = "pactree.pactree_engine", name = "TextContentView")]
pub(crate) struct PyTextContentView;

#[pyclass(extends = PyComponentView, module = "pactree.pactree_engine", name = "ReasoningContentView")]
pub(crate) struct PyReasoningContentView;

#[pyclass(extends = PyComponentView, module = "pactree.pactree_engine", name = "MediaContentView")]
pub(crate) struct PyMediaContentView;

#[pyclass(extends = PyComponentView, module = "pactree.pactree_engine", name = "ToolCallView")]
pub(crate) struct PyToolCallView;

#[pyclass(extends = PyComponentView, module = "pactree.pactree_engine", name = "ToolResultView")]
pub(crate) struct PyToolResultView;

#[pyclass(extends = PyComponentView, module = "pactree.pactree_engine", name = "CapturedContentView")]
pub(crate) struct PyCapturedContentView;

#[pyclass(extends = PyComponentView, module = "pactree.pactree_engine", name = "UnknownComponentView")]
pub(crate) struct PyUnknownComponentView;

pub(crate) fn component_view_to_py(
    py: Python<'_>,
    component: pact::components::Component,
) -> PyResult<Py<PyAny>> {
    component_view_inner_to_py(py, PyComponentView::from_component(component))
}

pub(crate) fn selected_component_to_py(
    py: Python<'_>,
    component: pact::selector::SelectedComponent,
) -> PyResult<Py<PyAny>> {
    component_view_inner_to_py(py, PyComponentView::from_selected(component))
}

fn component_view_inner_to_py(py: Python<'_>, base: PyComponentView) -> PyResult<Py<PyAny>> {
    let object = match base.inner.component_type() {
        "text_content" => Py::new(
            py,
            PyClassInitializer::from(base).add_subclass(PyTextContentView),
        )?
        .into_any(),
        "reasoning_content" => Py::new(
            py,
            PyClassInitializer::from(base).add_subclass(PyReasoningContentView),
        )?
        .into_any(),
        "image_content" | "audio_content" | "video_content" | "document_content" => Py::new(
            py,
            PyClassInitializer::from(base).add_subclass(PyMediaContentView),
        )?
        .into_any(),
        "captured_content" => Py::new(
            py,
            PyClassInitializer::from(base).add_subclass(PyCapturedContentView),
        )?
        .into_any(),
        "tool_call" => Py::new(
            py,
            PyClassInitializer::from(base).add_subclass(PyToolCallView),
        )?
        .into_any(),
        "tool_result" => Py::new(
            py,
            PyClassInitializer::from(base).add_subclass(PyToolResultView),
        )?
        .into_any(),
        "message_turn" => Py::new(
            py,
            PyClassInitializer::from(base).add_subclass(PyMessageTurnView),
        )?
        .into_any(),
        "root" | "system_header" | "message_container" | "conversation_history" | "container" => {
            Py::new(
                py,
                PyClassInitializer::from(base).add_subclass(PyContainerComponentView),
            )?
            .into_any()
        }
        _ => Py::new(
            py,
            PyClassInitializer::from(base).add_subclass(PyUnknownComponentView),
        )?
        .into_any(),
    };
    Ok(object)
}

fn opt_to_py(py: Python<'_>, value: Option<Value>) -> PyResult<Py<PyAny>> {
    match value {
        Some(value) => to_py_object(py, &value),
        None => Ok(py.None()),
    }
}

#[pyclass(module = "pactree.pactree_engine", name = "PactView")]
#[derive(Clone)]
pub(crate) struct PyPactView {
    inner: pact::PactView,
}

impl PyPactView {
    pub(crate) fn from_view(view: pact::PactView) -> Self {
        Self { inner: view }
    }
}

#[pymethods]
impl PyPactView {
    #[getter]
    fn message_order(&self) -> Vec<String> {
        self.inner.message_order.clone()
    }

    #[getter]
    fn nodes(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let dict = pyo3::types::PyDict::new(py);
        for component_id in self.inner.tree.index.by_id.keys() {
            if let Some(component) = component_by_id(&self.inner.tree.root, component_id) {
                dict.set_item(component_id, component_view_to_py(py, component.clone())?)?;
            }
        }
        Ok(dict.unbind().into())
    }

    #[getter]
    fn first_seen(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        to_py_object(py, &self.inner.tree.first_seen)
    }

    #[getter]
    fn lifecycle_seen(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        to_py_object(py, &self.inner.lifecycle_seen)
    }

    fn to_dict(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        to_py_object(py, &self.inner)
    }

    fn to_json(&self) -> PyResult<String> {
        serde_json::to_string(&self.inner)
            .map_err(|error| PactError::new_err(format!("failed to encode view: {error}")))
    }
}

fn component_by_id<'a>(
    component: &'a pact::components::Component,
    component_id: &str,
) -> Option<&'a pact::components::Component> {
    if component.id == component_id {
        return Some(component);
    }
    component
        .children
        .iter()
        .find_map(|child| component_by_id(child, component_id))
}

#[pyclass(module = "pactree.pactree_engine", name = "ProjectionRange")]
pub(crate) struct PyProjectionRange {
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    start: u64,
    #[pyo3(get)]
    end: u64,
}

#[pyclass(module = "pactree.pactree_engine", name = "PactFileProjection")]
pub(crate) struct PyPactFileProjection {
    #[pyo3(get)]
    schema: String,
    #[pyo3(get)]
    scope: String,
    tree: pact::PactView,
    range: Option<(String, u64, u64)>,
}

impl PyPactFileProjection {
    pub(crate) fn from_projection(projection: &pact::file_format::PactFileProjection) -> Self {
        Self {
            schema: projection.schema.clone(),
            scope: projection.scope.clone(),
            tree: projection.branch.clone(),
            range: projection
                .range
                .as_ref()
                .map(|r| (r.kind.clone(), r.start, r.end)),
        }
    }
}

#[pymethods]
impl PyPactFileProjection {
    #[getter]
    fn tree(&self, py: Python<'_>) -> PyResult<Py<PyPactView>> {
        Py::new(py, PyPactView::from_view(self.tree.clone()))
    }

    #[getter]
    fn components(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        to_py_object(py, &Vec::<Value>::new())
    }

    #[getter]
    fn component_transactions(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        to_py_object(py, &Vec::<Value>::new())
    }

    #[getter]
    fn range(&self, py: Python<'_>) -> PyResult<Option<Py<PyProjectionRange>>> {
        match &self.range {
            Some((kind, start, end)) => Ok(Some(Py::new(
                py,
                PyProjectionRange {
                    kind: kind.clone(),
                    start: *start,
                    end: *end,
                },
            )?)),
            None => Ok(None),
        }
    }

    fn to_dict(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        to_py_object(py, &self.to_value())
    }

    fn to_json(&self) -> PyResult<String> {
        serde_json::to_string(&self.to_value()).map_err(|error| {
            PactError::new_err(format!("failed to encode file projection: {error}"))
        })
    }
}

impl PyPactFileProjection {
    fn to_value(&self) -> Value {
        serde_json::json!({
            "schema": self.schema,
            "scope": self.scope,
            "tree": self.tree,
            "components": Vec::<Value>::new(),
            "component_transactions": Vec::<Value>::new(),
            "range": self.range.as_ref().map(|(kind, start, end)| serde_json::json!({
                "kind": kind,
                "start": start,
                "end": end,
            })),
        })
    }
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PyComponentView>()?;
    module.add_class::<PyContainerComponentView>()?;
    module.add_class::<PyMessageTurnView>()?;
    module.add_class::<PyTextContentView>()?;
    module.add_class::<PyReasoningContentView>()?;
    module.add_class::<PyMediaContentView>()?;
    module.add_class::<PyToolCallView>()?;
    module.add_class::<PyToolResultView>()?;
    module.add_class::<PyCapturedContentView>()?;
    module.add_class::<PyUnknownComponentView>()?;
    module.add_class::<PyPactView>()?;
    module.add_class::<PyProjectionRange>()?;
    module.add_class::<PyPactFileProjection>()?;
    Ok(())
}
