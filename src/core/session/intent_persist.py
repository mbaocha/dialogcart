"""Intent resolution and session lifecycle helpers for persistence."""

import logging
from typing import Any, Dict, Optional, Tuple

from core.orchestration.persistence.durable_intents import is_durable_intent

logger = logging.getLogger(__name__)


def resolve_durable_intent_for_session(
    outcome: Dict[str, Any],
    previous_session_state: Optional[Dict[str, Any]],
    outcome_status: str,
) -> str:
    """
    Resolve durable intent_name using explicit precedence:
    1. outcome.intent_name (if durable)
    2. plan.intent_name (if durable)
    3. previous_session_state.intent_name (if durable)
    """
    intent_name = ""

    if outcome:
        outcome_intent = outcome.get("intent_name") or outcome.get("intent")
        if outcome_intent and outcome_intent not in ("", "UNKNOWN"):
            if is_durable_intent(outcome_intent):
                intent_name = outcome_intent
                logger.debug(
                    "[build_session_state_from_outcome] Using outcome.intent_name=%s (durable)",
                    intent_name,
                )
            else:
                logger.debug(
                    "[build_session_state_from_outcome] Skipping ephemeral outcome.intent_name=%s",
                    outcome_intent,
                )

    if not intent_name and outcome:
        plan_obj = outcome.get("plan", {})
        if isinstance(plan_obj, dict):
            plan_intent_name = plan_obj.get("intent_name") or plan_obj.get("intent")
            if plan_intent_name and plan_intent_name not in ("", "UNKNOWN"):
                if is_durable_intent(plan_intent_name):
                    intent_name = plan_intent_name
                    logger.debug(
                        "[build_session_state_from_outcome] Using plan.intent_name=%s (durable)",
                        intent_name,
                    )
                else:
                    logger.debug(
                        "[build_session_state_from_outcome] Skipping ephemeral plan.intent_name=%s",
                        plan_intent_name,
                    )

    if not intent_name and previous_session_state:
        previous_intent = previous_session_state.get(
            "intent_name"
        ) or previous_session_state.get("intent")
        if previous_intent and previous_intent not in ("", "UNKNOWN"):
            if is_durable_intent(previous_intent):
                intent_name = previous_intent
                logger.info(
                    "[build_session_state_from_outcome] Preserving durable previous session intent=%s "
                    "(outcome.intent_name was falsy/empty, status=%s)",
                    intent_name,
                    outcome_status,
                )
            else:
                logger.debug(
                    "[build_session_state_from_outcome] Not preserving ephemeral previous session intent=%s",
                    previous_intent,
                )

    return intent_name


def should_clear_session_on_executed(
    outcome_status: str, intent_name: str
) -> Tuple[bool, Optional[str]]:
    """
    Return (should_clear, reason) for successful execution outcomes.

    Durable booking intents rebuild session (post-commit artifacts, booking_id, etc.).
    Ephemeral executed intents clear session as before.
    """
    if outcome_status != "EXECUTED":
        return False, None
    if intent_name and is_durable_intent(intent_name):
        logger.info(
            "[SESSION_LIFECYCLE] Rebuilding durable session after EXECUTED: intent=%s",
            intent_name,
        )
        return False, None
    if intent_name:
        logger.info(
            "[SESSION_LIFECYCLE] Clearing ephemeral session after EXECUTED: intent=%s",
            intent_name,
        )
        return True, "ephemeral intent executed"
    return False, None


def should_clear_session_on_ready(
    outcome_status: str, intent_name: str
) -> Tuple[bool, Optional[str]]:
    """
    Return (should_clear, reason).

    Durable intents preserve session on READY; ephemeral intents clear.
    """
    if outcome_status != "READY":
        return False, None
    if not intent_name:
        return True, "no intent"
    if is_durable_intent(intent_name):
        logger.info(
            "[SESSION_LIFECYCLE] Preserving durable session on READY: intent=%s",
            intent_name,
        )
        return False, None
    logger.info(
        "[SESSION_LIFECYCLE] Clearing ephemeral session on READY: intent=%s",
        intent_name,
    )
    return True, "ephemeral intent"


def resolve_final_intent_name(
    intent_name: str,
    outcome: Dict[str, Any],
    previous_session_state: Optional[Dict[str, Any]],
    outcome_status: str,
) -> Optional[str]:
    """Ensure durable intents are never wiped during persistence."""
    final_intent_name = (
        intent_name if intent_name and intent_name not in ("", "UNKNOWN") else None
    )

    if not final_intent_name and previous_session_state:
        previous_intent = previous_session_state.get(
            "intent_name"
        ) or previous_session_state.get("intent")
        if previous_intent and previous_intent not in ("", "UNKNOWN"):
            if is_durable_intent(previous_intent):
                final_intent_name = previous_intent
                logger.info(
                    "[build_session_state_from_outcome] Preserving durable session intent=%s "
                    "(outcome.intent_name was falsy/empty, status=%s)",
                    final_intent_name,
                    outcome_status,
                )

    if previous_session_state:
        previous_intent = previous_session_state.get(
            "intent_name"
        ) or previous_session_state.get("intent")
        if previous_intent and previous_intent not in ("", "UNKNOWN"):
            if is_durable_intent(previous_intent):
                assert final_intent_name == previous_intent or final_intent_name, (
                    f"Invariant violation: durable intent '{previous_intent}' lost during session persistence. "
                    f"final_intent_name={final_intent_name}, outcome.intent_name={outcome.get('intent_name')}, "
                    f"plan.intent_name={outcome.get('plan', {}).get('intent_name') if isinstance(outcome.get('plan'), dict) else None}, "
                    f"outcome_status={outcome_status}"
                )
                if not final_intent_name:
                    final_intent_name = previous_intent
                    logger.error(
                        "[SESSION_PERSISTENCE] CRITICAL: Recovered durable intent=%s (was about to be wiped)",
                        final_intent_name,
                    )

    if not final_intent_name and previous_session_state:
        logger.error(
            "[SESSION_PERSISTENCE] ERROR: intent_name is empty during persistence with existing session! "
            "outcome_status=%s, outcome_keys=%s, plan_keys=%s, previous_session_intent=%s",
            outcome_status,
            list(outcome.keys()) if outcome else None,
            (
                list(outcome.get("plan", {}).keys())
                if outcome and isinstance(outcome.get("plan"), dict)
                else None
            ),
            previous_session_state.get("intent_name"),
        )
        previous_intent = previous_session_state.get(
            "intent_name"
        ) or previous_session_state.get("intent")
        if previous_intent and previous_intent not in ("", "UNKNOWN"):
            final_intent_name = previous_intent
            logger.warning(
                "[SESSION_PERSISTENCE] Last resort: Preserving previous session intent=%s",
                final_intent_name,
            )

    return final_intent_name
