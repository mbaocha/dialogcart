"""Temporary test-only leftovers previously housed in orchestrator.py.

Not part of the production turn path. Prefer engine / workflows.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.workflows import get_workflow

logger = logging.getLogger(__name__)


def _handle_non_core_intent(
    luma_response: Dict[str, Any], decision: Dict[str, Any], user_id: str
) -> Dict[str, Any]:
    intent_name = decision.get("intent_name", "")
    facts = decision.get("facts", {})
    if not facts:
        facts = {}

    slots = luma_response.get("slots", {})
    if slots:
        facts.setdefault("slots", slots)

    missing_slots = luma_response.get("missing_slots")
    if missing_slots is not None and isinstance(missing_slots, list):
        facts.setdefault("missing_slots", missing_slots)
    else:
        logger.error(
            f"[MISSING_SLOTS] VIOLATION: missing_slots is None or not a list in non-core intent! "
            f"user_id={user_id}, missing_slots={missing_slots}, luma_response_keys={list(luma_response.keys())}"
        )
        facts["missing_slots"] = []

    context = luma_response.get("context")
    if context:
        facts.setdefault("context", context)
    elif "context" not in facts:
        facts["context"] = {}

    logger.info(
        f"Passing through non-core intent '{intent_name}' for user {user_id} "
        f"(not orchestrated by core)"
    )

    return {
        "success": True,
        "outcome": {
            "status": "NON_CORE_INTENT",
            "intent_name": intent_name,
            "facts": facts,
        },
    }


def _invoke_workflow_after_execute(
    intent_name: str, outcome: Dict[str, Any]
) -> Dict[str, Any]:
    try:
        workflow = get_workflow(intent_name)
        if workflow and hasattr(workflow, "after_execute"):
            try:
                return workflow.after_execute(outcome)
            except Exception as e:
                logger.warning(
                    f"Workflow after_execute hook failed for intent '{intent_name}': {e}. "
                    f"Returning original outcome."
                )
                return outcome
    except Exception as e:
        logger.debug(
            f"Error looking up workflow for intent '{intent_name}': {e}"
        )

    return outcome
