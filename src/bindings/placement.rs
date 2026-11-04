use pyo3::prelude::*;

use crate::bindings::errors::py_invalid_input;

#[pyclass(module = "pactree.pactree_engine", name = "Placement")]
#[derive(Clone)]
pub struct PyPlacement {
    inner: pact::Placement,
}

#[pymethods]
impl PyPlacement {
    #[new]
    #[pyo3(signature = (target, offset=None))]
    fn new(target: &Bound<'_, PyAny>, offset: Option<i32>) -> PyResult<Self> {
        if target.is_instance_of::<pyo3::types::PyBool>() {
            return Err(py_invalid_input(
                "Placement target must be an int depth or str, not bool".to_string(),
            ));
        }
        let inner = if let Ok(depth) = target.extract::<i32>() {
            pact::Placement::from((depth, offset.unwrap_or(0)))
        } else if let Ok(text) = target.extract::<String>() {
            match offset {
                Some(off) => pact::Placement::from((text.as_str(), off)),
                None => pact::Placement::from(text.as_str()),
            }
        } else {
            return Err(py_invalid_input(
                "Placement target must be an int depth or a str (selector / depth / component id)"
                    .to_string(),
            ));
        };
        Ok(Self { inner })
    }

    fn __repr__(&self) -> String {
        format!("Placement({:?})", self.inner)
    }
}

impl PyPlacement {
    fn placement(&self) -> pact::Placement {
        self.inner.clone()
    }
}

pub(crate) fn placement_arg(obj: &Bound<'_, PyAny>) -> PyResult<pact::Placement> {
    if let Ok(placement) = obj.extract::<PyPlacement>() {
        return Ok(placement.placement());
    }
    if let Ok((text, offset)) = obj.extract::<(String, i32)>() {
        return Ok(pact::Placement::from((text.as_str(), offset)));
    }
    if let Ok((depth, offset)) = obj.extract::<(i32, i32)>() {
        return Ok(pact::Placement::from((depth, offset)));
    }
    if let Ok(text) = obj.extract::<String>() {
        return Ok(pact::Placement::from(text.as_str()));
    }
    Err(py_invalid_input(
        "placement must be a Placement, a str, an (int, int) tuple, or a (str, int) tuple"
            .to_string(),
    ))
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PyPlacement>()?;
    Ok(())
}
