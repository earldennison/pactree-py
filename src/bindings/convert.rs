use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyList;
use pythonize::pythonize;
use serde::Serialize;

use crate::bindings::authoring::PyComponent;
use crate::bindings::errors::py_invalid_input;

pub(crate) fn depythonize_node(obj: &Bound<'_, PyAny>) -> PyResult<serde_json::Value> {
    pythonize::depythonize(obj).map_err(|err| py_invalid_input(format!("invalid node: {err}")))
}

pub(crate) fn depythonize_str_value_map(
    obj: Option<&Bound<'_, PyAny>>,
    label: &str,
) -> PyResult<std::collections::BTreeMap<String, serde_json::Value>> {
    match obj {
        Some(o) => pythonize::depythonize(o)
            .map_err(|err| py_invalid_input(format!("invalid {label}: {err}"))),
        None => Ok(std::collections::BTreeMap::new()),
    }
}

pub(crate) fn to_py_object<T: Serialize + ?Sized>(
    py: Python<'_>,
    value: &T,
) -> PyResult<Py<PyAny>> {
    pythonize(py, value)
        .map(|bound| bound.unbind())
        .map_err(|error| PyValueError::new_err(format!("failed to encode native object: {error}")))
}

pub(crate) fn component_from_value(
    value: serde_json::Value,
    label: &str,
) -> PyResult<pact::Component> {
    let value = match value {
        serde_json::Value::String(content) => serde_json::json!({
            "componentType": "text_content",
            "content": content,
        }),
        other => other,
    };
    pact::Component::from_component_value(&value)
        .map_err(|err| py_invalid_input(format!("invalid {label}: {err}")))
}

pub(crate) fn depythonize_component(
    obj: &Bound<'_, PyAny>,
    label: &str,
) -> PyResult<pact::Component> {
    if let Ok(component) = obj.extract::<PyRef<'_, PyComponent>>() {
        return Ok(component.inner.clone());
    }
    let value = depythonize_node(obj)?;
    component_from_value(value, label)
}

pub(crate) fn depythonize_components(
    obj: &Bound<'_, PyAny>,
    label: &str,
) -> PyResult<Vec<pact::Component>> {
    let list = obj
        .cast::<PyList>()
        .map_err(|_| py_invalid_input(format!("{label} must be a list")))?;
    let mut out = Vec::with_capacity(list.len());
    for item in list.iter() {
        out.push(depythonize_component(&item, label)?);
    }
    Ok(out)
}

pub(crate) fn split_xml_content(
    content: Option<&Bound<'_, PyAny>>,
) -> PyResult<(Option<String>, Vec<pact::Component>)> {
    let Some(obj) = content else {
        return Ok((None, Vec::new()));
    };
    if obj.is_none() {
        return Ok((None, Vec::new()));
    }

    if let Ok(text) = obj.extract::<String>() {
        return Ok((Some(text), Vec::new()));
    }

    if let Ok(component) = obj.extract::<PyRef<'_, PyComponent>>() {
        return Ok((None, vec![component.inner.clone()]));
    }

    Ok((None, depythonize_components(obj, "content")?))
}
