"""
Capabilities Module

Provides adapter interface for external capabilities (payment, KYC, consent, etc.)
that integrate with DialogCart core via the capability gate mechanism.

Adapters are invoked when core emits AWAITING_CAPABILITY status and operate
independently of core intent/planning logic.
"""

from .base import CapabilityAdapter, AdapterResponse
from .runner import CapabilityRunner, RunnerResult, InMemoryStateStore
from .registry import (
    ADAPTER_REGISTRY,
    register_adapter,
    get_adapter,
    list_adapters,
    clear_registry
)

# Optional: Export adapters for convenience
# Users can import from capabilities.adapters or capabilities
from .adapters import PaymentAdapter, NoopAdapter

# Export bootstrap function
from .bootstrap import register_default_adapters

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

