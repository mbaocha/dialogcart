"""Thin NLU invocation for planning turns.

Owns payload construction, conversation-context lookup, client resolve,
contract validation, raw-response copy, and minimal invocation tracing.

Does not build durable-session recovery outcomes, resolve intent, merge
session, or flatten planning results.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from core.adapters.errors import ContractViolation, UpstreamError
from core.adapters.nlu import LumaClient, assert_luma_contract
from core.adapters.nlu.conversation_memory import build_conversation_context

logger = logging.getLogger(__name__)
turn_logger = logging.getLogger("core.turn_log")

NluStatus = Literal["ok", "upstream_error", "empty_response", "contract_violation"]


@dataclass
class NluInvocationResult:
    status: NluStatus
    luma_response: Optional[Dict[str, Any]] = None
    raw_luma_response_deep_copy: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


def invoke_nlu_for_planning(
    *,
    user_id: str,
    text: str,
    derived_domain: str,
    timezone: str,
    tenant_context: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]],
    luma_client: LumaClient,
    entity_schema: Optional[Dict[str, Any]] = None,
) -> NluInvocationResult:
    """Call NLU and validate contract; return structured ok/failure status."""
    conversation_context = build_conversation_context(session_state)

    luma_payload = {
        "user_id": user_id,
        "text": text,
        "domain": derived_domain,
        "timezone": timezone,
    }
    if tenant_context:
        luma_payload["tenant_context"] = tenant_context
    else:
        logger.warning(
            f"[ORCHESTRATOR] No tenant_context to send to Luma (domain={derived_domain})"
        )
    if entity_schema:
        luma_payload["entity_schema"] = entity_schema

    logger.info(
        "Luma request payload: %s", json.dumps(luma_payload, ensure_ascii=False)
    )

    try:
        from core.tracing.decision_trace import measure_stage
    except ImportError:
        from contextlib import contextmanager

        @contextmanager
        def measure_stage(_stage: str):  # type: ignore[misc]
            yield

    try:
        with measure_stage("luma"):
            luma_response = luma_client.resolve(
                user_id=user_id,
                text=text,
                domain=derived_domain,
                timezone=timezone,
                tenant_context=tenant_context,
                conversation_context=conversation_context,
                entity_schema=entity_schema,
            )
    except UpstreamError as e:
        logger.error(
            f"[LUMA_ERROR_FALLBACK] Luma API error for user {user_id}: {str(e)}"
        )
        return NluInvocationResult(
            status="upstream_error",
            error_message=str(e),
        )

    raw_luma_response_deep_copy = copy.deepcopy(luma_response)

    luma_intent_obj = (
        luma_response.get("intent", {}) if isinstance(luma_response, dict) else {}
    )
    luma_intent = (
        luma_intent_obj.get("name", "") if isinstance(luma_intent_obj, dict) else ""
    )
    luma_slots = (
        luma_response.get("slots", {}) if isinstance(luma_response, dict) else {}
    )
    luma_missing_slots = (
        luma_response.get("missing_slots", [])
        if isinstance(luma_response, dict)
        else []
    )
    try:
        from core.tracing.decision_trace import is_decision_trace_enabled

        _trace_on = is_decision_trace_enabled()
    except ImportError:
        _trace_on = False
    if not _trace_on:
        turn_logger.info(
            json.dumps(
                {
                    "turn": "LUMA",
                    "intent": luma_intent,
                    "slots": luma_slots,
                    "missing_slots": luma_missing_slots,
                },
                ensure_ascii=True,
                default=str,
            )
        )

    if not luma_response or not isinstance(luma_response, dict):
        logger.error(
            f"[LUMA_ERROR_FALLBACK] Luma returned None or invalid response for user {user_id}"
        )
        return NluInvocationResult(
            status="empty_response",
            luma_response=luma_response if isinstance(luma_response, dict) else None,
            raw_luma_response_deep_copy=raw_luma_response_deep_copy
            if isinstance(raw_luma_response_deep_copy, dict)
            else None,
            error_message="Luma returned empty response",
        )

    try:
        assert_luma_contract(luma_response)
    except ContractViolation as e:
        logger.error(
            f"[LUMA_ERROR_FALLBACK] Contract violation for user {user_id}: {str(e)}"
        )
        return NluInvocationResult(
            status="contract_violation",
            luma_response=luma_response,
            raw_luma_response_deep_copy=raw_luma_response_deep_copy,
            error_message=str(e),
        )

    return NluInvocationResult(
        status="ok",
        luma_response=luma_response,
        raw_luma_response_deep_copy=raw_luma_response_deep_copy,
    )
