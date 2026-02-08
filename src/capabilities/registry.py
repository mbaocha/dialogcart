"""
Capability Adapter Registry

Registry mapping capability names to adapter instances.

The registry is the single source of truth for adapter lookup.
Runner uses registry to find adapters when core emits AWAITING_CAPABILITY.

Design:
- Fail fast if adapter not registered
- Never guess or infer adapters
- One adapter can be swapped without touching runner
"""

import logging
from typing import Dict, Optional

from .base import CapabilityAdapter

logger = logging.getLogger(__name__)

# Global adapter registry
# Maps capability name (str) → adapter instance (CapabilityAdapter)
ADAPTER_REGISTRY: Dict[str, CapabilityAdapter] = {}


def register_adapter(adapter: CapabilityAdapter) -> None:
    """
    Register a capability adapter.
    
    Args:
        adapter: Adapter instance to register
    
    Raises:
        ValueError: If adapter name is invalid or already registered
    
    Example:
        payment_adapter = PaymentAdapter()
        register_adapter(payment_adapter)
    """
    if not isinstance(adapter, CapabilityAdapter):
        raise ValueError(f"Adapter must be instance of CapabilityAdapter, got {type(adapter)}")
    
    name = adapter.name
    if not name or not isinstance(name, str):
        raise ValueError(f"Adapter name must be non-empty string, got {name!r}")
    
    if name in ADAPTER_REGISTRY:
        logger.warning(f"Adapter {name} already registered, overwriting")
    
    ADAPTER_REGISTRY[name] = adapter
    logger.info(f"Registered adapter: {name}")


def get_adapter(capability: str) -> CapabilityAdapter:
    """
    Get adapter for capability name.
    
    Args:
        capability: Capability name (e.g., "payment", "kyc")
    
    Returns:
        Adapter instance
    
    Raises:
        KeyError: If adapter not registered for capability
    
    Example:
        adapter = get_adapter("payment")
        response = adapter.start(context)
    """
    if capability not in ADAPTER_REGISTRY:
        available = list(ADAPTER_REGISTRY.keys())
        raise KeyError(
            f"Adapter not registered for capability: {capability}. "
            f"Available adapters: {available}"
        )
    
    return ADAPTER_REGISTRY[capability]


def list_adapters() -> list[str]:
    """
    List all registered adapter names.
    
    Returns:
        List of capability names
    """
    return list(ADAPTER_REGISTRY.keys())


def clear_registry() -> None:
    """
    Clear all registered adapters.
    
    Useful for testing.
    """
    ADAPTER_REGISTRY.clear()
    logger.info("Cleared adapter registry")

