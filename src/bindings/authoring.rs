use pyo3::prelude::*;
use pyo3::types::PyDict;
use serde_json::{Map, Value};

use crate::bindings::convert::{
    component_from_value, depythonize_component, split_xml_content, to_py_object,
};
use crate::bindings::errors::py_invalid_input;

fn depythonize_value(obj: &Bound<'_, PyAny>, label: &str) -> PyResult<Value> {
    pythonize::depythonize(obj).map_err(|err| py_invalid_input(format!("invalid {label}: {err}")))
}

fn depythonize_object(obj: Option<&Bound<'_, PyAny>>, label: &str) -> PyResult<Map<String, Value>> {
    match obj {
        None => Ok(Map::new()),
        Some(obj) => match depythonize_value(obj, label)? {
            Value::Object(map) => Ok(map),
            Value::Null => Ok(Map::new()),
            _ => Err(py_invalid_input(format!("{label} must be a mapping"))),
        },
    }
}

fn external_ref_from_value(mut value: Value) -> PyResult<pact::ExternalRef> {
    let object = value
        .as_object_mut()
        .ok_or_else(|| py_invalid_input("external_refs entries must be mappings".to_string()))?;
    object
        .entry("kind".to_string())
        .or_insert_with(|| Value::String("external".to_string()));
    serde_json::from_value(value)
        .map_err(|err| py_invalid_input(format!("invalid external_refs entry: {err}")))
}

fn depythonize_external_refs(obj: Option<&Bound<'_, PyAny>>) -> PyResult<Vec<pact::ExternalRef>> {
    let Some(obj) = obj else {
        return Ok(Vec::new());
    };
    match depythonize_value(obj, "external_refs")? {
        Value::Null => Ok(Vec::new()),
        Value::Array(items) => items.into_iter().map(external_ref_from_value).collect(),
        _ => Err(py_invalid_input(
            "external_refs must be a sequence".to_string(),
        )),
    }
}

fn media_kind_from_str(kind: &str) -> pact::MediaKind {
    pact::MediaKind::from_kind_str(Some(kind))
}

fn media_payload(
    media_kind: pact::MediaKind,
    body: pact::MediaBody,
    mime_type: Option<String>,
    label: Option<String>,
    aux: Option<&Bound<'_, PyAny>>,
) -> PyResult<pact::MediaPayload> {
    Ok(pact::MediaPayload {
        media_kind,
        mime_type,
        label,
        body,
        aux: depythonize_object(aux, "aux")?.into_iter().collect(),
        ..pact::MediaPayload::default()
    })
}

#[allow(clippy::too_many_arguments)]
fn media_component(
    media_kind: pact::MediaKind,
    body: pact::MediaBody,
    mime_type: Option<String>,
    label: Option<String>,
    ttl: Option<i64>,
    cad: Option<i64>,
    key: Option<String>,
    tags: Option<Vec<String>>,
    aux: Option<&Bound<'_, PyAny>>,
    external_refs: Option<&Bound<'_, PyAny>>,
) -> PyResult<PyComponent> {
    let payload = media_payload(media_kind, body, mime_type, label, aux)?;
    let options = authoring_options(Map::new(), ttl, cad, key, tags, None, external_refs)?;
    Ok(PyComponent {
        inner: pact::Component::media_authoring(payload, options),
    })
}

fn media_body_from_parts(
    url: Option<String>,
    data: Option<Vec<u8>>,
    content_ref: Option<&Bound<'_, PyAny>>,
    external_ref: Option<&Bound<'_, PyAny>>,
) -> PyResult<pact::MediaBody> {
    let supplied = [
        url.is_some(),
        data.is_some(),
        content_ref.is_some(),
        external_ref.is_some(),
    ]
    .into_iter()
    .filter(|value| *value)
    .count();
    if supplied != 1 {
        return Err(py_invalid_input(
            "media content needs exactly one of url, data, content_ref, or external_ref"
                .to_string(),
        ));
    }
    if let Some(url) = url {
        Ok(pact::MediaBody::Url { url })
    } else if let Some(data) = data {
        Ok(pact::MediaBody::InlineBytes {
            bytes: data,
            encoding: None,
        })
    } else if let Some(content_ref) = content_ref {
        Ok(pact::MediaBody::ContentRef(
            serde_json::from_value::<pact::PactContentRef>(depythonize_value(
                content_ref,
                "content_ref",
            )?)
            .map_err(|err| py_invalid_input(format!("invalid content_ref: {err}")))?,
        ))
    } else {
        Ok(pact::MediaBody::ExternalRef(
            serde_json::from_value::<pact::ExternalRef>(depythonize_value(
                external_ref.expect("checked above"),
                "external_ref",
            )?)
            .map_err(|err| py_invalid_input(format!("invalid external_ref: {err}")))?,
        ))
    }
}

#[allow(clippy::too_many_arguments)]
fn authoring_options(
    mut extra_aux: Map<String, Value>,
    ttl: Option<i64>,
    cad: Option<i64>,
    key: Option<String>,
    tags: Option<Vec<String>>,
    aux: Option<&Bound<'_, PyAny>>,
    external_refs: Option<&Bound<'_, PyAny>>,
) -> PyResult<pact::AuthoringComponentOptions> {
    extra_aux.extend(depythonize_object(aux, "aux")?);
    Ok(pact::AuthoringComponentOptions {
        local_id: None,
        created_at_ns: None,
        ttl,
        cad,
        key,
        tags: tags.unwrap_or_default(),
        aux: extra_aux.into_iter().collect(),
        external_refs: depythonize_external_refs(external_refs)?,
    })
}

#[pyclass(subclass, module = "pactree.pactree_engine", name = "Component")]
#[derive(Clone)]
pub struct PyComponent {
    pub(crate) inner: pact::Component,
}

#[pymethods]
impl PyComponent {
    #[pyo3(signature = (**_kwargs))]
    fn model_dump(
        &self,
        py: Python<'_>,
        _kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        to_py_object(py, &self.inner.project_component_value())
    }

    fn to_dict(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        to_py_object(py, &self.inner.project_component_value())
    }

    #[pyo3(signature = (clean=true))]
    fn to_str(&self, clean: bool) -> String {
        if !clean {
            return pact::render_component_xml(&self.inner);
        }
        let mut out = String::new();
        render_clean_xml(&self.inner, &mut out);
        out
    }

    #[getter]
    fn id(&self) -> Option<String> {
        if self.inner.id.is_empty() {
            None
        } else {
            Some(self.inner.id.clone())
        }
    }

    #[getter]
    fn content(&self) -> Option<String> {
        self.inner.content().map(str::to_string).or_else(|| {
            self.inner
                .aux
                .get("content")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
    }

    #[setter]
    fn set_content(&mut self, content: Option<String>) {
        match &mut self.inner.kind {
            pact::ComponentKind::Text(payload) | pact::ComponentKind::Reasoning(payload) => {
                payload.content = content;
            }
            pact::ComponentKind::ToolResult(payload) => {
                payload.content = content;
            }
            pact::ComponentKind::Media(_) => {
                if let Some(content) = content {
                    self.inner
                        .aux
                        .insert("content".to_string(), Value::String(content));
                } else {
                    self.inner.aux.remove("content");
                }
            }
            pact::ComponentKind::Unknown(payload) => {
                if let Some(content) = content {
                    payload
                        .payload
                        .insert("content".to_string(), Value::String(content));
                } else {
                    payload.payload.remove("content");
                }
            }
            _ => {
                if let Some(content) = content {
                    self.inner
                        .aux
                        .insert("content".to_string(), Value::String(content));
                } else {
                    self.inner.aux.remove("content");
                }
            }
        }
    }

    fn append(&mut self, child: &Bound<'_, PyAny>) -> PyResult<()> {
        self.inner
            .children
            .push(depythonize_component(child, "append child")?);
        Ok(())
    }

    #[getter]
    fn component_type(&self) -> String {
        self.inner.kind.type_name().to_string()
    }

    #[getter]
    fn media_kind(&self) -> Option<String> {
        match &self.inner.kind {
            pact::ComponentKind::Media(payload) => Some(payload.media_kind.as_str().to_string()),
            _ => None,
        }
    }

    #[getter]
    fn mime_type(&self) -> Option<String> {
        match &self.inner.kind {
            pact::ComponentKind::Media(payload) => payload.mime_type.clone(),
            _ => None,
        }
    }

    #[getter]
    fn r#type(&self) -> String {
        self.inner.kind.type_name().to_string()
    }

    #[getter]
    fn ttl(&self) -> Option<i64> {
        self.inner.lifecycle.policy.ttl
    }

    #[setter]
    fn set_ttl(&mut self, ttl: Option<i64>) {
        self.inner.lifecycle.policy.ttl = ttl;
    }

    #[getter]
    fn cad(&self) -> Option<i64> {
        self.inner.lifecycle.policy.cad
    }

    #[setter]
    fn set_cad(&mut self, cad: Option<i64>) {
        self.inner.lifecycle.policy.cad = cad;
    }

    #[getter]
    fn key(&self) -> Option<String> {
        self.inner.key.clone()
    }

    #[setter]
    fn set_key(&mut self, key: Option<String>) {
        self.inner.key = key;
    }

    #[getter]
    fn tags(&self) -> Vec<String> {
        self.inner.tags.clone()
    }

    #[setter]
    fn set_tags(&mut self, tags: Vec<String>) {
        self.inner.tags = tags;
    }

    #[getter]
    fn aux(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        to_py_object(
            py,
            &Value::Object(self.inner.aux.clone().into_iter().collect()),
        )
    }

    #[setter]
    fn set_aux(&mut self, aux: &Bound<'_, PyAny>) -> PyResult<()> {
        self.inner.aux = depythonize_object(Some(aux), "aux")?.into_iter().collect();
        Ok(())
    }

    #[getter]
    fn attrs(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let attrs = match &self.inner.kind {
            pact::ComponentKind::Unknown(payload) => payload.attrs.clone(),
            _ => Default::default(),
        };
        to_py_object(py, &Value::Object(attrs.into_iter().collect()))
    }

    #[setter]
    fn set_attrs(&mut self, attrs: &Bound<'_, PyAny>) -> PyResult<()> {
        let attrs = depythonize_object(Some(attrs), "attrs")?
            .into_iter()
            .collect();
        if let pact::ComponentKind::Unknown(payload) = &mut self.inner.kind {
            payload.attrs = attrs;
        }
        Ok(())
    }

    #[getter]
    fn external_refs(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let value = serde_json::to_value(&self.inner.external_refs)
            .map_err(|err| py_invalid_input(format!("invalid external_refs projection: {err}")))?;
        to_py_object(py, &value)
    }

    #[setter]
    fn set_external_refs(&mut self, external_refs: Option<&Bound<'_, PyAny>>) -> PyResult<()> {
        self.inner.external_refs = depythonize_external_refs(external_refs)?;
        Ok(())
    }

    #[getter]
    fn tool_call_id(&self) -> Option<String> {
        self.inner.tool_call_id().map(str::to_string)
    }

    #[getter]
    fn tool_name(&self) -> Option<String> {
        self.inner.tool_name().map(str::to_string)
    }

    #[getter]
    fn parameters(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match self.inner.parameters() {
            Some(parameters) => to_py_object(py, parameters),
            None => Ok(py.None()),
        }
    }

    #[getter]
    fn success(&self) -> Option<bool> {
        self.inner.success()
    }

    #[getter]
    fn result(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match self.inner.result() {
            Some(result) => to_py_object(py, result),
            None => Ok(py.None()),
        }
    }

    #[getter]
    fn error_message(&self) -> Option<String> {
        self.inner.error_message().map(str::to_string)
    }

    fn __repr__(&self) -> String {
        format!(
            "{}({:?})",
            self.inner.kind.type_name(),
            self.inner.content().unwrap_or("")
        )
    }
}

fn xml_escape_text(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

fn xml_escape_attr(value: &str) -> String {
    xml_escape_text(value).replace('"', "&quot;")
}

fn aux_value_to_str(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::Bool(v) => v.to_string(),
        Value::Number(v) => v.to_string(),
        Value::String(v) => v.clone(),
        Value::Array(items) => items
            .iter()
            .map(aux_value_to_str)
            .collect::<Vec<_>>()
            .join(" "),
        other => other.to_string(),
    }
}

fn render_clean_xml(component: &pact::Component, out: &mut String) {
    let tag = component.component_type().to_string();
    out.push('<');
    out.push_str(&tag);

    if let Some(attrs) = component.xml_attrs() {
        for (key, value) in attrs {
            if key.starts_with('_') {
                continue;
            }
            out.push(' ');
            out.push_str(key);
            out.push_str("=\"");
            out.push_str(&xml_escape_attr(&aux_value_to_str(value)));
            out.push('"');
        }
    }
    let content = component.content().unwrap_or("");
    if content.is_empty() && component.children.is_empty() {
        out.push_str(" />");
        return;
    }
    out.push('>');
    if !content.is_empty() {
        out.push_str(&xml_escape_text(content));
    }
    for child in &component.children {
        render_clean_xml(child, out);
    }
    out.push_str("</");
    out.push_str(&tag);
    out.push('>');
}

#[pyclass(subclass, extends = PyComponent, module = "pactree.pactree_engine", name = "TextContent")]
pub struct PyTextContent;

#[pymethods]
impl PyTextContent {
    #[new]
    #[pyo3(signature = (content=None, *, ttl=None, cad=None, key=None, tags=None, aux=None, external_refs=None))]
    fn new(
        content: Option<String>,
        ttl: Option<i64>,
        cad: Option<i64>,
        key: Option<String>,
        tags: Option<Vec<String>>,
        aux: Option<&Bound<'_, PyAny>>,
        external_refs: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<(Self, PyComponent)> {
        let options = authoring_options(Map::new(), ttl, cad, key, tags, aux, external_refs)?;
        let inner = pact::Component::text_authoring(content.unwrap_or_default(), options);
        Ok((PyTextContent, PyComponent { inner }))
    }
}

#[pyclass(subclass, extends = PyComponent, module = "pactree.pactree_engine", name = "ReasoningContent")]
pub struct PyReasoningContent;

#[pymethods]
impl PyReasoningContent {
    #[new]
    #[pyo3(signature = (content=None, *, visibility=None, ttl=None, cad=None, key=None, tags=None, aux=None, external_refs=None))]
    fn new(
        content: Option<String>,
        visibility: Option<String>,
        ttl: Option<i64>,
        cad: Option<i64>,
        key: Option<String>,
        tags: Option<Vec<String>>,
        aux: Option<&Bound<'_, PyAny>>,
        external_refs: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<(Self, PyComponent)> {
        let mut extra_aux = Map::new();
        if let Some(visibility) = visibility {
            extra_aux.insert("visibility".to_string(), Value::String(visibility));
        }
        let options = authoring_options(extra_aux, ttl, cad, key, tags, aux, external_refs)?;
        let inner = pact::Component::reasoning_authoring(content.unwrap_or_default(), options);
        Ok((PyReasoningContent, PyComponent { inner }))
    }
}

#[pyclass(subclass, extends = PyComponent, module = "pactree.pactree_engine", name = "MediaContent")]
pub struct PyMediaContent;

#[pymethods]
impl PyMediaContent {
    #[new]
    #[pyo3(signature = (kind=String::from("image"), *, url=None, data=None, mime_type=None, label=None, content_ref=None, external_ref=None, ttl=None, cad=None, key=None, tags=None, aux=None, external_refs=None))]
    fn new(
        kind: String,
        url: Option<String>,
        data: Option<Vec<u8>>,
        mime_type: Option<String>,
        label: Option<String>,
        content_ref: Option<&Bound<'_, PyAny>>,
        external_ref: Option<&Bound<'_, PyAny>>,
        ttl: Option<i64>,
        cad: Option<i64>,
        key: Option<String>,
        tags: Option<Vec<String>>,
        aux: Option<&Bound<'_, PyAny>>,
        external_refs: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<(Self, PyComponent)> {
        Ok((
            PyMediaContent,
            media_component(
                media_kind_from_str(&kind),
                media_body_from_parts(url, data, content_ref, external_ref)?,
                mime_type,
                label,
                ttl,
                cad,
                key,
                tags,
                aux,
                external_refs,
            )?,
        ))
    }
}

#[pyclass(subclass, extends = PyComponent, module = "pactree.pactree_engine", name = "ImageContent")]
pub struct PyImageContent;

#[pymethods]
impl PyImageContent {
    #[new]
    #[pyo3(signature = (*, url=None, data=None, mime_type=None, label=None, content_ref=None, external_ref=None, ttl=None, cad=None, key=None, tags=None, aux=None, external_refs=None))]
    fn new(
        url: Option<String>,
        data: Option<Vec<u8>>,
        mime_type: Option<String>,
        label: Option<String>,
        content_ref: Option<&Bound<'_, PyAny>>,
        external_ref: Option<&Bound<'_, PyAny>>,
        ttl: Option<i64>,
        cad: Option<i64>,
        key: Option<String>,
        tags: Option<Vec<String>>,
        aux: Option<&Bound<'_, PyAny>>,
        external_refs: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<(Self, PyComponent)> {
        Ok((
            PyImageContent,
            media_component(
                pact::MediaKind::Image,
                media_body_from_parts(url, data, content_ref, external_ref)?,
                mime_type,
                label,
                ttl,
                cad,
                key,
                tags,
                aux,
                external_refs,
            )?,
        ))
    }
}

#[pyclass(subclass, extends = PyComponent, module = "pactree.pactree_engine", name = "AudioContent")]
pub struct PyAudioContent;

#[pymethods]
impl PyAudioContent {
    #[new]
    #[pyo3(signature = (*, url=None, data=None, mime_type=None, label=None, content_ref=None, external_ref=None, ttl=None, cad=None, key=None, tags=None, aux=None, external_refs=None))]
    fn new(
        url: Option<String>,
        data: Option<Vec<u8>>,
        mime_type: Option<String>,
        label: Option<String>,
        content_ref: Option<&Bound<'_, PyAny>>,
        external_ref: Option<&Bound<'_, PyAny>>,
        ttl: Option<i64>,
        cad: Option<i64>,
        key: Option<String>,
        tags: Option<Vec<String>>,
        aux: Option<&Bound<'_, PyAny>>,
        external_refs: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<(Self, PyComponent)> {
        Ok((
            PyAudioContent,
            media_component(
                pact::MediaKind::Audio,
                media_body_from_parts(url, data, content_ref, external_ref)?,
                mime_type,
                label,
                ttl,
                cad,
                key,
                tags,
                aux,
                external_refs,
            )?,
        ))
    }
}

#[pyclass(subclass, extends = PyComponent, module = "pactree.pactree_engine", name = "ToolCall")]
pub struct PyToolCall;

#[pymethods]
impl PyToolCall {
    #[new]
    #[pyo3(signature = (tool_call_id, tool_name, parameters=None, *, content=None, ttl=None, cad=None, key=None, tags=None, aux=None, external_refs=None))]
    fn new(
        tool_call_id: String,
        tool_name: String,
        parameters: Option<&Bound<'_, PyAny>>,
        content: Option<String>,
        ttl: Option<i64>,
        cad: Option<i64>,
        key: Option<String>,
        tags: Option<Vec<String>>,
        aux: Option<&Bound<'_, PyAny>>,
        external_refs: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<(Self, PyComponent)> {
        if let Some(content) = content {
            let mut extra_aux = depythonize_object(aux, "aux")?;
            extra_aux.insert("content".to_string(), Value::String(content));
            let options = pact::AuthoringComponentOptions {
                local_id: None,
                created_at_ns: None,
                ttl,
                cad,
                key,
                tags: tags.unwrap_or_default(),
                aux: extra_aux.into_iter().collect(),
                external_refs: depythonize_external_refs(external_refs)?,
            };
            let parameters = parameters
                .map(|obj| depythonize_value(obj, "parameters"))
                .transpose()?;
            let inner =
                pact::Component::tool_call_authoring(tool_call_id, tool_name, parameters, options);
            return Ok((PyToolCall, PyComponent { inner }));
        }
        let options = authoring_options(Map::new(), ttl, cad, key, tags, aux, external_refs)?;
        let parameters = parameters
            .map(|obj| depythonize_value(obj, "parameters"))
            .transpose()?;
        let inner =
            pact::Component::tool_call_authoring(tool_call_id, tool_name, parameters, options);
        Ok((PyToolCall, PyComponent { inner }))
    }
}

#[pyclass(subclass, extends = PyComponent, module = "pactree.pactree_engine", name = "ToolResult")]
pub struct PyToolResult;

#[pymethods]
impl PyToolResult {
    #[new]
    #[pyo3(signature = (tool_call_id, *, tool_name=None, success=None, result=None, error_message=None, content=None, ttl=None, cad=None, key=None, tags=None, aux=None, external_refs=None))]
    fn new(
        tool_call_id: String,
        tool_name: Option<String>,
        success: Option<bool>,
        result: Option<&Bound<'_, PyAny>>,
        error_message: Option<String>,
        content: Option<String>,
        ttl: Option<i64>,
        cad: Option<i64>,
        key: Option<String>,
        tags: Option<Vec<String>>,
        aux: Option<&Bound<'_, PyAny>>,
        external_refs: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<(Self, PyComponent)> {
        let mut result = result
            .map(|obj| depythonize_value(obj, "result"))
            .transpose()?;
        if let Some(content_text) = &content {
            if !content_text.is_empty() && result.is_none() {
                result = Some(Value::String(content_text.clone()));
            }
        }
        let options = authoring_options(Map::new(), ttl, cad, key, tags, aux, external_refs)?;
        let inner = pact::Component::tool_result_authoring(
            tool_call_id,
            tool_name,
            success,
            result,
            error_message,
            content,
            options,
        );
        Ok((PyToolResult, PyComponent { inner }))
    }
}

#[pyclass(subclass, extends = PyComponent, module = "pactree.pactree_engine", name = "SystemHeader")]
pub struct PySystemHeader;

#[pymethods]
impl PySystemHeader {
    #[new]
    #[pyo3(signature = (content=None, *, ttl=None, cad=None, key=None, tags=None, aux=None, external_refs=None))]
    fn new(
        content: Option<String>,
        ttl: Option<i64>,
        cad: Option<i64>,
        key: Option<String>,
        tags: Option<Vec<String>>,
        aux: Option<&Bound<'_, PyAny>>,
        external_refs: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<(Self, PyComponent)> {
        let mut extra_aux = Map::new();
        if let Some(content) = content.filter(|value| !value.is_empty()) {
            extra_aux.insert("content".to_string(), Value::String(content));
        }
        let options = authoring_options(extra_aux, ttl, cad, key, tags, aux, external_refs)?;
        let inner = pact::Component::system_header_authoring(options);
        Ok((PySystemHeader, PyComponent { inner }))
    }
}

#[pyclass(subclass, extends = PyComponent, module = "pactree.pactree_engine", name = "MessageTurn")]
pub struct PyMessageTurn;

#[pymethods]
impl PyMessageTurn {
    #[new]
    #[pyo3(signature = (content=None, *, role=None, ttl=None, cad=None, key=None, tags=None, aux=None, external_refs=None))]
    fn new(
        content: Option<&Bound<'_, PyAny>>,
        role: Option<String>,
        ttl: Option<i64>,
        cad: Option<i64>,
        key: Option<String>,
        tags: Option<Vec<String>>,
        aux: Option<&Bound<'_, PyAny>>,
        external_refs: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<(Self, PyComponent)> {
        let content = content
            .map(|obj| depythonize_value(obj, "content"))
            .transpose()?
            .filter(|value| !value.is_null());
        let options = authoring_options(Map::new(), ttl, cad, key, tags, aux, external_refs)?;
        let inner = pact::Component::message_turn_authoring(role, content, options);
        Ok((PyMessageTurn, PyComponent { inner }))
    }
}

#[pyclass(subclass, extends = PyComponent, module = "pactree.pactree_engine", name = "MessageContainer")]
pub struct PyMessageContainer;

#[pymethods]
impl PyMessageContainer {
    #[new]
    #[pyo3(signature = (content=None, *, ttl=None, cad=None, key=None, tags=None, aux=None, external_refs=None))]
    fn new(
        content: Option<&Bound<'_, PyAny>>,
        ttl: Option<i64>,
        cad: Option<i64>,
        key: Option<String>,
        tags: Option<Vec<String>>,
        aux: Option<&Bound<'_, PyAny>>,
        external_refs: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<(Self, PyComponent)> {
        let content = content
            .map(|obj| depythonize_value(obj, "content"))
            .transpose()?
            .filter(|value| !value.is_null());
        let options = authoring_options(Map::new(), ttl, cad, key, tags, aux, external_refs)?;
        let inner = pact::Component::container_authoring("container".to_string(), content, options);
        Ok((PyMessageContainer, PyComponent { inner }))
    }
}

#[pyclass(subclass, extends = PyComponent, module = "pactree.pactree_engine", name = "XMLComponent")]
pub struct PyXMLComponent;

#[pymethods]
impl PyXMLComponent {
    #[new]
    #[pyo3(signature = (content=None, *, r#type=String::from("xml"), attrs=None, ttl=None, cad=None, key=None, tags=None, aux=None, external_refs=None, **extra_attrs))]
    fn new(
        content: Option<&Bound<'_, PyAny>>,
        r#type: String,
        attrs: Option<&Bound<'_, PyAny>>,
        ttl: Option<i64>,
        cad: Option<i64>,
        key: Option<String>,
        tags: Option<Vec<String>>,
        aux: Option<&Bound<'_, PyAny>>,
        external_refs: Option<&Bound<'_, PyAny>>,
        extra_attrs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<(Self, PyComponent)> {
        let mut attrs = depythonize_object(attrs, "attrs")?;
        attrs.extend(depythonize_object(
            extra_attrs.map(|value| value.as_any()),
            "extra XML attrs",
        )?);
        let options = authoring_options(Map::new(), ttl, cad, key, tags, aux, external_refs)?;

        let (text_content, children) = split_xml_content(content)?;
        let mut inner = pact::Component::unknown_authoring(
            r#type,
            text_content,
            attrs.into_iter().collect(),
            options,
        )
        .map_err(|err| py_invalid_input(err.to_string()))?;
        inner.children = children;
        Ok((PyXMLComponent, PyComponent { inner }))
    }
}

#[pyfunction]
fn component_from_dict(payload: &Bound<'_, PyAny>) -> PyResult<PyComponent> {
    let value = depythonize_value(payload, "component payload")?;
    Ok(PyComponent {
        inner: component_from_value(value, "component payload")?,
    })
}

#[pyfunction]
#[pyo3(signature = (uri, **metadata))]
fn external_ref(
    py: Python<'_>,
    uri: String,
    metadata: Option<&Bound<'_, PyDict>>,
) -> PyResult<Py<PyAny>> {
    let mut map = Map::new();
    map.insert("kind".to_string(), Value::String("external".to_string()));
    map.insert("uri".to_string(), Value::String(uri));
    if let Some(obj) = metadata {
        let extra = depythonize_object(Some(obj.as_any()), "external_ref metadata")?;
        map.extend(extra);
    }
    to_py_object(py, &Value::Object(map))
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PyComponent>()?;
    module.add_class::<PyTextContent>()?;
    module.add_class::<PyReasoningContent>()?;
    module.add_class::<PyMediaContent>()?;
    module.add_class::<PyImageContent>()?;
    module.add_class::<PyAudioContent>()?;
    module.add_class::<PyToolCall>()?;
    module.add_class::<PyToolResult>()?;
    module.add_class::<PySystemHeader>()?;
    module.add_class::<PyMessageTurn>()?;
    module.add_class::<PyMessageContainer>()?;
    module.add_class::<PyXMLComponent>()?;
    module.add_function(wrap_pyfunction!(component_from_dict, module)?)?;
    module.add_function(wrap_pyfunction!(external_ref, module)?)?;
    Ok(())
}
