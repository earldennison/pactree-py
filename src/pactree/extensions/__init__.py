"""Extension-author API for independently installable Pactree capabilities."""

from .authorization import (
    ApplyAuthorization,
    AuthorizationAttestation,
    AuthorizationVerifier,
)

from .errors import (
    ExtensionActivationError,
    ExtensionAuthorizationError,
    ExtensionCapabilityError,
    ExtensionCompatibilityError,
    ExtensionConfigurationError,
    ExtensionForkError,
    ExtensionNotFoundError,
    ExtensionOrderError,
    ExtensionTransactionError,
    PactExtensionError,
)
from .host import (
    EmbeddedExtensionHost,
    Pact,
    PactScopeView,
    _bind_runtime_scope,
    bind_context_tree,
    open_pact,
)
from .models import (
    PACTREE_EXTENSION_API_VERSION,
    AuthoritativeForkParticipant,
    ContextRef,
    ExtensionContext,
    ExtensionDescriptor,
    ExtensionLifecycle,
    ExtensionViewRef,
    PreparedFork,
    RuntimeSnapshot,
    SynchronizedRef,
)
from .services import PreparedExtensionBatch

__all__ = [
    "ApplyAuthorization",
    "AuthoritativeForkParticipant",
    "AuthorizationAttestation",
    "AuthorizationVerifier",
    "ContextRef",
    "EmbeddedExtensionHost",
    "ExtensionActivationError",
    "ExtensionAuthorizationError",
    "ExtensionCapabilityError",
    "ExtensionCompatibilityError",
    "ExtensionConfigurationError",
    "ExtensionContext",
    "ExtensionDescriptor",
    "ExtensionForkError",
    "ExtensionLifecycle",
    "ExtensionNotFoundError",
    "ExtensionOrderError",
    "ExtensionTransactionError",
    "ExtensionViewRef",
    "PACTREE_EXTENSION_API_VERSION",
    "Pact",
    "PactExtensionError",
    "PactScopeView",
    "PreparedExtensionBatch",
    "PreparedFork",
    "RuntimeSnapshot",
    "SynchronizedRef",
    "bind_context_tree",
    "open_pact",
]
