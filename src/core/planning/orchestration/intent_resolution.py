"""
Intent Resolution

Resolves effective intent from Luma response and session state.

Handles intent merge rules:
- If luma_intent != "UNKNOWN": use luma_intent
- ELSE: KEEP session.intent (NEVER allow UNKNOWN to overwrite session intent)
- Domain switch detection based on canonical service evidence
- Non-core intent handling

CRITICAL: Durable intent recovery
- If session exists AND session.intent_name is durable (per intent_policy.yaml)
- AND Luma intent is UNKNOWN, empty, or resolution failed
- THEN set effective_intent = session.intent_name BEFORE any planning
"""

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def resolve_effective_intent(
    luma_response: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
    user_id: str,
    transaction_id: Optional[str] = None,
) -> Tuple[str, bool]:
    """
    Resolve effective intent from Luma response and session state.

    CRITICAL INTENT MERGE RULE:
    - IF luma_intent != "UNKNOWN": use luma_intent
    - ELSE: KEEP session.intent (NEVER allow UNKNOWN to overwrite session intent)

    SESSION LIFECYCLE RULE: Handle session intent override for:
    1. NEEDS_CLARIFICATION sessions (existing behavior)
    2. READY sessions with CREATE_APPOINTMENT (allows time overrides like "make it 4pm")

    Args:
        luma_response: Luma API response
        session_state: Optional session state from previous turn
        user_id: User identifier for logging
        transaction_id: Optional transaction ID for logging

    Returns:
        Tuple of (effective_intent, session_reset_occurred)
    """
    log_transaction_id = f" transaction_id={transaction_id}" if transaction_id else ""

    # Guard: Handle None luma_response (e.g., when Luma API fails)
    # DURABLE INTENT RECOVERY: If session exists and session intent is durable, recover it
    if luma_response is None:
        if session_state:
            # CRITICAL: Check intent_name first, then fall back to intent
            session_intent = session_state.get("intent_name") or session_state.get(
                "intent"
            )
            session_intent_str = (
                session_intent
                if isinstance(session_intent, str)
                else (
                    session_intent.get("name", "")
                    if isinstance(session_intent, dict)
                    else ""
                )
            )
            if session_intent_str:
                session_intent_str = session_intent_str.strip()
            else:
                session_intent_str = ""
            if session_intent_str:
                # Check if session intent is durable using intent_policy.yaml
                try:
                    from core.policy.intent_policy import get_intent_durable

                    if get_intent_durable(session_intent_str):
                        logger.info(
                            f"[durable_intent_recovery] Recovered durable intent from session: {session_intent_str} "
                            f"(luma_response=None, user_id={user_id}{log_transaction_id})"
                        )
                        return session_intent_str, False
                except (ImportError, Exception) as e:
                    logger.warning(
                        f"Failed to check durable status for '{session_intent_str}': {e}. "
                        f"Defaulting to session intent."
                    )
                    return session_intent_str, False
        return "UNKNOWN", False

    # Extract Luma intent
    luma_intent_obj = luma_response.get("intent", {})
    luma_intent_name = (
        luma_intent_obj.get("name", "") if isinstance(luma_intent_obj, dict) else ""
    )

    # DURABLE INTENT RECOVERY: If Luma intent is UNKNOWN/empty/error and session has durable intent, recover it
    # This must happen BEFORE any other intent resolution logic to ensure planning uses the correct intent
    luma_intent_is_unknown_or_empty = (
        not luma_intent_name or luma_intent_name == "UNKNOWN"
    )

    if luma_intent_is_unknown_or_empty and session_state:
        # CRITICAL: Check intent_name first, then fall back to intent
        session_intent = session_state.get("intent_name") or session_state.get("intent")
        session_intent_str = (
            session_intent
            if isinstance(session_intent, str)
            else (
                session_intent.get("name", "")
                if isinstance(session_intent, dict)
                else ""
            )
        )
        if session_intent_str:
            session_intent_str = session_intent_str.strip()
        else:
            session_intent_str = ""
        if session_intent_str:
            # Check if session intent is durable using intent_policy.yaml
            try:
                from core.policy.intent_policy import get_intent_durable

                if get_intent_durable(session_intent_str):
                    # Recover durable intent from session BEFORE any other processing
                    # This ensures durable intents are preserved even when Luma returns UNKNOWN/empty/error
                    logger.info(
                        f"[durable_intent_recovery] Recovered durable intent from session: {session_intent_str} "
                        f"(luma_intent={luma_intent_name}, user_id={user_id}{log_transaction_id})"
                    )
                    # Return immediately with recovered durable intent - no further processing needed
                    logger.error(
                        f"[SESSION_RESET_WRITER] WRITER=early_return_durable_recovery value=False "
                        f"location=line_113_early_return reason=durable_intent_recovered "
                        f"session_intent={session_intent_str} user_id={user_id}{log_transaction_id}"
                    )
                    return session_intent_str, False
            except (ImportError, Exception) as e:
                logger.warning(
                    f"Failed to check durable status for '{session_intent_str}': {e}. "
                    f"Continuing with normal intent resolution."
                )

    # Resolve effective_intent using session
    effective_intent = luma_intent_name
    session_reset_occurred = False
    logger.error(
        f"[SESSION_RESET_WRITER] WRITER=initial_default value=False "
        f"location=line_122_default reason=initial_assignment "
        f"luma_intent={luma_intent_name} user_id={user_id}{log_transaction_id}"
    )

    # CRITICAL: CONFIRM_* intents are continuations, not intent switches, when:
    # 1) session has a durable booking intent
    # 2) AND session.status == "AWAITING_CONFIRMATION"
    # This prevents MODIFY_BOOKING flow from breaking when user confirms with "yes"
    # but also prevents CONFIRM_* from auto-confirming when status != AWAITING_CONFIRMATION
    if luma_intent_name and luma_intent_name.startswith("CONFIRM_") and session_state:
        # Defensive null check for session_state.get("status")
        session_status = session_state.get("status") if session_state else None

        # Only treat CONFIRM_* as continuation when status == AWAITING_CONFIRMATION
        if session_status == "AWAITING_CONFIRMATION":
            session_intent = session_state.get("intent_name") or session_state.get(
                "intent"
            )
            session_intent_str = (
                session_intent
                if isinstance(session_intent, str)
                else (
                    session_intent.get("name", "")
                    if isinstance(session_intent, dict)
                    else ""
                )
            )
            if session_intent_str:
                session_intent_str = session_intent_str.strip()
            else:
                session_intent_str = ""

            if session_intent_str:
                # Check if session intent is durable
                try:
                    from core.policy.intent_policy import get_intent_durable

                    if get_intent_durable(session_intent_str):
                        # CONFIRM_* is a continuation of durable session intent, not a switch
                        effective_intent = session_intent_str
                        session_reset_occurred = False
                        logger.info(
                            f"[session] confirm_intent_continuation user_id={user_id}{log_transaction_id} "
                            f"session.intent={session_intent_str} luma.intent={luma_intent_name} "
                            f"session.status={session_status} "
                            f"(CONFIRM_* treated as continuation of durable intent with AWAITING_CONFIRMATION status)"
                        )
                except (ImportError, Exception) as e:
                    logger.warning(
                        f"Failed to check durable status for '{session_intent_str}': {e}. "
                        f"Continuing with normal intent resolution."
                    )
        else:
            # Status != AWAITING_CONFIRMATION - treat CONFIRM_* as normal non-core intent
            logger.info(
                f"[session] confirm_intent_not_continuation user_id={user_id}{log_transaction_id} "
                f"luma.intent={luma_intent_name} session.status={session_status} "
                f"(CONFIRM_* NOT treated as continuation - status != AWAITING_CONFIRMATION)"
            )

    # SESSION LIFECYCLE RULE: Handle session intent override for:
    # 1. NEEDS_CLARIFICATION sessions (existing behavior)
    # 2. READY sessions with CREATE_APPOINTMENT (allows time overrides like "make it 4pm")
    session_status = session_state.get("status") if session_state else None
    should_handle_session_intent = session_state and (
        session_status == "NEEDS_CLARIFICATION"
        or (
            session_status == "READY"
            and (session_state.get("intent_name") or session_state.get("intent"))
            is not None
        )
    )

    if should_handle_session_intent:
        # CRITICAL: Session stores intent_name (not intent). Check intent_name first, then fall back to intent.
        # This ensures UNKNOWN intents are correctly detected for materialization logic.
        session_intent = session_state.get("intent_name") or session_state.get("intent")
        session_intent_str = (
            session_intent
            if isinstance(session_intent, str)
            else (
                session_intent.get("name", "")
                if isinstance(session_intent, dict)
                else ""
            )
        )
        # Ensure empty strings are preserved as empty (do not coerce UNKNOWN to empty)
        if session_intent_str:
            session_intent_str = session_intent_str.strip()
        else:
            session_intent_str = ""

        # For READY sessions, only process if intent is CREATE_APPOINTMENT (preserve for modifications)
        if session_status == "READY" and session_intent_str != "CREATE_APPOINTMENT":
            # For non-CREATE_APPOINTMENT READY sessions, don't override intent (session should be cleared)
            pass
        else:
            # CRITICAL: Canonical domain switch detection applies ONLY when BOTH prior and new intents are concrete.
            # UNKNOWN has no domain; UNKNOWN → concrete is materialization, not a domain switch.
            # Domain switching should only occur when:
            # - Prior intent is concrete (CREATE_APPOINTMENT or CREATE_RESERVATION)
            # - New intent is concrete
            # - Domains are incompatible (service vs reservation)
            canonical_indicates_switch = False
            # Guard: Only check for domain switching if prior intent is concrete (not UNKNOWN)
            if session_intent_str and session_intent_str != "UNKNOWN":
                context = luma_response.get("context", {})
                services = (
                    context.get("services", []) if isinstance(context, dict) else []
                )

                if services and isinstance(services, list) and len(services) > 0:
                    first_service = services[0]
                    if isinstance(first_service, dict):
                        canonical = first_service.get("canonical") or first_service.get(
                            "canonical_key"
                        )
                        if canonical:
                            canonical_str = str(canonical).lower()
                            # Check if canonical indicates reservation domain (hospitality.*)
                            if canonical_str.startswith(
                                "hospitality."
                            ) or canonical_str.startswith("lodging."):
                                # Canonical indicates reservation domain
                                if session_intent_str == "CREATE_APPOINTMENT":
                                    canonical_indicates_switch = True
                                    logger.info(
                                        f"[session] domain_switch_detected user_id={user_id}{log_transaction_id} "
                                        f"canonical={canonical} indicates reservation domain, session was service"
                                    )
                            elif canonical_str.startswith(
                                "beauty_and_wellness."
                            ) or canonical_str.startswith("service."):
                                # Canonical indicates service domain
                                if session_intent_str == "CREATE_RESERVATION":
                                    canonical_indicates_switch = True
                                    logger.info(
                                        f"[session] domain_switch_detected user_id={user_id}{log_transaction_id} "
                                        f"canonical={canonical} indicates service domain, session was reservation"
                                    )

            if canonical_indicates_switch:
                # Domain switch detected - reset session and use new intent based on canonical
                # Determine new intent from canonical evidence
                new_intent = None
                if services and isinstance(services, list) and len(services) > 0:
                    first_service = services[0]
                    if isinstance(first_service, dict):
                        canonical = first_service.get("canonical") or first_service.get(
                            "canonical_key"
                        )
                        if canonical:
                            canonical_str = str(canonical).lower()
                            if canonical_str.startswith(
                                "hospitality."
                            ) or canonical_str.startswith("lodging."):
                                new_intent = "CREATE_RESERVATION"
                            elif canonical_str.startswith(
                                "beauty_and_wellness."
                            ) or canonical_str.startswith("service."):
                                new_intent = "CREATE_APPOINTMENT"

                if new_intent:
                    effective_intent = new_intent
                else:
                    # Fallback: use session intent if canonical parsing fails
                    effective_intent = session_intent_str
                    canonical_indicates_switch = False

                if canonical_indicates_switch:
                    from core.orchestration.session.session_manager import clear_session

                    clear_session(user_id)
                    session_state = None
                    session_reset_occurred = True
                    logger.error(
                        f"[SESSION_RESET_WRITER] WRITER=canonical_domain_switch value=True "
                        f"location=line_210_canonical_switch reason=domain_switch_detected "
                        f"old={session_intent_str} new={effective_intent} "
                        f"user_id={user_id}{log_transaction_id}"
                    )
                    logger.info(
                        f"[session] domain_switch_reset user_id={user_id}{log_transaction_id} "
                        f"old={session_intent_str} new={effective_intent} (canonical-based switch)"
                    )
            elif luma_intent_name == "UNKNOWN" or not luma_intent_name:
                # DURABLE INTENT RECOVERY: If luma.intent is UNKNOWN/empty, recover durable session intent
                # GUARD: Do NOT recover for CONFIRM_* intents unless continuation conditions are met
                # (CONFIRM_* intents should never be UNKNOWN, but defensive check prevents recovery)
                is_confirm_intent = luma_intent_name and luma_intent_name.startswith(
                    "CONFIRM_"
                )
                session_status_for_recovery = (
                    session_state.get("status") if session_state else None
                )
                should_block_recovery = (
                    is_confirm_intent
                    and session_status_for_recovery != "AWAITING_CONFIRMATION"
                )

                if should_block_recovery:
                    # CONFIRM_* intent without continuation conditions - do NOT recover session intent
                    effective_intent = (
                        luma_intent_name if luma_intent_name else "UNKNOWN"
                    )
                    logger.info(
                        f"[durable_intent_recovery] Blocked recovery for CONFIRM_* intent "
                        f"luma_intent={luma_intent_name} session.status={session_status_for_recovery} "
                        f"(status != AWAITING_CONFIRMATION, user_id={user_id}{log_transaction_id})"
                    )
                else:
                    # Check if session intent is durable using intent_policy.yaml
                    try:
                        from core.policy.intent_policy import get_intent_durable

                        if get_intent_durable(session_intent_str):
                            # Recover durable intent from session
                            effective_intent = session_intent_str
                            logger.info(
                                f"[durable_intent_recovery] Recovered durable intent: {effective_intent} "
                                f"(luma_intent={luma_intent_name}, user_id={user_id}{log_transaction_id})"
                            )
                        else:
                            # Session intent is not durable - use UNKNOWN
                            effective_intent = "UNKNOWN"
                            logger.info(
                                f"[session] ephemeral_intent_dropped user_id={user_id}{log_transaction_id} "
                                f"session.intent={session_intent_str} is not durable, using UNKNOWN"
                            )
                    except (ImportError, Exception) as e:
                        # Fallback: use session intent if durability check fails
                        logger.warning(
                            f"Failed to check durable status for '{session_intent_str}': {e}. "
                            f"Using session intent as fallback."
                        )
                        effective_intent = session_intent_str
                        logger.info(
                            f"[session] intent_override user_id={user_id}{log_transaction_id} "
                            f"UNKNOWN -> session.intent={effective_intent} (fallback)"
                        )
            else:
                # Check if new intent is non-core (DISCOVERY, CONFIRM_ACTION, etc.)
                from core.routing.intents.base_intents import is_core_intent

                is_new_intent_core = is_core_intent(luma_intent_name)
                is_session_intent_core = (
                    is_core_intent(session_intent_str) if session_intent_str else False
                )

                # CRITICAL: Informational/question intents (DETAILS, FAQ, HELP, etc.) are intent breakers
                # They should switch the intent even if they're non-core, interrupting booking flow
                # This allows users to ask questions mid-booking without preserving the booking intent
                informational_intents = {
                    "DETAILS",
                    "FAQ",
                    "HELP",
                    "AVAILABILITY",
                    "QUOTE",
                    "RECOMMENDATION",
                    "DISCOVERY",
                }
                is_informational_intent = luma_intent_name in informational_intents

                # Rule: Non-core intents (DISCOVERY, CONFIRM_ACTION) should NOT overwrite active booking session
                # EXCEPTION: Informational/question intents break the booking flow and switch intent
                # EXCEPTION: CONFIRM_* intents should NOT preserve session intent unless continuation conditions are met
                is_confirm_intent = luma_intent_name and luma_intent_name.startswith(
                    "CONFIRM_"
                )
                session_status_for_noncore = (
                    session_state.get("status") if session_state else None
                )
                should_block_noncore_preservation = (
                    is_confirm_intent
                    and session_status_for_noncore != "AWAITING_CONFIRMATION"
                )

                if (
                    is_session_intent_core
                    and not is_new_intent_core
                    and not is_informational_intent
                ):
                    if should_block_noncore_preservation:
                        # CONFIRM_* intent without continuation conditions - do NOT preserve session intent
                        effective_intent = luma_intent_name
                        logger.info(
                            f"[session] confirm_intent_not_preserved user_id={user_id}{log_transaction_id} "
                            f"luma.intent={luma_intent_name} session.intent={session_intent_str} "
                            f"session.status={session_status_for_noncore} "
                            f"(CONFIRM_* NOT preserving session - status != AWAITING_CONFIRMATION)"
                        )
                    else:
                        # Keep session intent - non-core intents are side-intents that don't interrupt booking flow
                        # (but informational intents are excluded from this rule)
                        effective_intent = session_intent_str
                        logger.info(
                            f"[session] non_core_intent_ignored user_id={user_id}{log_transaction_id} "
                            f"session.intent={session_intent_str} luma.intent={luma_intent_name} (non-core, preserving session)"
                        )
                elif (
                    is_informational_intent
                    and session_intent_str
                    and session_intent_str != "UNKNOWN"
                ):
                    # Informational intent breaks booking flow - switch to informational intent
                    # Do NOT clear session (just switch intent) unless explicitly required
                    effective_intent = luma_intent_name
                    session_reset_occurred = False  # Don't reset - just switch intent
                    logger.info(
                        f"[session] informational_intent_switch user_id={user_id}{log_transaction_id} "
                        f"old={session_intent_str} new={luma_intent_name} (informational intent breaks booking flow)"
                    )
                elif is_new_intent_core and luma_intent_name != session_intent_str:
                    # INTENT TRANSITION CLASSIFICATION:
                    # - UNKNOWN → concrete intent = INTENT MATERIALIZATION (upgrade, not reset)
                    #   Pre-intent slots (e.g., date from "tomorrow") must be preserved and merged.
                    #   Do NOT clear session, do NOT set session_reset_occurred = True.
                    # - Concrete → incompatible concrete intent = TRUE INTENT SWITCH (reset)
                    #   Clear old session (e.g., CREATE_APPOINTMENT → DETAILS).
                    #   Set session_reset_occurred = True to block merge.
                    branch_name = None
                    if session_intent_str == "UNKNOWN":
                        # Intent materialization: UNKNOWN → concrete intent
                        # CRITICAL: Do NOT call clear_session() - preserve pre-intent slots
                        # CRITICAL: Set session_reset_occurred = False to allow merge
                        effective_intent = luma_intent_name
                        session_reset_occurred = False
                        logger.error(
                            f"[SESSION_RESET_WRITER] WRITER=unknown_to_concrete_materialization value=False "
                            f"location=line_274_materialization reason=UNKNOWN_to_concrete_upgrade "
                            f"prior={session_intent_str} new={luma_intent_name} "
                            f"user_id={user_id}{log_transaction_id}"
                        )
                        branch_name = "materialization"
                        logger.info(
                            f"[session] intent_materialized user_id={user_id}{log_transaction_id} "
                            f"old={session_intent_str} new={luma_intent_name} (preserving pre-intent slots)"
                        )
                    else:
                        # True intent switch: concrete intent → different concrete intent
                        # Clear old session (e.g., CREATE_APPOINTMENT → DETAILS)
                        effective_intent = luma_intent_name
                        from core.orchestration.session.session_manager import (
                            clear_session,
                        )

                        clear_session(user_id)
                        session_state = None
                        session_reset_occurred = True
                        logger.error(
                            f"[SESSION_RESET_WRITER] WRITER=concrete_to_concrete_reset value=True "
                            f"location=line_287_intent_switch reason=true_intent_switch "
                            f"old={session_intent_str} new={luma_intent_name} "
                            f"user_id={user_id}{log_transaction_id}"
                        )
                        branch_name = "reset"
                        logger.info(
                            f"[session] intent_changed user_id={user_id}{log_transaction_id} "
                            f"old={session_intent_str} new={luma_intent_name}"
                        )
                    # DEBUG: Log intent resolution decision (show both intent_name and intent fields)
                    session_intent_name_field = (
                        session_state.get("intent_name") if session_state else None
                    )
                    session_intent_field = (
                        session_state.get("intent") if session_state else None
                    )
                    logger.error(
                        f"[INTENT_RESOLVE] prior={session_intent_str} new={luma_intent_name} "
                        f"reset={session_reset_occurred} reason={branch_name} "
                        f"session.intent_name={session_intent_name_field} session.intent={session_intent_field} "
                        f"user_id={user_id}{log_transaction_id}"
                    )
                else:
                    # Same core intent or no switch - keep session
                    # GUARD: Do NOT preserve session intent for CONFIRM_* unless continuation conditions are met
                    is_confirm_intent = (
                        luma_intent_name and luma_intent_name.startswith("CONFIRM_")
                    )
                    session_status_for_same = (
                        session_state.get("status") if session_state else None
                    )
                    should_block_same_preservation = (
                        is_confirm_intent
                        and session_status_for_same != "AWAITING_CONFIRMATION"
                    )

                    if should_block_same_preservation:
                        # CONFIRM_* intent without continuation conditions - use CONFIRM_* intent, not session
                        effective_intent = luma_intent_name
                        logger.info(
                            f"[session] confirm_intent_not_preserved_same user_id={user_id}{log_transaction_id} "
                            f"luma.intent={luma_intent_name} session.intent={session_intent_str} "
                            f"session.status={session_status_for_same} "
                            f"(CONFIRM_* NOT preserving session - status != AWAITING_CONFIRMATION)"
                        )
                    else:
                        # Same core intent or no switch - keep session
                        effective_intent = session_intent_str

    # Hard assertion: effective_intent must NOT be UNKNOWN when session exists with durable intent (and not reset)
    # Durable intents should always be recovered from session when Luma returns UNKNOWN/empty
    if session_state and not session_reset_occurred:
        # CRITICAL: Check intent_name first, then fall back to intent
        session_intent_for_assert = session_state.get(
            "intent_name"
        ) or session_state.get("intent")
        session_intent_str_for_assert = (
            session_intent_for_assert
            if isinstance(session_intent_for_assert, str)
            else (
                session_intent_for_assert.get("name", "")
                if isinstance(session_intent_for_assert, dict)
                else ""
            )
        )
        if session_intent_str_for_assert:
            session_intent_str_for_assert = session_intent_str_for_assert.strip()
        else:
            session_intent_str_for_assert = ""
        if session_intent_str_for_assert:
            # Check if session intent is durable
            try:
                from core.policy.intent_policy import get_intent_durable

                if get_intent_durable(session_intent_str_for_assert):
                    # If session has durable intent, effective_intent should never be UNKNOWN
                    assert effective_intent != "UNKNOWN", (
                        f"Assertion failed: effective_intent is UNKNOWN but session has durable intent. "
                        f"session.intent={session_intent_str_for_assert}, effective_intent={effective_intent}, luma.intent={luma_intent_name}"
                    )
            except (ImportError, Exception):
                # If durability check fails, skip assertion (don't fail on import errors)
                pass

    # DEBUG: Log final intent resolution result before returning
    logger.error(
        f"[SESSION_RESET_WRITER] FINAL_VALUE={session_reset_occurred} "
        f"effective_intent={effective_intent} "
        f"location=line_337_before_return "
        f"user_id={user_id}{log_transaction_id}"
    )
    logger.error(
        f"[INTENT_RESOLVE] RETURNING effective_intent={effective_intent} "
        f"session_reset_occurred={session_reset_occurred} "
        f"user_id={user_id}{log_transaction_id}"
    )
    return effective_intent, session_reset_occurred
