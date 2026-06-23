"""
Capability adapters — multi-turn integrations for AWAITING_CAPABILITY.

Part of extensions.capabilities.
"""

# Optional: Export adapters for convenience
# Users can import from extensions.capabilities.adapters or extensions.capabilities
from .adapters import NoopAdapter, PaymentAdapter
from .base import AdapterResponse, CapabilityAdapter

# Export bootstrap function
from .bootstrap import register_default_adapters
from .registry import (
    ADAPTER_REGISTRY,
    clear_registry,
    get_adapter,
    list_adapters,
    register_adapter,
)
from .runner import CapabilityRunner, InMemoryStateStore, RunnerResult

__all__ = [
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
    # Adapters (optional convenience exports)
    "PaymentAdapter",
    "NoopAdapter",
]
