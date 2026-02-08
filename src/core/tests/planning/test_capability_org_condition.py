"""
Test capability evaluation with organization payment_required condition.

Validates that capability blocking respects org.payment_required setting:
- When org.payment_required = false -> no blocking even if payment_satisfied != true
- When org.payment_required = true -> blocking works as expected

Architecture:
- Tests that assert AWAITING_CAPABILITY must call handle_message() (orchestration layer)
- Planner-only tests assert only planner outputs (READY/allowed_actions), not orchestration statuses
"""

import sys
from pathlib import Path
from unittest.mock import Mock

# Add src/ to path for imports
src_path = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(src_path))

from core.planning.orchestration.plan_builder import build_decision_plan, _evaluate_condition
from core.config.capabilities_loader import load_capability_policies
from core.orchestration.orchestrator import handle_message
from core.orchestration.nlu import LumaClient
from core.orchestration.clients.organization_client import OrganizationClient


def test_org_payment_required_false_no_blocking():
    """
    Planner-only test: When org.payment_required = false, planner should not set active_capability.
    
    This tests the planner's condition evaluation logic, not orchestration gating.
    Even if facts.payment_satisfied != true, if org.payment_required = false,
    the condition should evaluate to False and planner should not set active_capability.
    """
    # Load capability policies
    policies = load_capability_policies()
    assert "capabilities" in policies, "Capability policies should be loaded"
    
    # Create luma_response with org.payment_required = false
    luma_response = {
        "intent": {"name": "CREATE_RESERVATION"},
        "slots": {
            "service_id": "room",
            "date_range": "2026-01-20 to 2026-01-22",
            "booking_id": 123
        },
        "missing_slots": [],
        "needs_clarification": False,
        "facts": {
            "org": {
                "payment_required": False  # Payment not required
            },
            "payment_satisfied": False  # Payment not satisfied
        }
    }
    
    # Build decision plan (planner-only test)
    plan = build_decision_plan(
        intent_name="CREATE_RESERVATION",
        luma_response=luma_response,
        domain="service",
        availability_resolved=True,
        session_state=None
    )
    
    # Planner-only assertions: check planner outputs (status, allowed_actions)
    # Do NOT assert orchestration statuses like AWAITING_CAPABILITY (that's orchestration layer)
    assert plan.get("status") in ("READY", "NEEDS_CLARIFICATION"), \
        f"Planner should return READY or NEEDS_CLARIFICATION, got: {plan.get('status')}"
    
    # Verify: planner should not set active_capability when condition is False
    assert plan.get("active_capability") is None, \
        f"When org.payment_required=false, planner should not set active_capability, got: {plan.get('active_capability')}"


def test_org_payment_required_true_blocks():
    """
    Orchestration test: When org.payment_required = true, capability gating should block.
    
    This tests the orchestration layer's capability gating logic.
    When org.payment_required = true AND payment_satisfied = false,
    handle_message() should return AWAITING_CAPABILITY status.
    """
    user_id = "test-payment-required-user"
    text = "book a room from 2026-01-20 to 2026-01-22"
    
    # Mock Luma response
    mock_luma_response = {
        "success": True,
        "intent": {"name": "CREATE_RESERVATION"},
        "slots": {
            "service_id": "room",
            "date_range": "2026-01-20 to 2026-01-22"
        },
        "missing_slots": [],
        "needs_clarification": False,
        "facts": {
            "payment_satisfied": False  # Payment not satisfied
        }
    }
    
    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.return_value = mock_luma_response
    
    # Mock organization client with payment_required = True
    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {
            "payment_required": True,  # Payment required
            "businessCategoryId": 1
        }
    }
    
    # Call handle_message (orchestration layer)
    result = handle_message(
        text=text,
        user_id=user_id,
        luma_client=mock_luma_client,
        organization_client=mock_org_client,
        organization_id=1
    )
    
    # Verify orchestration response
    assert result is not None, "Result should not be None"
    assert result.get("success") is True, f"Result should be successful, got: {result}"
    
    outcome = result.get("result") or result.get("outcome")
    assert outcome is not None, f"Outcome should not be None, result keys: {list(result.keys())}"
    
    # Verify: status should be AWAITING_CAPABILITY (orchestration gating)
    status = outcome.get("status")
    assert status == "AWAITING_CAPABILITY", \
        f"When org.payment_required=true and payment_satisfied=false, status should be AWAITING_CAPABILITY, got: {status}"
    
    # Verify: plan.status should also be AWAITING_CAPABILITY
    plan = outcome.get("plan", {})
    plan_status = plan.get("status")
    assert plan_status == "AWAITING_CAPABILITY", \
        f"plan.status should be AWAITING_CAPABILITY, got: {plan_status}"
    
    # Verify: awaiting should be PAYMENT
    awaiting = outcome.get("awaiting") or plan.get("awaiting")
    assert awaiting == "PAYMENT", \
        f"awaiting should be PAYMENT, got: {awaiting}"


def test_org_payment_required_missing_no_blocking():
    """
    Planner-only test: When org.payment_required is missing, planner should not set active_capability.
    
    This tests the planner's condition evaluation logic with missing org data.
    Missing org data should cause condition to evaluate False (safe default).
    """
    # Create luma_response without org data
    luma_response = {
        "intent": {"name": "CREATE_RESERVATION"},
        "slots": {
            "service_id": "room",
            "date_range": "2026-01-20 to 2026-01-22",
            "booking_id": 123
        },
        "missing_slots": [],
        "needs_clarification": False,
        "facts": {
            # No org data
            "payment_satisfied": False
        }
    }
    
    # Build decision plan (planner-only test)
    plan = build_decision_plan(
        intent_name="CREATE_RESERVATION",
        luma_response=luma_response,
        domain="service",
        availability_resolved=True,
        session_state=None
    )
    
    # Planner-only assertions: check planner outputs
    # Do NOT assert orchestration statuses like AWAITING_CAPABILITY (that's orchestration layer)
    assert plan.get("status") in ("READY", "NEEDS_CLARIFICATION"), \
        f"Planner should return READY or NEEDS_CLARIFICATION, got: {plan.get('status')}"
    
    # Verify: planner should not set active_capability when org data is missing
    assert plan.get("active_capability") is None, \
        f"When org.payment_required is missing, planner should not set active_capability, got: {plan.get('active_capability')}"


def test_condition_evaluator_org_namespace():
    """
    Test that _evaluate_condition correctly reads org.* from facts["org"].
    """
    facts = {
        "org": {
            "payment_required": True
        }
    }
    slots = {}
    session_state = None
    
    # Test: org.payment_required == true
    result = _evaluate_condition(
        "org.payment_required == true",
        facts=facts,
        slots=slots,
        session_state=session_state
    )
    assert result is True, "org.payment_required == true should evaluate to True"
    
    # Test: org.payment_required == false
    facts["org"]["payment_required"] = False
    result = _evaluate_condition(
        "org.payment_required == true",
        facts=facts,
        slots=slots,
        session_state=session_state
    )
    assert result is False, "org.payment_required == false should evaluate to False when compared to true"
    
    # Test: org.payment_required missing
    facts_without_org = {"payment_satisfied": False}
    result = _evaluate_condition(
        "org.payment_required == true",
        facts=facts_without_org,
        slots=slots,
        session_state=session_state
    )
    assert result is False, "Missing org.payment_required should evaluate to False"


def test_condition_evaluator_org_from_session_state():
    """
    Test that _evaluate_condition falls back to session_state["org"] if not in facts.
    """
    facts = {}  # No org in facts
    slots = {}
    session_state = {
        "org": {
            "payment_required": True
        }
    }
    
    # Test: org.payment_required from session_state
    result = _evaluate_condition(
        "org.payment_required == true",
        facts=facts,
        slots=slots,
        session_state=session_state
    )
    assert result is True, "org.payment_required from session_state should be readable"


if __name__ == "__main__":
    # Simple test runner
    test_org_payment_required_false_no_blocking()
    test_org_payment_required_true_blocks()
    test_org_payment_required_missing_no_blocking()
    test_condition_evaluator_org_namespace()
    test_condition_evaluator_org_from_session_state()
    print("All org condition tests passed!")

