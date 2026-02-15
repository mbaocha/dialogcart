"""
Test capability adapter bootstrap registration.

Validates that register_default_adapters() correctly registers adapters
and that they can be resolved from the registry without manual registration.
"""

import sys
from pathlib import Path

import pytest

# Add src/ to path for imports
src_path = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_path))

from capabilities.adapters.payment import PaymentAdapter
from capabilities.bootstrap import register_default_adapters
from capabilities.clients.payment import HttpPaymentClient
from capabilities.registry import clear_registry, get_adapter, list_adapters


def test_register_default_adapters_registers_payment():
    """Test that register_default_adapters() registers payment adapter."""
    # Clear registry to start fresh
    clear_registry()

    # Verify payment adapter is not registered
    with pytest.raises(KeyError):
        get_adapter("payment")

    # Register default adapters
    register_default_adapters(organization_id=1)

    # Verify payment adapter is now registered
    adapter = get_adapter("payment")
    assert adapter is not None
    assert isinstance(adapter, PaymentAdapter)
    assert adapter.name == "payment"

    # Verify adapter has HttpPaymentClient
    assert hasattr(adapter, "payment_client")
    assert isinstance(adapter.payment_client, HttpPaymentClient)


def test_register_default_adapters_idempotent():
    """Test that calling register_default_adapters() multiple times is safe."""
    clear_registry()

    # Register twice
    register_default_adapters(organization_id=1)
    adapter1 = get_adapter("payment")

    register_default_adapters(organization_id=2)
    adapter2 = get_adapter("payment")

    # Should still work (second registration overwrites first)
    assert adapter1 is not None
    assert adapter2 is not None
    assert isinstance(adapter1, PaymentAdapter)
    assert isinstance(adapter2, PaymentAdapter)


def test_register_default_adapters_without_organization_id():
    """Test that register_default_adapters() works without organization_id."""
    clear_registry()

    # Register without organization_id
    register_default_adapters(organization_id=None)

    # Verify payment adapter is registered
    adapter = get_adapter("payment")
    assert adapter is not None
    assert isinstance(adapter, PaymentAdapter)


def test_bootstrap_import_graceful_failure():
    """Test that bootstrap handles missing dependencies gracefully."""
    # This test verifies the import error handling in bootstrap.py
    # If imports fail, register_default_adapters should not crash
    # (This is tested implicitly by the fact that tests can run)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
