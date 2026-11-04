use pyo3::create_exception;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

create_exception!(
    pactree_engine,
    PactError,
    PyValueError,
    "Base class for every error raised by the pactree native engine."
);
create_exception!(
    pactree_engine,
    PactInvalidInput,
    PactError,
    "Caller supplied malformed or invalid input (bad selector, payload, argument)."
);
create_exception!(
    pactree_engine,
    PactNotFound,
    PactError,
    "A referenced node, branch, transaction, component, or revision does not exist."
);
create_exception!(
    pactree_engine,
    PactCorruptFile,
    PactError,
    "A .pact artifact is structurally corrupt (truncated, bad magic, checksum mismatch)."
);
create_exception!(
    pactree_engine,
    PactCodecError,
    PactError,
    "Encode/decode of pactree bytes or JSON failed."
);

pub(crate) fn pact_err_to_py(error: pact::PactError) -> PyErr {
    use pact::PactErrorKind as K;
    let message = error.to_string();
    match error.kind() {
        K::Invalid | K::Selector | K::Component => PactInvalidInput::new_err(message),
        K::NotFound => PactNotFound::new_err(message),
        K::CorruptFile => PactCorruptFile::new_err(message),
        K::Codec => PactCodecError::new_err(message),
        _ => PactError::new_err(message),
    }
}

pub(crate) fn py_invalid_input(message: impl Into<String>) -> PyErr {
    PactInvalidInput::new_err(message.into())
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("PactError", module.py().get_type::<PactError>())?;
    module.add(
        "PactInvalidInput",
        module.py().get_type::<PactInvalidInput>(),
    )?;
    module.add("PactNotFound", module.py().get_type::<PactNotFound>())?;
    module.add("PactCorruptFile", module.py().get_type::<PactCorruptFile>())?;
    module.add("PactCodecError", module.py().get_type::<PactCodecError>())?;
    Ok(())
}
