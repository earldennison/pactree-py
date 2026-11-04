"""Typed failures raised by the Pactree extension host."""

from __future__ import annotations


class PactExtensionError(RuntimeError):
    """Base class for extension discovery, lifecycle, and coordination errors."""


class ExtensionConfigurationError(PactExtensionError, ValueError):
    """The ordered extension specification is malformed or duplicated."""


class ExtensionNotFoundError(PactExtensionError, LookupError):
    """No installed ``pactree.extensions`` entry point owns a requested id."""


class ExtensionCompatibilityError(PactExtensionError):
    """An installed descriptor is incompatible with this Pactree host."""


class ExtensionOrderError(PactExtensionError):
    """Extension dependencies or authority ordering are invalid."""


class ExtensionCapabilityError(PactExtensionError, PermissionError):
    """An extension requested a capability the host did not grant."""


class ExtensionActivationError(PactExtensionError):
    """An extension failed while activating or rebinding."""


class ExtensionForkError(PactExtensionError):
    """An authoritative extension could not prepare a runnable child."""


class ExtensionTransactionError(PactExtensionError):
    """A prepared extension batch could not cross its canonical seal boundary."""


class ExtensionAuthorizationError(PactExtensionError, PermissionError):
    """A host-issued extension capability is invalid, expired, or already used."""
