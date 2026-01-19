"""
Manual test script to verify workflow system works.

This can be run directly without pytest:
    python -m core.tests.workflows.test_workflow_manual
"""

import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from core.routing.workflows import (
    register_workflow,
    get_workflow,
    has_workflow,
)
from core.orchestration.orchestrator import _invoke_workflow_after_execute
from core.routing.workflows.examples.payment_prompt_workflow import PaymentPromptWorkflow


def test_workflow_registration():
    """Test workflow registration."""
    print("Testing workflow registration...")
    
    workflow = PaymentPromptWorkflow()
    register_workflow(workflow)
    
    assert has_workflow("CREATE_APPOINTMENT"), "Workflow should be registered"
    assert get_workflow("CREATE_APPOINTMENT") == workflow, "Should retrieve registered workflow"
    
    print("[OK] Workflow registration works")


def test_workflow_invocation():
    """Test workflow invocation."""
    print("Testing workflow invocation...")
    
    # Register workflow
    workflow = PaymentPromptWorkflow()
    register_workflow(workflow)
    
    # Create outcome
    outcome = {
        "status": "EXECUTED",
        "booking_code": "TEST123",
        "facts": {"context": {}}
    }
    
    # Invoke workflow
    result = _invoke_workflow_after_execute("CREATE_APPOINTMENT", outcome)
    
    # Verify workflow injected data
    assert result["facts"]["context"]["payment_prompt"] == "Do you want to pay now or later?"
    assert result["status"] == "EXECUTED", "Status should be preserved"
    assert result["booking_code"] == "TEST123", "Booking code should be preserved"
    
    print("[OK] Workflow invocation works")


def test_no_workflow_registered():
    """Test behavior when no workflow is registered."""
    print("Testing behavior without workflow...")
    
    outcome = {
        "status": "EXECUTED",
        "booking_code": "TEST456",
    }
    
    # Invoke for unregistered intent
    result = _invoke_workflow_after_execute("UNREGISTERED_INTENT", outcome)
    
    # Outcome should be unchanged
    assert result == outcome, "Outcome should be unchanged when no workflow registered"
    
    print("[OK] No workflow behavior works")


def main():
    """Run all manual tests."""
    print("=" * 50)
    print("Workflow System Manual Tests")
    print("=" * 50)
    print()
    
    try:
        test_workflow_registration()
        test_workflow_invocation()
        test_no_workflow_registered()
        
        print()
        print("=" * 50)
        print("[OK] All tests passed!")
        print("=" * 50)
        return 0
    except AssertionError as e:
        print()
        print("=" * 50)
        print(f"[FAIL] Test failed: {e}")
        print("=" * 50)
        return 1
    except Exception as e:
        print()
        print("=" * 50)
        print(f"[ERROR] Error: {e}")
        import traceback
        traceback.print_exc()
        print("=" * 50)
        return 1


if __name__ == "__main__":
    sys.exit(main())

