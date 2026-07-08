"""
Core Planning Test Suite

Tests planning functionality across multi-turn conversations.
Validates planning outputs: status, plan.stage, plan.action, slots, missing_slots.

Note: Uses session management as a mechanism to enable multi-turn testing,
but the tests themselves validate planning behavior, not session management.

Usage:
    python -m core.tests.planning.test_planning              # Run all scenarios (14)
    python -m core.tests.planning.test_planning 4           # Run scenario 4
    python -m core.tests.planning.test_planning 4,9         # Run scenarios 4 and 9
    python -m core.tests.planning.test_planning 20-23       # Run scenarios 20-23

    python -m core.tests.planning.test_planning -o out.out   # Test output only (recommended)
    python -m core.tests.planning.test_planning > out.out 2>&1  # Shell redirect (stdout+stderr)

Scenarios are defined in planning_scenarios.py (requires NLU on localhost:9002).
"""

import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from core.orchestration.api.session_merge import build_session_state_from_outcome
from core.orchestration.cache.catalog_cache import catalog_cache
from core.orchestration.orchestrator import handle_message
from core.orchestration.persistence.durable_intents import is_durable_intent
from core.orchestration.session import clear_session, get_session, save_session
from core.tests.harness.clients import TestCatalogClient, TestLumaClient
from core.tests.harness.org_setup import get_customer_details, setup_test_org_domain
from core.tests.planning.adapter import normalize_planning_outcome
from core.tests.planning.planning_scenarios import planning_scenarios

# Pytest support (optional - allows running with pytest)
try:
    import pytest

    PYTEST_AVAILABLE = True
except ImportError:
    PYTEST_AVAILABLE = False
    # Create a dummy pytest module for when pytest is not available

    class DummyPytest:
        @staticmethod
        def mark(*args, **kwargs):
            return lambda f: f

        @staticmethod
        def fixture(*args, **kwargs):
            return lambda f: f

    pytest = DummyPytest()

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


def configure_test_logging(verbose: bool = False) -> None:
    """Silence orchestrator trace logs during E2E runs unless --verbose.

    Core uses verbose orchestrator logs at debug level; Decision Trace is the
    primary debugging tool. configure_test_logging quiets stderr during E2E runs.
    """
    level = logging.INFO if verbose else logging.CRITICAL
    logging.basicConfig(level=level, format="%(message)s", force=True)
    for logger_name in (
        "core",
        "core.planning",
        "core.orchestration",
        "core.turn_log",
        "httpx",
        "httpcore",
    ):
        logging.getLogger(logger_name).setLevel(level)


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
        if "," in arg:
            # Comma-separated IDs
            for part in arg.split(","):
                part = part.strip()
                if "-" in part:
                    # Range within comma-separated
                    start, end = map(int, part.split("-"))
                    scenario_ids.update(range(start, end + 1))
                else:
                    scenario_ids.add(int(part))
        elif "-" in arg:
            # Range
            start, end = map(int, arg.split("-"))
            scenario_ids.update(range(start, end + 1))
        else:
            # Single ID
            scenario_ids.add(int(arg))

    return scenario_ids


def filter_scenarios_by_id(
    scenarios: List[Dict[str, Any]], scenario_ids: Set[int]
) -> List[Dict[str, Any]]:
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


def _compact_planning_state(state: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Extract planning-relevant fields from session or merged Luma response."""
    if not state or not isinstance(state, dict):
        return None

    intent = state.get("intent_name")
    if not intent:
        raw_intent = state.get("intent")
        if isinstance(raw_intent, dict):
            intent = raw_intent.get("name")
        elif isinstance(raw_intent, str):
            intent = raw_intent
    if not intent:
        intent = state.get("_effective_intent")

    slots = state.get("slots")
    if not isinstance(slots, dict):
        facts = state.get("facts")
        slots = facts.get("slots") if isinstance(facts, dict) else {}
    if not isinstance(slots, dict):
        slots = {}

    missing = state.get("missing_slots")
    if not isinstance(missing, list):
        facts = state.get("facts")
        missing = facts.get("missing_slots") if isinstance(facts, dict) else []

    compact: Dict[str, Any] = {
        "intent": intent,
        "status": state.get("status"),
        "missing_slots": missing or [],
        "slots": slots,
    }

    for key in ("service_candidates", "date_proposal", "time_proposal", "_source_text"):
        val = state.get(key)
        if val is None and isinstance(state.get("facts"), dict):
            val = state["facts"].get(key)
        if val is not None:
            compact[key] = val

    dropped = state.get("_intentionally_dropped_slots")
    if dropped:
        compact["_intentionally_dropped_slots"] = (
            sorted(dropped) if isinstance(dropped, set) else dropped
        )

    facts = state.get("facts")
    if isinstance(facts, dict) and facts.get("service_id") is not None:
        compact["facts_service_id"] = facts.get("service_id")

    return compact


def _compact_got_from_normalized(normalized: Dict[str, Any]) -> Dict[str, Any]:
    """Planning outcome fields for failure diffs."""
    got: Dict[str, Any] = {
        "intent": normalized.get("intent"),
        "status": normalized.get("status"),
        "missing_slots": normalized.get("missing_slots", []),
        "slots": normalized.get("slots", {}),
        "plan": normalized.get("plan", {}),
    }
    for key in ("date_proposal", "time_proposal"):
        if normalized.get(key) is not None:
            got[key] = normalized[key]
    return got


def _print_failure(
    scenario_name: str,
    turn_label: str,
    user_id: str,
    error_msg: str,
    *,
    expected: Any,
    got: Any,
    session_before: Optional[Dict[str, Any]] = None,
    session_after: Optional[Dict[str, Any]] = None,
    merged: Optional[Dict[str, Any]] = None,
    verbose: bool = False,
    full_snapshot: Optional[Dict[str, Any]] = None,
) -> None:
    """Print a concise failure report; full dumps only with verbose."""
    print(f"\nFAIL [{scenario_name}] turn={turn_label}: {error_msg}")
    compact = {
        "expected": expected,
        "got": got,
    }
    before = _compact_planning_state(session_before)
    after = _compact_planning_state(session_after)
    merge = _compact_planning_state(merged)
    if before:
        compact["session_before"] = before
    if after:
        compact["session_after"] = after
    if merge:
        compact["merge"] = merge
    print(json.dumps(compact, indent=2, default=str))
    if verbose and full_snapshot is not None:
        print("\n--- verbose full snapshot ---")
        print(json.dumps(full_snapshot, indent=2, default=str))


def assert_turn_expectations(
    result: Dict[str, Any],
    expected: Dict[str, Any],
    turn_index: int,
    session_state: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Assert turn result matches expectations.

    Validates:
    - intent_name
    - status (READY / NEEDS_CLARIFICATION)
    - plan.stage
    - plan.action
    - missing_slots
    - slots (partial match)

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

    # Normalize CoreOutcome to stable planning view
    try:
        normalized = normalize_planning_outcome(result)
    except Exception as e:
        return f"Turn {turn_index + 1} failed: adapter error: {str(e)}"

    # Assert intent if provided
    expected_intent = expected.get("intent")
    if expected_intent:
        actual_intent = normalized.get("intent")
        if actual_intent != expected_intent:
            return f"Turn {turn_index + 1} intent mismatch: expected {expected_intent}, got {actual_intent}"

    # Assert stage if provided (from plan.stage)
    expected_plan = expected.get("plan", {})
    if not isinstance(expected_plan, dict):
        expected_plan = {}
    # Support both nested "plan" structure and legacy top-level "stage"/"action" for backward compatibility
    expected_stage = expected_plan.get("stage") or expected.get("stage")
    if expected_stage:
        actual_stage = normalized.get("plan", {}).get("stage")
        if actual_stage != expected_stage:
            return f"Turn {turn_index + 1} stage mismatch: expected {expected_stage}, got {actual_stage}"

    # Assert action if provided (from plan.action)
    expected_action = expected_plan.get("action") or expected.get("action")
    if expected_action:
        actual_action = normalized.get("plan", {}).get("action")
        if actual_action != expected_action:
            return f"Turn {turn_index + 1} action mismatch: expected {expected_action}, got {actual_action}"

    # Assert missing_slots (exact match, order-insensitive)
    # INVARIANT: missing_slots order does not matter
    expected_missing = expected.get("missing_slots")
    if expected_missing is not None:
        actual_missing = normalized.get("missing_slots", [])
        if not isinstance(actual_missing, list):
            actual_missing = []

        # Compare as sets for order-insensitive match
        if set(actual_missing) != set(expected_missing):
            return f"Turn {turn_index + 1} missing_slots mismatch: expected {expected_missing}, got {actual_missing}"

        # INVARIANT: Verify missing_slots is sorted (planner returns sorted list)
        # This ensures deterministic behavior while order doesn't matter for comparison
        # Exception: If awaiting_slot is present and matches actual_missing[0], allow it at index 0
        # and verify the rest (actual_missing[1:]) is sorted
        if len(actual_missing) > 0:
            awaiting_slot = None
            if session_state and isinstance(session_state, dict):
                awaiting_slot = session_state.get("awaiting_slot")

            if awaiting_slot is not None and actual_missing[0] == awaiting_slot:
                # awaiting_slot is at index 0 - verify the rest is sorted
                remaining = actual_missing[1:]
                if remaining != sorted(remaining):
                    return f"Turn {turn_index + 1} missing_slots not sorted after awaiting_slot: got {actual_missing} (awaiting_slot='{awaiting_slot}' at index 0, rest should be sorted)"
            else:
                # No awaiting_slot or it's not at index 0 - verify entire list is sorted
                if actual_missing != sorted(actual_missing):
                    return f"Turn {turn_index + 1} missing_slots not sorted: got {actual_missing} (should be sorted for determinism)"

    # Assert slots (partial match only)
    # INVARIANT: Slots can be provided in any order - all slots are additive
    expected_slots = expected.get("slots")
    if expected_slots:
        actual_slots = normalized.get("slots", {})
        if not isinstance(actual_slots, dict):
            actual_slots = {}

        # Partial match: check that expected keys exist in actual
        for key, expected_value in expected_slots.items():
            if key not in actual_slots:
                return f"Turn {turn_index + 1} missing slot: {key}"
            # For other values, do exact match
            if actual_slots[key] != expected_value:
                return f"Turn {turn_index + 1} slot {key} mismatch: expected {expected_value}, got {actual_slots[key]}"

    for proposal_key in ("date_proposal", "time_proposal"):
        expected_proposal = expected.get(proposal_key)
        if expected_proposal is not None:
            actual_proposal = normalized.get(proposal_key)
            if actual_proposal != expected_proposal:
                return (
                    f"Turn {turn_index + 1} {proposal_key} mismatch: "
                    f"expected {expected_proposal}, got {actual_proposal}"
                )

    # Assert status if provided
    # Status contract: READY when executable_with subset is satisfied (e.g. service_id present)
    # OR when missing_slots == []. NEEDS_CLARIFICATION only when no executable_with subset is met.
    expected_status = expected.get("status")
    if expected_status is not None:
        actual_status = normalized.get("status")
        if actual_status != expected_status:
            return f"Turn {turn_index + 1} status mismatch: expected {expected_status}, got {actual_status}"

    return None


def _test_scenario(
    scenario: Dict[str, Any],
    scenario_id: int,
    customer_details: Dict[str, Optional[Any]],
    verbose: bool = False,
    run_id: Optional[str] = None,
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
    setup_test_org_domain(domain)

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
                print(f"\n--- Turn {turn_index + 1}/{len(turns)}: {sentence} ---")
                print(f"Expected: {json.dumps(expected, indent=2)}")

            # Load session state before each turn
            # SESSION LIFECYCLE RULE: Durable intents preserve sessions on READY
            # Load sessions with status NEEDS_CLARIFICATION OR READY (for durable intents)
            session_state = get_session(user_id)
            if session_state:
                session_status = session_state.get("status")
                session_intent_name = session_state.get("intent_name")
                # Only consider session if status is NEEDS_CLARIFICATION, or READY for durable intents
                if session_status != "NEEDS_CLARIFICATION":
                    if session_status == "READY" and session_intent_name:
                        # Check if intent is durable - only preserve READY sessions for durable intents
                        if not is_durable_intent(session_intent_name):
                            session_state = None
                    else:
                        # Not NEEDS_CLARIFICATION and not READY (or no intent_name) - clear it
                        session_state = None

            # Print session state before turn (verbose only)
            if verbose:
                print(f"\n[SESSION BEFORE TURN {turn_index + 1}] user_id={user_id}")
                if session_state:
                    print(
                        f"  {_compact_planning_state(session_state)}"
                    )
                else:
                    print("  (no session)")

            # Call handle_message with the same user_id and session_state
            # Create a session_store wrapper that retrieves sessions dynamically
            # This ensures session continuity across turns - the wrapper will retrieve
            # the latest session state that was saved in previous turns
            class SessionStoreWrapper:
                def __init__(self, user_id):
                    self.user_id = user_id

                def get_session(self, user_id):
                    # Retrieve session dynamically to get the latest saved state
                    # This ensures durable intents can be recovered from session
                    # even when Luma returns UNKNOWN/empty/error on follow-up turns
                    return get_session(user_id)

            # Always create session_store wrapper (even if current session_state is None)
            # The wrapper will dynamically retrieve sessions, allowing durable intent recovery
            session_store = SessionStoreWrapper(user_id)

            result = handle_message(
                text=sentence,
                user_id=user_id,
                luma_client=luma_client,
                organization_client=None,  # Will use default
                session_store=session_store,
            )

            if not result or not isinstance(result, dict):
                error_msg = f"Turn {turn_index + 1} failed: handle_message returned None or not a dict: {result}"
                _print_failure(
                    scenario_name,
                    str(turn_index + 1),
                    user_id,
                    error_msg,
                    expected=expected,
                    got={"error": "handle_message returned None or not a dict", "result": result},
                    session_before=session_state,
                    verbose=verbose,
                )
                return False, error_msg, user_id

            if verbose:
                print(f"\nResult:")
                print(json.dumps(result, indent=2, default=str))

            # Save session after response (same logic as API endpoint)
            # Note: result already validated above
            # Extract outcome from result for session management (needs raw structure)
            outcome = result.get("outcome")
            if not outcome:
                # Try to extract from result or plan
                outcome = result.get("result") or result.get("plan", {})

            if outcome and isinstance(outcome, dict):
                outcome_status = outcome.get("status")
                # If status not in outcome, use normalized status
                if outcome_status is None:
                    normalized = normalize_planning_outcome(result)
                    outcome_status = normalized.get("status")

                # DEBUG: outcome status (verbose only)
                if verbose:
                    print(
                        f"\n[OUTCOME STATUS] Turn {turn_index + 1} outcome_status={outcome_status}"
                    )
                # Initialize new_session_state for all paths
                new_session_state = None
                merged_luma_response = result.get("_merged_luma_response")

                if outcome_status == "NEEDS_CLARIFICATION":
                    # Save session state for follow-up
                    new_session_state = build_session_state_from_outcome(
                        outcome,
                        outcome_status,
                        merged_luma_response,
                        session_state,
                        user_id,
                    )
                    if new_session_state:
                        save_session(user_id, new_session_state)
                        if verbose:
                            print(
                                f"\n[SESSION AFTER TURN {turn_index + 1}] SAVED "
                                f"{_compact_planning_state(new_session_state)}"
                            )
                    elif verbose:
                        print(
                            f"\n[SESSION AFTER TURN {turn_index + 1}] NOT SAVED (new_session_state is None)"
                        )
                elif outcome_status in ("READY", "EXECUTED", "AWAITING_CONFIRMATION"):
                    # For READY status, try to build session state (will be None for non-durable intents)
                    # EXECUTED/AWAITING_CONFIRMATION also try to build (but will return None)
                    # DURABLE INTENT CONTRACT: Durable intents (as defined in intent_policy.yaml) preserve sessions on READY
                    # This allows follow-up modifications (e.g., "make it 4pm" after booking is ready)
                    new_session_state = build_session_state_from_outcome(
                        outcome,
                        outcome_status,
                        merged_luma_response,
                        session_state,
                        user_id,
                    )
                    if new_session_state is None:
                        clear_session(user_id)
                        if verbose:
                            print(
                                f"\n[SESSION AFTER TURN {turn_index + 1}] CLEARED (status={outcome_status})"
                            )
                    else:
                        save_session(user_id, new_session_state)
                        if verbose:
                            print(
                                f"\n[SESSION AFTER TURN {turn_index + 1}] SAVED (status={outcome_status}) "
                                f"{_compact_planning_state(new_session_state)}"
                            )
                elif verbose:
                    print(
                        f"\n[SESSION AFTER TURN {turn_index + 1}] NOT SAVED (status={outcome_status})"
                    )

            # Capture data for failure snapshot after save, before assertions
            session_state_before = session_state
            session_state_after = None
            merged_luma_response_for_snapshot = result.get("_merged_luma_response")

            # Extract outcome for snapshot (use normalized view for consistency)
            normalized_snapshot = normalize_planning_outcome(result)
            plan_for_snapshot = normalized_snapshot.get("plan", {})
            facts_for_snapshot = {
                "slots": normalized_snapshot.get("slots", {}),
                "missing_slots": normalized_snapshot.get("missing_slots", []),
            }

            # Session state after this turn (for failure diagnostics)
            session_state_after = get_session(user_id)

            # Assert expectations
            error_msg = assert_turn_expectations(
                result, expected, turn_index, session_state_before
            )
            if error_msg:
                full_snapshot = {
                    "expected": expected,
                    "got": _compact_got_from_normalized(normalized_snapshot),
                    "session_before": session_state_before,
                    "session_after": session_state_after,
                    "merged_luma_response": merged_luma_response_for_snapshot,
                    "final_plan": plan_for_snapshot,
                    "facts": facts_for_snapshot,
                }
                _print_failure(
                    scenario_name,
                    str(turn_index + 1),
                    user_id,
                    error_msg,
                    expected=expected,
                    got=_compact_got_from_normalized(normalized_snapshot),
                    session_before=session_state_before,
                    session_after=session_state_after,
                    merged=merged_luma_response_for_snapshot,
                    verbose=verbose,
                    full_snapshot=full_snapshot,
                )
                return False, error_msg, user_id

        # SESSION LIFECYCLE RULE: Deterministic session clearing check
        # DURABLE INTENT CONTRACT: Durable intents (as defined in intent_policy.yaml) preserve sessions on READY
        # This allows follow-up modifications (e.g., "make it 4pm" after booking is ready)
        # Ephemeral intents clear sessions on READY (terminal state)
        final_expected = turns[-1].get("expected", {})
        final_missing_slots = final_expected.get("missing_slots", [])
        final_intent = final_expected.get("intent", "")
        final_status = final_expected.get("status")

        # RULE: Session should be cleared when missing_slots is empty for non-durable intents
        # Durable intents preserve session on READY (allows follow-up modifications)
        # HANDLER_DELEGATED turns do not persist session — skip lifecycle check
        if final_missing_slots == [] and final_status != "HANDLER_DELEGATED":
            session_state = get_session(user_id)
            # Check session intent_name (session stores intent_name, not intent)
            # Also check expected intent from test scenario
            session_intent_name = None
            if session_state:
                # Session stores intent_name as a string
                session_intent_name = session_state.get("intent_name")

            # Determine if the intent is durable (from intent_policy.yaml)
            # Check both final_intent (from expected) and session_intent_name (from actual session)
            intent_to_check = session_intent_name or final_intent
            is_durable = False
            if intent_to_check:
                is_durable = is_durable_intent(intent_to_check)

            # DURABLE INTENT RULE: Durable intents preserve session on READY
            # Verify session is preserved with correct state
            if is_durable:
                if session_state is None:
                    error_msg = f"Durable intent '{intent_to_check}' session was cleared but should be preserved on READY"
                    _print_failure(
                        scenario_name,
                        "FINAL",
                        user_id,
                        error_msg,
                        expected={
                            "missing_slots": [],
                            "session_cleared": False,
                            "intent_name": intent_to_check,
                            "status": "READY",
                        },
                        got={"missing_slots": [], "session_cleared": True},
                        verbose=verbose,
                    )
                    return False, error_msg, user_id

                # Verify session state is correct for durable intent
                if session_state.get("intent_name") != intent_to_check:
                    error_msg = f"Durable intent session has wrong intent_name: expected '{intent_to_check}', got '{session_state.get('intent_name')}'"
                    _print_failure(
                        scenario_name,
                        "FINAL",
                        user_id,
                        error_msg,
                        expected={
                            "missing_slots": [],
                            "intent_name": intent_to_check,
                            "status": "READY",
                        },
                        got=_compact_planning_state(session_state) or {},
                        session_after=session_state,
                        verbose=verbose,
                    )
                    return False, error_msg, user_id

                if session_state.get("status") != "READY":
                    error_msg = f"Durable intent session has wrong status: expected 'READY', got '{session_state.get('status')}'"
                    _print_failure(
                        scenario_name,
                        "FINAL",
                        user_id,
                        error_msg,
                        expected={"missing_slots": [], "status": "READY"},
                        got=_compact_planning_state(session_state) or {},
                        session_after=session_state,
                        verbose=verbose,
                    )
                    return False, error_msg, user_id
            else:
                # EPHEMERAL INTENT RULE: Ephemeral intents clear session on READY
                if session_state is not None:
                    error_msg = "Session not cleared after planning complete (missing_slots=[])"
                    _print_failure(
                        scenario_name,
                        "FINAL",
                        user_id,
                        error_msg,
                        expected={"missing_slots": [], "session_cleared": True},
                        got={"session_cleared": False},
                        session_after=session_state,
                        verbose=verbose,
                        full_snapshot={"session_state": session_state} if verbose else None,
                    )
                    return False, error_msg, user_id

        if verbose:
            print(f"\n[OK] Scenario {scenario_id} passed")

        return True, None, user_id

    except (AssertionError, Exception) as e:
        session_state_before = None
        try:
            session_state_before = get_session(user_id)
        except Exception:
            pass

        import traceback

        tb = traceback.format_exc()
        error_msg = f"Exception in scenario {scenario_id}: {str(e)}"
        _print_failure(
            scenario_name,
            "EXCEPTION",
            user_id,
            str(e),
            expected="(exception — see error message)",
            got={"error": str(e)},
            session_before=session_state_before,
            verbose=verbose,
        )
        if verbose:
            print(tb)
        return False, f"{error_msg}\n{tb}", user_id
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

        from core.orchestration.session.session_manager import (
            SESSION_KEY_PREFIX,
            _get_redis_url,
        )

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
    verbose: bool = False,
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

        success, error_msg, user_id = _test_scenario(
            scenario, scenario_id, customer_details, verbose, run_id=run_id
        )

        if success:
            passed += 1
        else:
            failed += 1
            failures.append((scenario_id, error_msg or "Unknown error", user_id))
            failing_scenario_names.append(scenario_name)

    return passed, failed, skipped, failures, failing_scenario_names


class TeeOutput:
    """Write to both file and stdout."""

    def __init__(self, file_path, verbose=True):
        self.file = open(file_path, "w", encoding="utf-8")
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


# ============================================================================
# Pytest Test Functions (Hybrid Approach)
# ============================================================================
# These pytest test functions allow running tests with pytest while maintaining
# backward compatibility with the existing CLI interface.
#
# Usage with pytest:
#   pytest core/tests/planning/test_planning.py                    # Run all scenarios
#   pytest core/tests/planning/test_planning.py -k "scenario_22"    # Run specific scenario
#   pytest core/tests/planning/test_planning.py -v                  # Verbose output
#   pytest core/tests/planning/test_planning.py -x                  # Stop on first failure
#
# Usage with original CLI (still works):
#   python -m core.tests.planning.test_planning                     # Run all scenarios
#   python -m core.tests.planning.test_planning 22,24              # Run specific scenarios
# ============================================================================

if PYTEST_AVAILABLE:

    @pytest.fixture(scope="session")
    def customer_details_fixture():
        """Pytest fixture for customer details (session-scoped)."""
        return get_customer_details()

    @pytest.fixture(scope="session")
    def all_scenarios_fixture():
        """Pytest fixture for all scenarios (session-scoped)."""
        return planning_scenarios

    @pytest.fixture(scope="function", autouse=True)
    def cleanup_sessions():
        """Auto-cleanup sessions before each test."""
        yield
        # Cleanup happens in _test_scenario's finally block

    # Generate test parameters from all scenarios
    def _generate_scenario_params():
        """Generate pytest parameters from scenarios."""
        all_scenarios = planning_scenarios
        params = []
        for index, scenario in enumerate(all_scenarios, start=1):
            scenario_name = scenario.get("name", f"scenario_{index}")
            params.append(
                pytest.param(
                    scenario, index, id=f"scenario_{index:03d}_{scenario_name}"
                )
            )
        return params

    @pytest.mark.parametrize("scenario,scenario_id", _generate_scenario_params())
    def test_scenario_pytest(scenario, scenario_id, customer_details_fixture):
        """
        Pytest test function for each scenario.

        This allows running tests with pytest while maintaining backward compatibility
        with the existing CLI interface.
        """
        # Generate unique run_id for pytest runs
        run_id = f"pytest_{int(time.time())}"

        # Run the scenario test
        success, error_msg, _user_id = _test_scenario(
            scenario=scenario,
            scenario_id=scenario_id,
            customer_details=customer_details_fixture,
            verbose=False,  # Pytest handles verbose output
            run_id=run_id,
        )

        # Assert success (pytest will handle the failure reporting)
        if not success:
            pytest.fail(f"Scenario {scenario_id} failed: {error_msg}")


def main():
    """Main entry point for CLI interface."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Core Planning Test Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m core.tests.planning.test_planning
  python -m core.tests.planning.test_planning 22
  python -m core.tests.planning.test_planning 22,24
  python -m core.tests.planning.test_planning 30-33
  python -m core.tests.planning.test_planning 22,24,30-33
  python -m core.tests.planning.test_planning --v -o result.txt
        """,
    )
    parser.add_argument(
        "scenarios",
        nargs="*",
        help="Scenario IDs to run (single, comma-separated, or range like 30-33)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output (full session dumps and failure snapshots)",
    )
    parser.add_argument("-o", "--output", type=str, help="Save output to file")

    args = parser.parse_args()

    configure_test_logging(verbose=args.verbose)

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
        all_scenarios = planning_scenarios

        # Filter scenarios
        scenarios_to_run = filter_scenarios_by_id(all_scenarios, scenario_ids)

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
                    f"Running planning tests ({scenarios_count} scenario{'s' if scenarios_count != 1 else ''})..."
                )
            else:
                print("=" * 70)
                print("CORE PLANNING TEST SUITE")
                print("=" * 70)
                print(
                    f"Total scenarios: {len(all_scenarios)}"
                )
                if len(scenarios_to_run) != len(all_scenarios):
                    print(
                        f"Running: {len(scenarios_to_run)} scenario{'s' if len(scenarios_to_run) != 1 else ''}"
                    )

            # Run scenarios
            passed, failed, skipped, failures, failing_scenario_names = (
                run_all_scenarios(
                    scenarios_to_run, customer_details, verbose=args.verbose
                )
            )

            # Print summary
            if args.verbose:
                print("\n" + "=" * 70)
                print("TEST SUMMARY")
                print("=" * 70)
            else:
                print()

            print(
                f"Total: {len(scenarios_to_run)} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}"
            )

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
                                f"  - Scenario {scenario_id}: {scenario_name} (id: {user_id})"
                            )
                        else:
                            print(f"  - {scenario_name}")

            if args.verbose:
                print("=" * 70)

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
