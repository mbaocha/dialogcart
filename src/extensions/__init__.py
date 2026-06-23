"""
Extensions — non-core behavior outside the booking kernel.

Subpackages:
- capabilities: Multi-turn capability adapters (payment, KYC) — AWAITING_CAPABILITY
- handlers:     Single-shot intent handlers (RAG) — HANDLER_DELEGATED
"""

from .bootstrap import register_default_extensions
from .capabilities import (
    ADAPTER_REGISTRY,
    AdapterResponse,
    CapabilityAdapter,
    CapabilityRunner,
    InMemoryStateStore,
    NoopAdapter,
    PaymentAdapter,
    RunnerResult,
    clear_registry,
    get_adapter,
    list_adapters,
    register_adapter,
    register_default_adapters,
)
from .handlers import (
    HANDLER_REGISTRY,
    HandlerResponse,
    HandlerRunner,
    IntentHandler,
    clear_registry as clear_handler_registry,
    get_handler,
    list_handlers,
    register_handler,
    register_default_handlers,
)

__all__ = [
    "register_default_extensions",
    # capabilities
    "CapabilityAdapter",
    "AdapterResponse",
    "CapabilityRunner",
    "RunnerResult",
    "InMemoryStateStore",
    "ADAPTER_REGISTRY",
    "register_adapter",
    "register_default_adapters",
    "get_adapter",
    "list_adapters",
    "clear_registry",
    "PaymentAdapter",
    "NoopAdapter",
    # handlers
    "IntentHandler",
    "HandlerResponse",
    "HandlerRunner",
    "HANDLER_REGISTRY",
    "register_handler",
    "register_default_handlers",
    "get_handler",
    "list_handlers",
    "clear_handler_registry",
]
