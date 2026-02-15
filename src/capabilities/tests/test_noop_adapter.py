"""
Simple Integration Test: Noop Capability Adapter

Tests the capability runner and adapter directly without calling core.
This validates the runner-adapter integration without external dependencies.
"""

import os
import sys
from pathlib import Path

from capabilities.adapters.noop import NoopAdapter
from capabilities.registry import clear_registry, get_adapter, register_adapter
from capabilities.runner import CapabilityRunner

# Add src/ to Python path (test is in src/capabilities/tests/)
# Go up to src/ directory
src_path = Path(__file__).resolve().parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Set execution mode to test
os.environ["CORE_EXECUTION_MODE"] = "test"


def test_noop_adapter_direct():
    """Test noop adapter directly."""
    print("Test 1: Direct adapter test...")

    # Arrange
    clear_registry()
    register_adapter(NoopAdapter())

    try:
        # Act
        adapter = get_adapter("noop")
        response = adapter.start(context={})

        # Assert
        assert response.completed is True, "Adapter should complete immediately"
        assert (
            "noop_done" in response.facts
        ), f"Facts should contain 'noop_done', got: {response.facts}"
        assert response.facts["noop_done"] is True, "noop_done should be True"
        assert response.text is None, "Noop adapter should not return text"

        print("  PASS: Adapter works correctly")

    finally:
        clear_registry()


def test_runner_with_noop_adapter():
    """Test runner with noop adapter."""
    print("Test 2: Runner with noop adapter...")

    # Arrange
    clear_registry()
    register_adapter(NoopAdapter())

    # Mock core outcome with AWAITING_CAPABILITY
    core_outcome = {
        "status": "AWAITING_CAPABILITY",
        "active_capability": "noop",
        "facts": {},
    }

    context = {
        "user_id": "test-user",
        "session_slots": {},
        "session_facts": {},
        "domain": "service",
        "timezone": "UTC",
        "organization_id": 1,
        "transaction_id": "test-001",
    }

    try:
        # Act
        runner = CapabilityRunner()
        runner_result = runner.handle(
            user_input="hello", core_outcome=core_outcome, context=context
        )

        # Assert
        assert (
            runner_result.passthrough is True
        ), f"Runner should return passthrough=True when adapter completes, got: {runner_result.passthrough}"
        assert runner_result.facts is not None, "Runner should return facts"
        assert (
            "noop_done" in runner_result.facts
        ), f"Facts should contain 'noop_done', got: {runner_result.facts}"
        assert (
            runner_result.facts["noop_done"] is True
        ), f"noop_done should be True, got: {runner_result.facts.get('noop_done')}"
        # When adapter completes, active_capability should be cleared
        assert (
            runner_result.active_capability is None
        ), f"active_capability should be None after adapter completes, got: {runner_result.active_capability}"

        print("  PASS: Runner correctly routes to adapter and merges facts")

    finally:
        clear_registry()


def test_runner_with_missing_adapter():
    """Test runner handles missing adapter gracefully."""
    print("Test 3: Runner with missing adapter...")

    # Arrange
    clear_registry()  # No adapters registered

    core_outcome = {
        "status": "AWAITING_CAPABILITY",
        "active_capability": "noop",
        "facts": {},
    }

    context = {
        "user_id": "test-user",
        "session_slots": {},
        "session_facts": {},
        "domain": "service",
        "timezone": "UTC",
        "organization_id": 1,
        "transaction_id": "test-002",
    }

    try:
        # Act
        runner = CapabilityRunner()
        runner_result = runner.handle(
            user_input="hello", core_outcome=core_outcome, context=context
        )

        # Assert: Runner should handle missing adapter gracefully
        # It returns passthrough=True to allow system to continue
        assert (
            runner_result.passthrough is True
        ), "Runner should return passthrough=True when adapter missing"
        assert (
            runner_result.facts is None
        ), "Runner should not return facts when adapter missing"
        assert (
            runner_result.active_capability is None
        ), "Runner should clear active_capability when adapter missing"

        print("  PASS: Runner handles missing adapter gracefully (passthrough)")

    finally:
        clear_registry()


def test_runner_passthrough_when_no_capability():
    """Test runner passes through when no capability active."""
    print("Test 4: Runner passthrough when no capability...")

    # Arrange
    clear_registry()
    register_adapter(NoopAdapter())

    # Core outcome without AWAITING_CAPABILITY
    core_outcome = {"status": "READY", "facts": {}}

    context = {
        "user_id": "test-user",
        "session_slots": {},
        "session_facts": {},
        "domain": "service",
        "timezone": "UTC",
        "organization_id": 1,
        "transaction_id": "test-003",
    }

    try:
        # Act
        runner = CapabilityRunner()
        runner_result = runner.handle(
            user_input="hello", core_outcome=core_outcome, context=context
        )

        # Assert
        assert (
            runner_result.passthrough is True
        ), "Runner should passthrough when no capability active"
        assert (
            runner_result.facts is None
        ), "Runner should not return facts when passthrough"
        assert (
            runner_result.active_capability is None
        ), "Runner should not have active_capability when passthrough"

        print("  PASS: Runner correctly passes through when no capability")

    finally:
        clear_registry()


if __name__ == "__main__":
    print("Running simple noop capability tests...")
    print()

    try:
        test_noop_adapter_direct()
        print()
        test_runner_with_noop_adapter()
        print()
        test_runner_with_missing_adapter()
        print()
        test_runner_passthrough_when_no_capability()
        print()
        print("All tests passed!")
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
