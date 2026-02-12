"""
Tests for hardened CONFIRM_* continuation logic.

Validates that CONFIRM_* intents are only treated as booking continuation when:
1) session has a durable booking intent
2) AND session.status == "AWAITING_CONFIRMATION"

Test Cases:
- Case A: Session status = AWAITING_CONFIRMATION → Should continue durable intent
- Case B: Session status = NEEDS_CLARIFICATION → Should NOT auto-confirm
- Case C: No session → Should NOT mutate booking state
"""

import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any, Optional

# Add src to path
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from core.planning.orchestration.intent_resolution import resolve_effective_intent


def test_confirm_continuation_with_awaiting_confirmation_status():
    """
    Case A: Session status = AWAITING_CONFIRMATION
    User says "confirmed"
    → Should continue durable intent
    → Should set confirmation_state="confirmed" (tested in orchestrator)
    """
    # Mock Luma response with CONFIRM_* intent
    luma_response = {
        "intent": {
            "name": "CONFIRM_ACTION"
        },
        "slots": {},
        "missing_slots": []
    }
    
    # Session with AWAITING_CONFIRMATION status and durable intent
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "slots": {
            "service_id": "svc_123",
            "date": "2024-01-15",
            "time": "14:00"
        }
    }
    
    # Mock get_intent_durable to return True for CREATE_APPOINTMENT
    # Patch at source module since intent_resolution imports it from core.policy.intent_policy
    with patch('core.policy.intent_policy.get_intent_durable') as mock_durable:
        mock_durable.return_value = True
        
        effective_intent, session_reset_occurred = resolve_effective_intent(
            luma_response=luma_response,
            session_state=session_state,
            user_id="test_user_123"
        )
    
    # Assert: CONFIRM_* was treated as continuation (effective_intent = session intent)
    assert effective_intent == "CREATE_APPOINTMENT", \
        f"Expected effective_intent=CREATE_APPOINTMENT (continuation), got {effective_intent}"
    
    # Assert: No session reset occurred
    assert session_reset_occurred == False, \
        f"Expected session_reset_occurred=False (continuation), got {session_reset_occurred}"


def test_confirm_not_continuation_with_needs_clarification_status():
    """
    Case B: Session status = NEEDS_CLARIFICATION
    User says "confirmed"
    → Should NOT auto-confirm
    → Should behave as non-core intent
    """
    # Mock Luma response with CONFIRM_* intent
    luma_response = {
        "intent": {
            "name": "CONFIRM_ACTION"
        },
        "slots": {},
        "missing_slots": []
    }
    
    # Session with NEEDS_CLARIFICATION status and durable intent
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION",
        "slots": {
            "service_id": "svc_123"
            # Missing date and time
        }
    }
    
    # Mock get_intent_durable to return True for CREATE_APPOINTMENT
    # Patch at source module since intent_resolution imports it from core.policy.intent_policy
    with patch('core.policy.intent_policy.get_intent_durable') as mock_durable:
        mock_durable.return_value = True
        
        effective_intent, session_reset_occurred = resolve_effective_intent(
            luma_response=luma_response,
            session_state=session_state,
            user_id="test_user_123"
        )
    
    # Assert: CONFIRM_* was NOT treated as continuation (effective_intent = CONFIRM_ACTION)
    assert effective_intent == "CONFIRM_ACTION", \
        f"Expected effective_intent=CONFIRM_ACTION (not continuation), got {effective_intent}"
    
    # Note: session_reset_occurred behavior depends on other logic, but key is that
    # effective_intent is CONFIRM_ACTION, not CREATE_APPOINTMENT


def test_confirm_not_continuation_with_ready_status():
    """
    Case B variant: Session status = READY
    User says "confirmed"
    → Should NOT auto-confirm
    → Should behave as non-core intent
    """
    # Mock Luma response with CONFIRM_* intent
    luma_response = {
        "intent": {
            "name": "CONFIRM_ACTION"
        },
        "slots": {},
        "missing_slots": []
    }
    
    # Session with READY status and durable intent
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "slots": {
            "service_id": "svc_123",
            "date": "2024-01-15",
            "time": "14:00"
        }
    }
    
    # Mock get_intent_durable to return True for CREATE_APPOINTMENT
    # Patch at source module since intent_resolution imports it from core.policy.intent_policy
    with patch('core.policy.intent_policy.get_intent_durable') as mock_durable:
        mock_durable.return_value = True
        
        effective_intent, session_reset_occurred = resolve_effective_intent(
            luma_response=luma_response,
            session_state=session_state,
            user_id="test_user_123"
        )
    
    # Assert: CONFIRM_* was NOT treated as continuation (effective_intent = CONFIRM_ACTION)
    assert effective_intent == "CONFIRM_ACTION", \
        f"Expected effective_intent=CONFIRM_ACTION (not continuation), got {effective_intent}"


def test_confirm_no_session():
    """
    Case C: No session
    User says "confirmed"
    → Should NOT mutate booking state
    → Should use CONFIRM_ACTION as effective intent
    """
    # Mock Luma response with CONFIRM_* intent
    luma_response = {
        "intent": {
            "name": "CONFIRM_ACTION"
        },
        "slots": {},
        "missing_slots": []
    }
    
    # No session state
    session_state = None
    
    effective_intent, session_reset_occurred = resolve_effective_intent(
        luma_response=luma_response,
        session_state=session_state,
        user_id="test_user_123"
    )
    
    # Assert: CONFIRM_ACTION is used as effective intent (no continuation logic)
    assert effective_intent == "CONFIRM_ACTION", \
        f"Expected effective_intent=CONFIRM_ACTION (no session), got {effective_intent}"
    
    # Assert: No session reset (no session to reset)
    assert session_reset_occurred == False, \
        f"Expected session_reset_occurred=False (no session), got {session_reset_occurred}"


def test_confirm_continuation_with_null_status():
    """
    Edge case: Session exists but status is None
    → Should NOT treat as continuation (defensive null check)
    """
    # Mock Luma response with CONFIRM_* intent
    luma_response = {
        "intent": {
            "name": "CONFIRM_ACTION"
        },
        "slots": {},
        "missing_slots": []
    }
    
    # Session with null status
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": None,  # Null status
        "slots": {
            "service_id": "svc_123"
        }
    }
    
    # Mock get_intent_durable to return True for CREATE_APPOINTMENT
    # Patch at source module since intent_resolution imports it from core.policy.intent_policy
    with patch('core.policy.intent_policy.get_intent_durable') as mock_durable:
        mock_durable.return_value = True
        
        effective_intent, session_reset_occurred = resolve_effective_intent(
            luma_response=luma_response,
            session_state=session_state,
            user_id="test_user_123"
        )
    
    # Assert: CONFIRM_* was NOT treated as continuation (status is None, not AWAITING_CONFIRMATION)
    assert effective_intent == "CONFIRM_ACTION", \
        f"Expected effective_intent=CONFIRM_ACTION (null status), got {effective_intent}"


def test_confirm_continuation_with_missing_status_key():
    """
    Edge case: Session exists but status key is missing
    → Should NOT treat as continuation (defensive null check)
    """
    # Mock Luma response with CONFIRM_* intent
    luma_response = {
        "intent": {
            "name": "CONFIRM_ACTION"
        },
        "slots": {},
        "missing_slots": []
    }
    
    # Session without status key
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        # No "status" key
        "slots": {
            "service_id": "svc_123"
        }
    }
    
    # Mock get_intent_durable to return True for CREATE_APPOINTMENT
    # Patch at source module since intent_resolution imports it from core.policy.intent_policy
    with patch('core.policy.intent_policy.get_intent_durable') as mock_durable:
        mock_durable.return_value = True
        
        effective_intent, session_reset_occurred = resolve_effective_intent(
            luma_response=luma_response,
            session_state=session_state,
            user_id="test_user_123"
        )
    
    # Assert: CONFIRM_* was NOT treated as continuation (status key missing)
    assert effective_intent == "CONFIRM_ACTION", \
        f"Expected effective_intent=CONFIRM_ACTION (missing status key), got {effective_intent}"


def test_confirm_not_overridden_by_durable_recovery():
    """
    Critical test: CONFIRM_* should NOT be overridden by durable recovery
    when status != AWAITING_CONFIRMATION.
    
    This tests that even when:
    - Session has durable intent (CREATE_APPOINTMENT)
    - CONFIRM_* is non-core
    - Session status is NEEDS_CLARIFICATION (not AWAITING_CONFIRMATION)
    
    The effective_intent should remain CONFIRM_ACTION, NOT be overridden
    back to CREATE_APPOINTMENT by durable recovery or non-core preservation logic.
    """
    # Mock Luma response with CONFIRM_* intent
    luma_response = {
        "intent": {
            "name": "CONFIRM_ACTION"
        },
        "slots": {},
        "missing_slots": []
    }
    
    # Session with NEEDS_CLARIFICATION status and durable intent
    # This scenario would normally trigger non-core preservation logic
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION",
        "slots": {
            "service_id": "svc_123"
            # Missing date and time - triggers NEEDS_CLARIFICATION
        }
    }
    
    # Mock get_intent_durable to return True for CREATE_APPOINTMENT
    # This simulates a durable intent that would normally be preserved
    with patch('core.policy.intent_policy.get_intent_durable') as mock_durable:
        mock_durable.return_value = True
        
        # Mock is_core_intent to return False for CONFIRM_ACTION (non-core)
        # and True for CREATE_APPOINTMENT (core)
        with patch('core.routing.intents.base_intents.is_core_intent') as mock_is_core:
            def is_core_side_effect(intent_name):
                return intent_name == "CREATE_APPOINTMENT"
            mock_is_core.side_effect = is_core_side_effect
            
            effective_intent, session_reset_occurred = resolve_effective_intent(
                luma_response=luma_response,
                session_state=session_state,
                user_id="test_user_123"
            )
    
    # CRITICAL ASSERTION: CONFIRM_ACTION should NOT be overridden by durable recovery
    # Even though:
    # - Session has durable intent (CREATE_APPOINTMENT)
    # - CONFIRM_ACTION is non-core
    # - Non-core preservation logic would normally preserve session intent
    # The guard should prevent this and keep effective_intent = CONFIRM_ACTION
    assert effective_intent == "CONFIRM_ACTION", \
        f"Expected effective_intent=CONFIRM_ACTION (not overridden by durable recovery), got {effective_intent}. " \
        f"This indicates durable recovery or non-core preservation incorrectly overrode CONFIRM_* intent."


if __name__ == "__main__":
    print("=" * 50)
    print("CONFIRM_* Continuation Hardening Tests")
    print("=" * 50)
    print()
    
    try:
        test_confirm_continuation_with_awaiting_confirmation_status()
        print("[OK] test_confirm_continuation_with_awaiting_confirmation_status")
        
        test_confirm_not_continuation_with_needs_clarification_status()
        print("[OK] test_confirm_not_continuation_with_needs_clarification_status")
        
        test_confirm_not_continuation_with_ready_status()
        print("[OK] test_confirm_not_continuation_with_ready_status")
        
        test_confirm_no_session()
        print("[OK] test_confirm_no_session")
        
        test_confirm_continuation_with_null_status()
        print("[OK] test_confirm_continuation_with_null_status")
        
        test_confirm_continuation_with_missing_status_key()
        print("[OK] test_confirm_continuation_with_missing_status_key")
        
        test_confirm_not_overridden_by_durable_recovery()
        print("[OK] test_confirm_not_overridden_by_durable_recovery")
        
        print()
        print("=" * 50)
        print("[OK] All tests passed!")
        print("=" * 50)
        sys.exit(0)
    except AssertionError as e:
        print()
        print("=" * 50)
        print(f"[FAIL] Test failed: {e}")
        print("=" * 50)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print()
        print("=" * 50)
        print(f"[ERROR] Error: {e}")
        print("=" * 50)
        import traceback
        traceback.print_exc()
        sys.exit(1)

