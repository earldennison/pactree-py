use pyo3::create_exception;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::bindings::convert::{depythonize_components, depythonize_str_value_map, to_py_object};
use crate::bindings::errors::{py_invalid_input, PactError};

create_exception!(
    pactree_engine,
    PactAppendError,
    PactError,
    "A bounded append failed; `category` names the native failure variant."
);

pub const CAPABILITY: &str = pact::PACT_APPEND_CAPABILITY;

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn parse_hex_32(text: &str, label: &str) -> PyResult<[u8; 32]> {
    if text.len() != 64 || !text.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(py_invalid_input(format!(
            "{label} must be exactly 64 lowercase hexadecimal characters"
        )));
    }
    if text.bytes().any(|byte| byte.is_ascii_uppercase()) {
        return Err(py_invalid_input(format!(
            "{label} must be lowercase hexadecimal"
        )));
    }
    let mut out = [0_u8; 32];
    for (index, chunk) in text.as_bytes().chunks(2).enumerate() {
        let pair = std::str::from_utf8(chunk).map_err(|_| py_invalid_input(label))?;
        out[index] = u8::from_str_radix(pair, 16).map_err(|_| py_invalid_input(label))?;
    }
    Ok(out)
}

fn version_to_py(py: Python<'_>, version: &pact::PactFileVersion) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    dict.set_item("file_identity", hex(&version.file_identity))?;
    dict.set_item("generation", version.generation)?;
    dict.set_item("branch_id", &version.branch_id)?;
    dict.set_item("revision", version.revision)?;
    Ok(dict.into_any().unbind())
}

fn version_from_py(obj: &Bound<'_, PyAny>) -> PyResult<pact::PactFileVersion> {
    let dict = obj
        .cast::<PyDict>()
        .map_err(|_| py_invalid_input("expected must be a mapping"))?;
    let field = |name: &str| -> PyResult<Bound<'_, PyAny>> {
        dict.get_item(name)?
            .ok_or_else(|| py_invalid_input(format!("expected.{name} is required")))
    };
    let identity: String = field("file_identity")?
        .extract()
        .map_err(|_| py_invalid_input("expected.file_identity must be a string"))?;
    let generation = extract_u64(&field("generation")?, "expected.generation")?;
    let revision = extract_u64(&field("revision")?, "expected.revision")?;
    let branch_id: String = field("branch_id")?
        .extract()
        .map_err(|_| py_invalid_input("expected.branch_id must be a string"))?;
    Ok(pact::PactFileVersion {
        file_identity: parse_hex_32(&identity, "expected.file_identity")?,
        generation,
        branch_id,
        revision,
    })
}

fn extract_u64(obj: &Bound<'_, PyAny>, label: &str) -> PyResult<u64> {
    if obj.is_instance_of::<pyo3::types::PyBool>() {
        return Err(py_invalid_input(format!(
            "{label} must be an integer, not a bool"
        )));
    }
    obj.extract::<u64>()
        .map_err(|_| py_invalid_input(format!("{label} must be a nonnegative integer")))
}

fn extract_usize(obj: &Bound<'_, PyAny>, label: &str) -> PyResult<usize> {
    usize::try_from(extract_u64(obj, label)?)
        .map_err(|_| py_invalid_input(format!("{label} exceeds the platform limit")))
}

fn memory_options_from_py(obj: Option<&Bound<'_, PyAny>>) -> PyResult<pact::PactMemoryOptions> {
    let Some(obj) = obj else {
        return Ok(pact::PactMemoryOptions::default());
    };
    if obj.is_none() {
        return Ok(pact::PactMemoryOptions::default());
    }
    let dict = obj
        .cast::<PyDict>()
        .map_err(|_| py_invalid_input("memory_options must be a mapping"))?;
    const FIELDS: [&str; 5] = [
        "page_cache_bytes",
        "component_cache_bytes",
        "scratch_cache_bytes",
        "io_chunk_bytes",
        "in_flight_bytes",
    ];
    for key in dict.keys().iter() {
        let key: String = key
            .extract()
            .map_err(|_| py_invalid_input("memory_options keys must be strings"))?;
        if !FIELDS.contains(&key.as_str()) {
            return Err(py_invalid_input(format!(
                "memory_options has an unknown field: {key}"
            )));
        }
    }
    let field = |name: &str| -> PyResult<usize> {
        let value = dict
            .get_item(name)?
            .ok_or_else(|| py_invalid_input(format!("memory_options.{name} is required")))?;
        extract_usize(&value, &format!("memory_options.{name}"))
    };
    let options = pact::PactMemoryOptions {
        page_cache_bytes: field("page_cache_bytes")?,
        component_cache_bytes: field("component_cache_bytes")?,
        scratch_cache_bytes: field("scratch_cache_bytes")?,
        io_chunk_bytes: field("io_chunk_bytes")?,
        in_flight_bytes: field("in_flight_bytes")?,
    };
    if options.io_chunk_bytes == 0 {
        return Err(py_invalid_input(
            "memory_options.io_chunk_bytes must be positive",
        ));
    }
    Ok(options)
}

fn receipt_to_py(py: Python<'_>, receipt: &pact::PactAppendReceipt) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    dict.set_item("version", version_to_py(py, &receipt.version)?)?;
    dict.set_item("transaction_id", &receipt.transaction_id)?;
    dict.set_item("source", &receipt.source)?;
    dict.set_item("batch_id", &receipt.batch_id)?;
    dict.set_item("request_digest", hex(&receipt.request_digest))?;
    dict.set_item("checkpoint", receipt.checkpoint.clone())?;
    dict.set_item("row_ids", PyList::new(py, receipt.row_ids.iter())?)?;
    Ok(dict.into_any().unbind())
}

fn outcome_to_py(py: Python<'_>, outcome: &pact::PactAppendOutcome) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    let (label, receipt) = match outcome {
        pact::PactAppendOutcome::Committed(receipt) => ("committed", receipt),
        pact::PactAppendOutcome::AlreadyCommitted(receipt) => ("already_committed", receipt),
    };
    dict.set_item("status", label)?;
    dict.set_item("receipt", receipt_to_py(py, receipt)?)?;
    Ok(dict.into_any().unbind())
}

fn report_to_py(py: Python<'_>, report: &pact::PactMaintenanceReport) -> PyResult<Py<PyAny>> {
    to_py_object(
        py,
        &serde_json::json!({
            "generation": report.generation,
            "file_bytes_before": report.file_bytes_before,
            "file_bytes_after": report.file_bytes_after,
            "derived_amplification_permille": report.derived_amplification_permille,
            "dead_page_permille": report.dead_page_permille,
            "needed": report.needed,
            "repacked": report.repacked,
            "reasons": report.reasons,
        }),
    )
}

fn append_failure_to_py(py: Python<'_>, failure: pact::PactAppendFailure) -> PyErr {
    let category = failure.category();
    let message = failure.to_string();
    let build = || -> PyResult<PyErr> {
        let error_type = py.get_type::<PactAppendError>();
        let instance = error_type.call1((message.clone(),))?;
        instance.setattr("category", category)?;
        instance.setattr("actual", py.None())?;
        instance.setattr("source", py.None())?;
        instance.setattr("batch_id", py.None())?;
        match &failure {
            pact::PactAppendFailure::Conflict { actual } => {
                instance.setattr("actual", version_to_py(py, actual)?)?;
            }
            pact::PactAppendFailure::OutcomeUnknown {
                source, batch_id, ..
            } => {
                instance.setattr("source", source.as_str())?;
                instance.setattr("batch_id", batch_id.as_str())?;
            }
            _ => {}
        }
        Ok(PyErr::from_value(instance))
    };
    build().unwrap_or_else(|error| error)
}

#[pyclass(module = "pactree.pactree_engine", name = "AppendWriter")]
pub struct PyAppendWriter {
    inner: Option<pact::PactAppendWriter>,
}

impl PyAppendWriter {
    fn writer(&self) -> PyResult<&pact::PactAppendWriter> {
        self.inner
            .as_ref()
            .ok_or_else(|| py_invalid_input("append writer is closed"))
    }

    fn writer_mut(&mut self) -> PyResult<&mut pact::PactAppendWriter> {
        self.inner
            .as_mut()
            .ok_or_else(|| py_invalid_input("append writer is closed"))
    }
}

fn message_from_py(obj: &Bound<'_, PyAny>, index: usize) -> PyResult<pact::PactAppendMessage> {
    let dict = obj
        .cast::<PyDict>()
        .map_err(|_| py_invalid_input(format!("messages[{index}] must be a mapping")))?;
    let row_id: String = dict
        .get_item("row_id")?
        .ok_or_else(|| py_invalid_input(format!("messages[{index}].row_id is required")))?
        .extract()
        .map_err(|_| py_invalid_input(format!("messages[{index}].row_id must be a string")))?;
    let role: String = dict
        .get_item("role")?
        .ok_or_else(|| py_invalid_input(format!("messages[{index}].role is required")))?
        .extract()
        .map_err(|_| py_invalid_input(format!("messages[{index}].role must be a string")))?;
    let parts = dict
        .get_item("parts")?
        .ok_or_else(|| py_invalid_input(format!("messages[{index}].parts is required")))?;
    let parts = depythonize_components(&parts, &format!("messages[{index}].parts"))?;
    let aux = depythonize_str_value_map(dict.get_item("aux")?.as_ref(), "aux")?;
    Ok(pact::PactAppendMessage {
        row_id,
        role,
        parts,
        aux,
    })
}

fn batch_from_py(obj: &Bound<'_, PyAny>) -> PyResult<pact::PactAppendBatch> {
    let dict = obj
        .cast::<PyDict>()
        .map_err(|_| py_invalid_input("batch must be a mapping"))?;
    let text = |name: &str| -> PyResult<String> {
        dict.get_item(name)?
            .ok_or_else(|| py_invalid_input(format!("batch.{name} is required")))?
            .extract()
            .map_err(|_| py_invalid_input(format!("batch.{name} must be a string")))
    };
    let optional_text = |name: &str| -> PyResult<Option<String>> {
        match dict.get_item(name)? {
            Some(value) if !value.is_none() => value
                .extract()
                .map(Some)
                .map_err(|_| py_invalid_input(format!("batch.{name} must be a string"))),
            _ => Ok(None),
        }
    };
    let expected = dict
        .get_item("expected")?
        .ok_or_else(|| py_invalid_input("batch.expected is required"))?;
    let messages = dict
        .get_item("messages")?
        .ok_or_else(|| py_invalid_input("batch.messages is required"))?;
    let messages = messages
        .cast::<PyList>()
        .map_err(|_| py_invalid_input("batch.messages must be a list"))?
        .iter()
        .enumerate()
        .map(|(index, item)| message_from_py(&item, index))
        .collect::<PyResult<Vec<_>>>()?;
    Ok(pact::PactAppendBatch {
        source: text("source")?,
        batch_id: text("batch_id")?,
        expected: version_from_py(&expected)?,
        previous_checkpoint: optional_text("previous_checkpoint")?,
        checkpoint: optional_text("checkpoint")?,
        messages,
    })
}

#[pymethods]
impl PyAppendWriter {
    #[staticmethod]
    #[pyo3(signature = (path, branch_id="main", memory_options=None, derived_depth=false))]
    fn open(
        py: Python<'_>,
        path: String,
        branch_id: &str,
        memory_options: Option<&Bound<'_, PyAny>>,
        derived_depth: bool,
    ) -> PyResult<Self> {
        let options = memory_options_from_py(memory_options)?;
        let branch_id = branch_id.to_string();
        let append_options = pact::PactAppendOptions { derived_depth };
        let inner = py
            .detach(|| {
                pact::PactAppendWriter::open_with_options(
                    &path,
                    &branch_id,
                    options,
                    append_options,
                )
            })
            .map_err(|failure| append_failure_to_py(py, failure))?;
        Ok(Self { inner: Some(inner) })
    }

    #[getter]
    fn capability(&self) -> &'static str {
        CAPABILITY
    }

    #[getter]
    fn path(&self) -> PyResult<String> {
        Ok(self.writer()?.path().display().to_string())
    }

    #[getter]
    fn closed(&self) -> bool {
        self.inner.is_none()
    }

    fn version(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        version_to_py(py, self.writer()?.version())
    }

    fn checkpoint(&self, py: Python<'_>, source: &str) -> PyResult<Option<String>> {
        let writer = self.writer()?;
        py.detach(|| writer.checkpoint(source))
            .map_err(|failure| append_failure_to_py(py, failure))
    }

    fn lookup(&self, py: Python<'_>, source: &str, batch_id: &str) -> PyResult<Py<PyAny>> {
        let writer = self.writer()?;
        let receipt = py
            .detach(|| writer.lookup(source, batch_id))
            .map_err(|failure| append_failure_to_py(py, failure))?;
        match receipt {
            Some(receipt) => receipt_to_py(py, &receipt),
            None => Ok(py.None()),
        }
    }

    fn latest_receipt(&self, py: Python<'_>, source: &str) -> PyResult<Py<PyAny>> {
        let writer = self.writer()?;
        let receipt = py
            .detach(|| writer.latest_receipt(source))
            .map_err(|failure| append_failure_to_py(py, failure))?;
        match receipt {
            Some(receipt) => receipt_to_py(py, &receipt),
            None => Ok(py.None()),
        }
    }

    fn append_batch(&mut self, py: Python<'_>, batch: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        let batch = batch_from_py(batch)?;
        let writer = self.writer_mut()?;
        let outcome = py
            .detach(|| writer.append(batch))
            .map_err(|failure| append_failure_to_py(py, failure))?;
        outcome_to_py(py, &outcome)
    }

    fn maintain(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let writer = self.writer_mut()?;
        let report = py
            .detach(|| writer.maintain())
            .map_err(|failure| append_failure_to_py(py, failure))?;
        report_to_py(py, &report)
    }

    fn set_automatic_maintenance(&mut self, enabled: bool) -> PyResult<()> {
        self.writer_mut()?.set_automatic_maintenance(enabled);
        Ok(())
    }

    #[getter]
    fn maintenance_debt(&self) -> PyResult<Option<String>> {
        Ok(self.writer()?.maintenance_debt().map(str::to_string))
    }

    fn close(&mut self) {
        if let Some(writer) = self.inner.take() {
            writer.close();
        }
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    #[pyo3(signature = (*_args))]
    fn __exit__(&mut self, _args: &Bound<'_, pyo3::types::PyTuple>) -> bool {
        self.close();
        false
    }
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PyAppendWriter>()?;
    module.add("PactAppendError", module.py().get_type::<PactAppendError>())?;
    module.add("PACT_APPEND_CAPABILITY", CAPABILITY)?;
    Ok(())
}
