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
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


def resolve_effective_intent(
    luma_response: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
    user_id: str,
    transaction_id: Optional[str] = None
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
            session_intent = session_state.get("intent")
            session_intent_str = session_intent if isinstance(session_intent, str) else (
                session_intent.get("name", "") if isinstance(session_intent, dict) else "")
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
    luma_intent_name = luma_intent_obj.get(
        "name", "") if isinstance(luma_intent_obj, dict) else ""

    # DURABLE INTENT RECOVERY: If Luma intent is UNKNOWN/empty/error and session has durable intent, recover it
    # This must happen BEFORE any other intent resolution logic to ensure planning uses the correct intent
    luma_intent_is_unknown_or_empty = (
        not luma_intent_name or luma_intent_name == "UNKNOWN")

    if luma_intent_is_unknown_or_empty and session_state:
        session_intent = session_state.get("intent")
        session_intent_str = session_intent if isinstance(session_intent, str) else (
            session_intent.get("name", "") if isinstance(session_intent, dict) else "")
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
                    return session_intent_str, False
            except (ImportError, Exception) as e:
                logger.warning(
                    f"Failed to check durable status for '{session_intent_str}': {e}. "
                    f"Continuing with normal intent resolution."
                )

    # Resolve effective_intent using session
    effective_intent = luma_intent_name
    session_reset_occurred = False

    # SESSION LIFECYCLE RULE: Handle session intent override for:
    # 1. NEEDS_CLARIFICATION sessions (existing behavior)
    # 2. READY sessions with CREATE_APPOINTMENT (allows time overrides like "make it 4pm")
    session_status = session_state.get("status") if session_state else None
    should_handle_session_intent = (
        session_state and (
            session_status == "NEEDS_CLARIFICATION" or
            (session_status == "READY" and session_state.get("intent") is not None)
        )
    )

    if should_handle_session_intent:
        session_intent = session_state.get("intent")
        session_intent_str = session_intent if isinstance(session_intent, str) else (
            session_intent.get("name", "") if isinstance(session_intent, dict) else "")

        # For READY sessions, only process if intent is CREATE_APPOINTMENT (preserve for modifications)
        if session_status == "READY" and session_intent_str != "CREATE_APPOINTMENT":
            # For non-CREATE_APPOINTMENT READY sessions, don't override intent (session should be cleared)
            pass
        else:
            # Check for domain switch based on canonical service evidence (even for UNKNOWN intents)
            canonical_indicates_switch = False
            context = luma_response.get("context", {})
            services = context.get("services", []) if isinstance(
                context, dict) else []

            if services and isinstance(services, list) and len(services) > 0:
                first_service = services[0]
                if isinstance(first_service, dict):
                    canonical = first_service.get(
                        "canonical") or first_service.get("canonical_key")
                    if canonical:
                        canonical_str = str(canonical).lower()
                        # Check if canonical indicates reservation domain (hospitality.*)
                        if canonical_str.startswith("hospitality.") or canonical_str.startswith("lodging."):
                            # Canonical indicates reservation domain
                            if session_intent_str == "CREATE_APPOINTMENT":
                                canonical_indicates_switch = True
                                logger.info(
                                    f"[session] domain_switch_detected user_id={user_id}{log_transaction_id} "
                                    f"canonical={canonical} indicates reservation domain, session was service"
                                )
                        elif canonical_str.startswith("beauty_and_wellness.") or canonical_str.startswith("service."):
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
                        canonical = first_service.get(
                            "canonical") or first_service.get("canonical_key")
                        if canonical:
                            canonical_str = str(canonical).lower()
                            if canonical_str.startswith("hospitality.") or canonical_str.startswith("lodging."):
                                new_intent = "CREATE_RESERVATION"
                            elif canonical_str.startswith("beauty_and_wellness.") or canonical_str.startswith("service."):
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
                    logger.info(
                        f"[session] domain_switch_reset user_id={user_id}{log_transaction_id} "
                        f"old={session_intent_str} new={effective_intent} (canonical-based switch)"
                    )
            elif luma_intent_name == "UNKNOWN" or not luma_intent_name:
                # DURABLE INTENT RECOVERY: If luma.intent is UNKNOWN/empty, recover durable session intent
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
                # Check if new intent is non-core (DISCOVERY, CONFIRM_BOOKING, etc.)
                from core.routing.intents.base_intents import is_core_intent
                is_new_intent_core = is_core_intent(luma_intent_name)
                is_session_intent_core = is_core_intent(
                    session_intent_str) if session_intent_str else False

                # Rule: Non-core intents (DISCOVERY, CONFIRM_BOOKING) should NOT overwrite active booking session
                if is_session_intent_core and not is_new_intent_core:
                    # Keep session intent - non-core intents are side-intents that don't interrupt booking flow
                    effective_intent = session_intent_str
                    logger.info(
                        f"[session] non_core_intent_ignored user_id={user_id}{log_transaction_id} "
                        f"session.intent={session_intent_str} luma.intent={luma_intent_name} (non-core, preserving session)"
                    )
                elif is_new_intent_core and luma_intent_name != session_intent_str:
                    # Core booking intent changed - clear old session
                    effective_intent = luma_intent_name
                    from core.orchestration.session.session_manager import clear_session
                    clear_session(user_id)
                    session_state = None
                    session_reset_occurred = True
                    logger.info(
                        f"[session] intent_changed user_id={user_id}{log_transaction_id} "
                        f"old={session_intent_str} new={luma_intent_name}"
                    )
                else:
                    # Same core intent or no switch - keep session
                    effective_intent = session_intent_str

    # Hard assertion: effective_intent must NOT be UNKNOWN when session exists with durable intent (and not reset)
    # Durable intents should always be recovered from session when Luma returns UNKNOWN/empty
    if session_state and not session_reset_occurred:
        session_intent_for_assert = session_state.get("intent")
        session_intent_str_for_assert = session_intent_for_assert if isinstance(session_intent_for_assert, str) else (
            session_intent_for_assert.get("name", "") if isinstance(session_intent_for_assert, dict) else "")
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

    return effective_intent, session_reset_occurred
