"""
Core Session Follow-up Test Suite

Tests Redis-backed session behavior across multi-turn conversations.
Validates session persistence, merge, and cleanup.

Usage:
    python -m core.tests.session.test_session              # Run all scenarios
    python -m core.tests.session.test_session 22           # Run scenario 22
    python -m core.tests.session.test_session 22,24        # Run scenarios 22 and 24
    python -m core.tests.session.test_session 30-33        # Run scenarios 30-33
"""

from core.orchestration.cache.catalog_cache import catalog_cache
from core.tests.integration.test_appointment_e2e import (
    TestLumaClient,
    TestCatalogClient,
    _setup_test_org_domain,
    get_customer_details
)
from core.tests.planning.followup import followup_scenarios
from core.tests.planning.test_planning_edges import planning_edges_scenarios
from core.orchestration.session import get_session, clear_session, save_session
from core.orchestration.orchestrator import handle_message
from core.orchestration.api.session_merge import build_session_state_from_outcome
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List, Set
import json
import time
import uuid

# Set execution mode to test for deterministic tests
os.environ["CORE_EXECUTION_MODE"] = "test"

# Add src/ to Python path
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Load environment variables
try:
    from dotenv import load_dotenv
    project_root = Path(__file__).parent.parent.parent.parent
    core_env_file = Path(__file__).parent.parent.parent / ".env"
    env_file = project_root / ".env"
    env_local_file = project_root / ".env.local"

    if env_file.exists():
        load_dotenv(env_file, override=False)
    if core_env_file.exists():
        load_dotenv(core_env_file, override=True)
    if env_local_file.exists():
        load_dotenv(env_local_file, override=True)
except ImportError:
    pass
except Exception:
    pass


def parse_scenario_args(args: List[str]) -> Set[int]:
    """
    Parse scenario ID arguments from command line.

    Supports:
    - Single ID: "22"
    - Comma-separated: "22,24"
    - Range: "30-33"
    - Mixed: "22,24,30-33"

    Args:
        args: Command line arguments

    Returns:
        Set of scenario numbers (sequential, 1-based index)
    """
    if not args:
        return set()  # All scenarios

    scenario_ids = set()

    for arg in args:
        if ',' in arg:
            # Comma-separated IDs
            for part in arg.split(','):
                part = part.strip()
                if '-' in part:
                    # Range within comma-separated
                    start, end = map(int, part.split('-'))
                    scenario_ids.update(range(start, end + 1))
                else:
                    scenario_ids.add(int(part))
        elif '-' in arg:
            # Range
            start, end = map(int, arg.split('-'))
            scenario_ids.update(range(start, end + 1))
        else:
            # Single ID
            scenario_ids.add(int(arg))

    return scenario_ids


def filter_scenarios_by_id(scenarios: List[Dict[str, Any]], scenario_ids: Set[int]) -> List[Dict[str, Any]]:
    """
    Filter scenarios by sequential number (1-based index).

    Args:
        scenarios: List of scenario dicts
        scenario_ids: Set of sequential numbers to include (1-based, empty set = all)

    Returns:
        Filtered list of scenarios
    """
    if not scenario_ids:
        return scenarios

    # Use 1-based sequential index instead of scenario's "id" field
    return [scenarios[i - 1] for i in scenario_ids if 1 <= i <= len(scenarios)]


def assert_turn_expectations(
    result: Dict[str, Any],
    expected: Dict[str, Any],
    turn_index: int
) -> Optional[str]:
    """Assert turn result matches expectations.

    Validates:
    - intent_name
    - stage
    - action
    - missing_slots
    - slots (partial match)
    - executable_actions
    - time_constraint (in _merged_luma_response)

    Args:
        result: Result from handle_message
        expected: Expected outcome dict
        turn_index: Turn index for error messages

    Returns:
        Error message if assertion fails, None if passes
    """
    """
    Assert turn result matches expectations.

    Args:
        result: Result from handle_message
        expected: Expected outcome dict
        turn_index: Turn index for error messages

    Returns:
        Error message if assertion fails, None if passes
    """
    if not result or not isinstance(result, dict):
        return f"Turn {turn_index + 1} failed: result is None or not a dict: {result}"

    if not result.get("success"):
        error_msg = result.get("error", "Unknown error")
        return f"Turn {turn_index + 1} failed: {error_msg}"

    outcome = result.get("outcome", {})
    if not isinstance(outcome, dict):
        return f"Turn {turn_index + 1} failed: outcome is not a dict: {outcome}"

    # Assert intent if provided
    expected_intent = expected.get("intent")
    if expected_intent:
        # Core contract: outcome must expose intent_name
        assert "intent_name" in outcome, "Core outcome must expose intent_name"
        actual_intent = outcome.get("intent_name")
        if actual_intent != expected_intent:
            return f"Turn {turn_index + 1} intent mismatch: expected {expected_intent}, got {actual_intent}"

    # Assert stage if provided
    # Tests assert outcome.stage / outcome.action directly (not nested in plan)
    expected_stage = expected.get("stage")
    if expected_stage:
        actual_stage = outcome.get("stage")
        print(
            f"[ISOLATION_ASSERT] Checking stage: expected={expected_stage}, actual={actual_stage}, outcome.keys()={list(outcome.keys())}")
        plan_dict = outcome.get('plan', {})
        print(
            f"[ISOLATION_ASSERT] outcome.get('stage')={outcome.get('stage')}, outcome.get('plan', {{}}).get('stage')={plan_dict.get('stage')}")
        if actual_stage != expected_stage:
            return f"Turn {turn_index + 1} stage mismatch: expected {expected_stage}, got {actual_stage}"

    # Assert action if provided
    # Tests assert outcome.stage / outcome.action directly (not nested in plan)
    expected_action = expected.get("action")
    if expected_action:
        actual_action = outcome.get("action")
        print(
            f"[ISOLATION_ASSERT] Checking action: expected={expected_action}, actual={actual_action}, outcome.keys()={list(outcome.keys())}")
        plan_dict = outcome.get('plan', {})
        print(
            f"[ISOLATION_ASSERT] outcome.get('action')={outcome.get('action')}, outcome.get('plan', {{}}).get('action')={plan_dict.get('action')}")
        if actual_action != expected_action:
            return f"Turn {turn_index + 1} action mismatch: expected {expected_action}, got {actual_action}"

    # Assert missing_slots (exact match, order-insensitive)
    # Tests assert outcome.missing_slots directly (not nested in facts)
    # INVARIANT: missing_slots order does not matter
    expected_missing = expected.get("missing_slots")
    if expected_missing is not None:
        # Check top-level outcome.missing_slots first, fallback to facts.missing_slots for backward compatibility
        actual_missing = outcome.get("missing_slots")
        if actual_missing is None:
            actual_missing = outcome.get("facts", {}).get("missing_slots", [])
        if not isinstance(actual_missing, list):
            actual_missing = []

        # Compare as sets for order-insensitive match
        if set(actual_missing) != set(expected_missing):
            return f"Turn {turn_index + 1} missing_slots mismatch: expected {expected_missing}, got {actual_missing}"

        # INVARIANT: Verify missing_slots is sorted (planner returns sorted list)
        # This ensures deterministic behavior while order doesn't matter for comparison
        if actual_missing != sorted(actual_missing):
            return f"Turn {turn_index + 1} missing_slots not sorted: got {actual_missing} (should be sorted for determinism)"

    # Assert slots (partial match only)
    # Tests assert outcome.slots directly (not nested in facts)
    # INVARIANT: Slots can be provided in any order - all slots are additive
    expected_slots = expected.get("slots")
    if expected_slots:
        # Check top-level outcome.slots first, fallback to facts.slots for backward compatibility
        actual_slots = outcome.get("slots")
        if actual_slots is None:
            actual_slots = outcome.get("facts", {}).get("slots", {})
        if not isinstance(actual_slots, dict):
            actual_slots = {}

        # Partial match: check that expected keys exist in actual
        for key, expected_value in expected_slots.items():
            if key not in actual_slots:
                return f"Turn {turn_index + 1} missing slot: {key}"
            # For other values, do exact match
            if actual_slots[key] != expected_value:
                return f"Turn {turn_index + 1} slot {key} mismatch: expected {expected_value}, got {actual_slots[key]}"

    # Assert executable_actions if provided
    # INVARIANT: executable_actions appear when partial slots are present
    expected_executable_actions = expected.get("executable_actions")
    if expected_executable_actions is not None:
        plan = outcome.get("plan", {})
        actual_executable_actions = plan.get("executable_actions", [])
        if not isinstance(actual_executable_actions, list):
            actual_executable_actions = []

        # Compare as sets for order-insensitive match
        if set(actual_executable_actions) != set(expected_executable_actions):
            return f"Turn {turn_index + 1} executable_actions mismatch: expected {expected_executable_actions}, got {actual_executable_actions}"

    # Assert time_constraint if provided
    # time_constraint is in _merged_luma_response, not in outcome
    expected_time_constraint = expected.get("time_constraint")
    if expected_time_constraint is not None:
        if result is None:
            return f"Turn {turn_index + 1} cannot validate time_constraint: result not provided"

        merged_luma_response = result.get("_merged_luma_response")
        if not isinstance(merged_luma_response, dict):
            return f"Turn {turn_index + 1} time_constraint validation failed: _merged_luma_response is not a dict"

        actual_time_constraint = merged_luma_response.get("time_constraint")
        if actual_time_constraint is None:
            return f"Turn {turn_index + 1} missing time_constraint in _merged_luma_response"

        # Validate time_constraint structure
        expected_mode = expected_time_constraint.get("mode")
        if expected_mode:
            actual_mode = actual_time_constraint.get("mode")
            if actual_mode != expected_mode:
                return f"Turn {turn_index + 1} time_constraint.mode mismatch: expected {expected_mode}, got {actual_mode}"

        expected_start = expected_time_constraint.get("start")
        if expected_start:
            actual_start = actual_time_constraint.get("start")
            if actual_start != expected_start:
                return f"Turn {turn_index + 1} time_constraint.start mismatch: expected {expected_start}, got {actual_start}"

        expected_end = expected_time_constraint.get("end")
        if expected_end:
            actual_end = actual_time_constraint.get("end")
            if actual_end != expected_end:
                return f"Turn {turn_index + 1} time_constraint.end mismatch: expected {expected_end}, got {actual_end}"

        expected_label = expected_time_constraint.get("label")
        if expected_label is not None:  # Allow None explicitly
            actual_label = actual_time_constraint.get("label")
            if actual_label != expected_label:
                return f"Turn {turn_index + 1} time_constraint.label mismatch: expected {expected_label}, got {actual_label}"

    return None


def test_scenario(
    scenario: Dict[str, Any],
    scenario_id: int,
    customer_details: Dict[str, Optional[Any]],
    verbose: bool = False,
    run_id: Optional[str] = None
) -> tuple:
    """
    Test a single follow-up scenario.

    Args:
        scenario: Scenario dict
        scenario_id: Scenario ID
        customer_details: Customer details
        verbose: Verbose output
        run_id: Unique run identifier to ensure session isolation between test runs

    Returns:
        Tuple of (success: bool, error_message: Optional[str], user_id: str)
    """
    scenario_name = scenario.get("name", f"scenario_{scenario_id}")
    aliases = scenario.get("aliases", {})
    domain = scenario.get("domain", "service")
    turns = scenario.get("turns", [])

    if not turns:
        # Generate user_id even for early return
        if run_id:
            user_id = f"test_session_{scenario_id:03d}_{run_id}"
        else:
            user_id = f"test_session_{scenario_id:03d}_{int(time.time())}"
        return False, "Scenario has no turns", user_id

    # Create unique user_id for this scenario (shared across all turns)
    # Include run_id to ensure sessions are isolated between test runs
    if run_id:
        user_id = f"test_session_{scenario_id:03d}_{run_id}"
    else:
        # Fallback: use timestamp if run_id not provided
        user_id = f"test_session_{scenario_id:03d}_{int(time.time())}"

    # Clear session before test
    clear_session(user_id)

    # Create test clients
    luma_client = TestLumaClient(test_aliases=aliases)
    catalog_client = TestCatalogClient(test_aliases=aliases, domain=domain)

    # Set up org domain cache
    _setup_test_org_domain(domain)

    # Clear catalog cache
    test_org_id = int(os.getenv("ORG_ID", "1"))
    catalog_cache._mem_cache.pop((test_org_id, domain), None)

    if verbose:
        print(f"\n{'='*60}")
        print(f"Scenario {scenario_id}: {scenario_name}")
        print(f"{'='*60}")
        print(f"Domain: {domain}, Turns: {len(turns)}")

    try:
        # Execute each turn in sequence with the same user_id
        for turn_index, turn in enumerate(turns):
            sentence = turn.get("sentence", "")
            expected = turn.get("expected", {})

            if verbose:
                print(
                    f"\n--- Turn {turn_index + 1}/{len(turns)}: {sentence} ---")
                print(f"Expected: {json.dumps(expected, indent=2)}")

            # Load session state before each turn
            # SESSION LIFECYCLE RULE: For CREATE_APPOINTMENT, sessions are preserved on READY
            # Load sessions with status NEEDS_CLARIFICATION OR READY (for CREATE_APPOINTMENT)
            session_state = get_session(user_id)
            if session_state:
                session_status = session_state.get("status")
                session_intent = session_state.get("intent")
                session_intent_str = session_intent if isinstance(session_intent, str) else (
                    session_intent.get("name", "") if isinstance(session_intent, dict) else "")
                # Only consider session if status is NEEDS_CLARIFICATION, or READY for CREATE_APPOINTMENT
                if session_status != "NEEDS_CLARIFICATION" and (session_status != "READY" or session_intent_str != "CREATE_APPOINTMENT"):
                    session_state = None

            # Print session state before turn
            if verbose or turn_index > 0:  # Always print for turns after first
                print(
                    f"\n[SESSION BEFORE TURN {turn_index + 1}] user_id={user_id}")
                if session_state:
                    print(
                        f"  Session state: {json.dumps(session_state, indent=2, default=str)}")
                else:
                    print("  Session state: None (no session found)")

            # Call handle_message with the same user_id and session_state
            # Use planning_only=True to limit tests to planning/resolution scope only
            # This prevents execution logic (service resolution, catalog lookup, etc.) from running
            result = handle_message(
                user_id=user_id,
                text=sentence,
                domain=domain,
                timezone="UTC",
                phone_number=customer_details.get(
                    'phone_number') if customer_details else None,
                email=customer_details.get(
                    'email') if customer_details else None,
                customer_id=customer_details.get(
                    'customer_id') if customer_details else None,
                luma_client=luma_client,
                catalog_client=catalog_client,
                session_state=session_state,
                planning_only=True  # Stop at READY, don't execute
            )

            if not result or not isinstance(result, dict):
                error_msg = f"Turn {turn_index + 1} failed: handle_message returned None or not a dict: {result}"
                # Print minimal snapshot on failure
                print(f"\n{'='*70}")
                print(
                    f"FAIL_SNAPSHOT: scenario={scenario_name} turn={turn_index + 1} user_id={user_id}")
                print(f"{'='*70}")
                fail_snapshot = {
                    "expected": expected,
                    "got": {"error": "handle_message returned None or not a dict", "result": result},
                    "session_before": session_state,
                    "session_after": None,
                    "merged_luma_response": None,
                    "final_plan": {},
                    "facts": {}
                }
                print(json.dumps(fail_snapshot, indent=2, default=str))
                print(f"{'='*70}\n")
                return False, error_msg, user_id

            if verbose:
                print(f"\nResult:")
                print(json.dumps(result, indent=2, default=str))

            # Save session after response (same logic as API endpoint)
            # Note: result already validated above
            outcome = result.get("outcome")
            if outcome and isinstance(outcome, dict):
                outcome_status = outcome.get("status")
                # DEBUG: Print outcome status to understand what's happening
                if verbose or turn_index >= 2:  # Always print for turn 3+
                    print(
                        f"\n[OUTCOME STATUS] Turn {turn_index + 1} outcome_status={outcome_status} outcome_keys={list(outcome.keys())}")
                # Initialize new_session_state for all paths
                new_session_state = None
                merged_luma_response = result.get("_merged_luma_response")

                if outcome_status == "NEEDS_CLARIFICATION":
                    # Save session state for follow-up
                    new_session_state = build_session_state_from_outcome(
                        outcome, outcome_status, merged_luma_response, session_state, user_id
                    )
                    if new_session_state:
                        save_session(user_id, new_session_state)
                        # Print session state after save
                        print(
                            f"\n[SESSION AFTER TURN {turn_index + 1}] user_id={user_id} - SAVED")
                        print(
                            f"  Session state: {json.dumps(new_session_state, indent=2, default=str)}")
                    else:
                        print(
                            f"\n[SESSION AFTER TURN {turn_index + 1}] user_id={user_id} - NOT SAVED (new_session_state is None)")
                elif outcome_status in ("READY", "EXECUTED", "AWAITING_CONFIRMATION"):
                    # For READY status, try to build session state (will be None for non-CREATE_APPOINTMENT)
                    # EXECUTED/AWAITING_CONFIRMATION also try to build (but will return None)
                    # Exception: CREATE_APPOINTMENT with READY status preserves session for follow-up modifications
                    new_session_state = build_session_state_from_outcome(
                        outcome, outcome_status, merged_luma_response, session_state, user_id
                    )
                    if new_session_state is None:
                        clear_session(user_id)
                        print(
                            f"\n[SESSION AFTER TURN {turn_index + 1}] user_id={user_id} - CLEARED (status={outcome_status})")
                    else:
                        # Session was preserved (e.g., CREATE_APPOINTMENT on READY for follow-up modifications)
                        save_session(user_id, new_session_state)
                        print(
                            f"\n[SESSION AFTER TURN {turn_index + 1}] user_id={user_id} - SAVED (status={outcome_status}, preserved for modifications)")
                        print(
                            f"  Session state: {json.dumps(new_session_state, indent=2, default=str)}")
                else:
                    print(
                        f"\n[SESSION AFTER TURN {turn_index + 1}] user_id={user_id} - NOT SAVED (status={outcome_status})")

            # Capture data for failure snapshot after save, before assertions
            session_state_before = session_state
            session_state_after = None
            merged_luma_response_for_snapshot = result.get(
                "_merged_luma_response")
            plan_for_snapshot = outcome.get("plan", {}) if outcome else {}
            facts_for_snapshot = outcome.get("facts", {}) if outcome else {}

            # Get session after save (if saved) - session was saved above if NEEDS_CLARIFICATION
            if outcome and isinstance(outcome, dict):
                outcome_status_snapshot = outcome.get("status")
                if outcome_status_snapshot == "NEEDS_CLARIFICATION":
                    # Session was saved - get it for snapshot
                    session_state_after = get_session(user_id)

            # Assert expectations
            error_msg = assert_turn_expectations(result, expected, turn_index)
            if error_msg:
                # Print compact FAIL_SNAPSHOT on assertion failure
                actual_outcome = result.get("outcome", {}) if result else {}
                actual_json = {
                    "intent": actual_outcome.get("intent_name"),
                    "status": actual_outcome.get("status"),
                    "missing_slots": actual_outcome.get("facts", {}).get("missing_slots", []),
                    "slots": actual_outcome.get("facts", {}).get("slots", {})
                }

                # TRACE_MERGE 8: Snapshot builder debug line
                outcome_keys = list(outcome.keys()) if isinstance(
                    outcome, dict) else []
                outcome_missing_slots_top = outcome.get("missing_slots")
                outcome_missing_slots_facts = outcome.get("facts", {}).get(
                    "missing_slots") if isinstance(outcome.get("facts"), dict) else None
                outcome_slots_top = outcome.get("slots")
                outcome_slots_facts = outcome.get("facts", {}).get(
                    "slots") if isinstance(outcome.get("facts"), dict) else None
                print(f"[TRACE_MERGE] user_id={user_id} point=SNAPSHOT_BUILDER outcome_keys={outcome_keys} outcome_missing_slots_top={outcome_missing_slots_top} outcome_missing_slots_facts={outcome_missing_slots_facts} outcome_slots_top={outcome_slots_top} outcome_slots_facts={outcome_slots_facts}")

                fail_snapshot = {
                    "expected": expected,
                    "got": actual_json,
                    "session_before": session_state_before,
                    "session_after": session_state_after,
                    "merged_luma_response": merged_luma_response_for_snapshot,
                    "final_plan": plan_for_snapshot,
                    "facts": facts_for_snapshot
                }

                print(f"\n{'='*70}")
                print(
                    f"FAIL_SNAPSHOT: scenario={scenario_name} turn={turn_index + 1} user_id={user_id}")
                print(f"{'='*70}")
                print(json.dumps(fail_snapshot, indent=2, default=str))
                print(f"{'='*70}\n")

                return False, error_msg, user_id

        # SESSION LIFECYCLE RULE: Deterministic session clearing check
        # READY is terminal (session cleared) for all intents EXCEPT CREATE_APPOINTMENT
        # CREATE_APPOINTMENT never clears session on READY (allows follow-up modifications like "make it 4pm")
        final_expected = turns[-1].get("expected", {})
        final_missing_slots = final_expected.get("missing_slots", [])
        final_intent = final_expected.get("intent", "")

        # RULE: Session should be cleared when missing_slots is empty for non-CREATE_APPOINTMENT intents
        # CREATE_APPOINTMENT preserves session on READY (single deterministic rule)
        if final_missing_slots == []:
            session_state = get_session(user_id)
            # Check session intent (not just expected intent, as final turn may not specify intent)
            session_intent = None
            if session_state:
                session_intent_obj = session_state.get("intent")
                if isinstance(session_intent_obj, str):
                    session_intent = session_intent_obj
                elif isinstance(session_intent_obj, dict):
                    session_intent = session_intent_obj.get("name", "")
            # Single rule: Never expect session clearing for CREATE_APPOINTMENT
            # Check both final_intent (from expected) and session_intent (from actual session)
            is_create_appointment = (
                final_intent == "CREATE_APPOINTMENT" or session_intent == "CREATE_APPOINTMENT")
            if session_state is not None and not is_create_appointment:
                error_msg = f"Session not cleared after planning complete (missing_slots=[]). Session state: {session_state}"
                # Print FAIL_SNAPSHOT on session not cleared
                fail_snapshot = {
                    "expected": {"missing_slots": [], "session_cleared": True},
                    "got": {"missing_slots": [], "session_cleared": False, "session_state": session_state},
                    "session_before": None,
                    "session_after": session_state,
                    "merged_luma_response": None,
                    "final_plan": {},
                    "facts": {}
                }
                print(f"\n{'='*70}")
                print(
                    f"FAIL_SNAPSHOT: scenario={scenario_name} turn=FINAL user_id={user_id}")
                print(f"{'='*70}")
                print(json.dumps(fail_snapshot, indent=2, default=str))
                print(f"{'='*70}\n")
                return False, error_msg, user_id

        if verbose:
            print(f"\n[OK] Scenario {scenario_id} passed")

        return True, None, user_id

    except (AssertionError, Exception) as e:
        # Print FAIL_SNAPSHOT on exception/assertion
        # Try to capture last turn's state if available
        session_state_before = None
        session_state_after = None
        merged_luma_response_for_snapshot = None
        plan_for_snapshot = {}
        facts_for_snapshot = {}

        try:
            session_state_before = get_session(user_id)
            # For exceptions, we might not have turn data, but try to get what we can
        except Exception:
            pass

        fail_snapshot = {
            "expected": "Exception occurred - no expected data available",
            "got": {"error": str(e)},
            "session_before": session_state_before,
            "session_after": session_state_after,
            "merged_luma_response": merged_luma_response_for_snapshot,
            "final_plan": plan_for_snapshot,
            "facts": facts_for_snapshot
        }

        print(f"\n{'='*70}")
        print(
            f"FAIL_SNAPSHOT: scenario={scenario_name} turn=EXCEPTION user_id={user_id}")
        print(f"{'='*70}")
        print(json.dumps(fail_snapshot, indent=2, default=str))
        print(f"{'='*70}\n")

        import traceback
        tb = traceback.format_exc()
        return False, f"Exception in scenario {scenario_id}: {str(e)}\n{tb}", user_id
    finally:
        # Always clear session after test
        clear_session(user_id)


def cleanup_test_sessions(verbose: bool = False) -> None:
    """
    Clean up all test sessions from Redis.

    This ensures no session leakage between test runs.
    Uses pattern matching to find all test_session_* keys.

    Args:
        verbose: Verbose output
    """
    try:
        # Import here to avoid circular dependencies
        import redis
        from core.orchestration.session.session_manager import _get_redis_url, SESSION_KEY_PREFIX

        redis_url = _get_redis_url()
        if not redis_url:
            # No Redis configured, skip cleanup
            return

        redis_client = redis.from_url(redis_url)
        if not redis_client:
            return

        # Find all test session keys
        pattern = f"{SESSION_KEY_PREFIX}test_session_*"
        keys_to_delete = []

        # Scan for matching keys (Redis SCAN is safer than KEYS for production)
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(cursor, match=pattern, count=100)
            keys_to_delete.extend(keys)
            if cursor == 0:
                break

        if keys_to_delete:
            deleted = redis_client.delete(*keys_to_delete)
            if verbose:
                print(f"Cleaned up {deleted} test session(s) from Redis")
    except Exception as e:
        # Don't fail tests if cleanup fails
        if verbose:
            print(f"Warning: Failed to cleanup test sessions: {e}")


def run_all_scenarios(
    scenarios: List[Dict[str, Any]],
    customer_details: Dict[str, Optional[Any]],
    verbose: bool = False
) -> tuple:
    """
    Run all scenarios and return statistics.

    Args:
        scenarios: List of scenario dicts
        customer_details: Customer details
        verbose: Verbose output

    Returns:
        Tuple of (passed_count, failed_count, skipped_count, failures_list)
    """
    # Generate unique run_id for this test run to ensure session isolation
    run_id = str(uuid.uuid4())[:8]  # Short UUID for readability

    # Clean up any leftover test sessions from previous runs
    cleanup_test_sessions(verbose=verbose)

    passed = 0
    failed = 0
    skipped = 0
    failures = []
    failing_scenario_names = []

    # Use sequential numbering (1-based) instead of scenario's "id" field
    for index, scenario in enumerate(scenarios, start=1):
        scenario_name = scenario.get("name", f"scenario_{index}")
        # Use sequential index for scenario_id (for user_id generation)
        scenario_id = index

        success, error_msg, user_id = test_scenario(
            scenario, scenario_id, customer_details, verbose, run_id=run_id)

        if success:
            passed += 1
        else:
            failed += 1
            failures.append(
                (scenario_id, error_msg or "Unknown error", user_id))
            failing_scenario_names.append(scenario_name)

    return passed, failed, skipped, failures, failing_scenario_names


class TeeOutput:
    """Write to both file and stdout."""

    def __init__(self, file_path, verbose=True):
        self.file = open(file_path, 'w', encoding='utf-8')
        self.stdout = sys.stdout
        self.verbose = verbose

    def write(self, text):
        self.file.write(text)
        if self.verbose:
            self.stdout.write(text)
        self.file.flush()
        if self.verbose:
            self.stdout.flush()

    def flush(self):
        self.file.flush()
        if self.verbose:
            self.stdout.flush()

    def close(self):
        if self.file:
            self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Core Session Follow-up Test Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m core.tests.session.test_session
  python -m core.tests.session.test_session 22
  python -m core.tests.session.test_session 22,24
  python -m core.tests.session.test_session 30-33
  python -m core.tests.session.test_session 22,24,30-33
  python -m core.tests.session.test_session --v -o result.txt
        """
    )
    parser.add_argument(
        "scenarios",
        nargs="*",
        help="Scenario IDs to run (single, comma-separated, or range like 30-33)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        help="Save output to file"
    )

    args = parser.parse_args()

    # Set up output redirection if -o is provided
    output_file = args.output
    original_stdout = sys.stdout
    exit_code = 0

    # Use context manager for file output
    if output_file:
        tee = TeeOutput(output_file, verbose=args.verbose)
        sys.stdout = tee
    else:
        tee = None

    try:
        # Parse scenario IDs
        scenario_ids = parse_scenario_args(args.scenarios)

        # Combine all scenarios
        all_scenarios = followup_scenarios + planning_edges_scenarios

        # Filter scenarios
        scenarios_to_run = filter_scenarios_by_id(
            all_scenarios, scenario_ids)

        if not scenarios_to_run:
            print("No scenarios to run!")
            exit_code = 1
        else:
            # Get customer details
            customer_details = get_customer_details()

            # Print header
            if not args.verbose:
                scenarios_count = len(scenarios_to_run)
                print(
                    f"Running session follow-up tests ({scenarios_count} scenario{'s' if scenarios_count != 1 else ''})...")
            else:
                print("="*70)
                print("CORE SESSION FOLLOW-UP TEST SUITE")
                print("="*70)
                print(
                    f"Total scenarios: {len(all_scenarios)} (followup: {len(followup_scenarios)}, planning_edges: {len(planning_edges_scenarios)})")
                if len(scenarios_to_run) != len(all_scenarios):
                    print(
                        f"Running: {len(scenarios_to_run)} scenario{'s' if len(scenarios_to_run) != 1 else ''}")

            # Run scenarios
            passed, failed, skipped, failures, failing_scenario_names = run_all_scenarios(
                scenarios_to_run,
                customer_details,
                verbose=args.verbose
            )

            # Print summary
            if args.verbose:
                print("\n" + "="*70)
                print("TEST SUMMARY")
                print("="*70)
            else:
                print()

            print(
                f"Total: {len(scenarios_to_run)} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")

            # Print final summary: TOTAL FAILURES and failing scenario names with IDs
            if failures:
                print(f"\nTOTAL FAILURES: {failed}")
                if failing_scenario_names:
                    print("Failing scenarios:")
                    for i, scenario_name in enumerate(failing_scenario_names):
                        if i < len(failures) and len(failures[i]) >= 3:
                            scenario_id = failures[i][0]
                            user_id = failures[i][2]
                            print(
                                f"  - Scenario {scenario_id}: {scenario_name} (id: {user_id})")
                        else:
                            print(f"  - {scenario_name}")

            if args.verbose:
                print("="*70)

            exit_code = 1 if failed > 0 else 0
    finally:
        # Restore stdout and close file
        if tee:
            sys.stdout = original_stdout
            tee.close()
            if not args.verbose and exit_code == 0:
                print(f"Output saved to: {output_file}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
