"""E2E helpers for turn.understanding regressions (utterance vs session stickiness)."""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional

from core.tests.e2e.framework.conversation import BookingConversation
from core.tests.harness.clients import (
    ScriptedLumaClient,
    apply_service_ambiguity_resolution,
)


class UnderstandingAwareScriptedLumaClient(ScriptedLumaClient):  # noqa: N801
    __test__ = False

    """Scripted Luma that mirrors production service stickiness + understanding.

    After the scripted Stage-2 payload, applies service ambiguity resolution
    (so session ``resolved_service_id`` can land in ``facts.service_id``) then
    derives ``turn.understanding`` via NLU ``derive_turn_understanding``.

    Does not import ``NLUPipeline`` (avoids Anthropic / Stage-1 import side effects).
    """

    def resolve(
        self,
        user_id: str,
        text: str,
        domain: str = "service",
        timezone: str = "UTC",
        tenant_context: Optional[Dict[str, Any]] = None,
        conversation_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        response = super().resolve(
            user_id,
            text,
            domain,
            timezone,
            tenant_context,
            conversation_context=conversation_context,
        )
        from nlu.stages.shared.turn_understanding import derive_turn_understanding

        intent = response.get("intent") or {}
        intent_name = intent.get("name") if isinstance(intent, dict) else intent
        if not isinstance(intent_name, str) or not intent_name:
            intent_name = "UNKNOWN"

        facts = response.get("facts") if isinstance(response.get("facts"), dict) else {}
        slm: Dict[str, Any] = {
            "intent": intent_name,
            "facts": dict(facts),
            "service_term": response.get("service_term"),
            "service_candidates": list(response.get("service_candidates") or []),
            "operation": response.get("operation"),
            "search_query": response.get("search_query"),
            "time_constraint": response.get("time_constraint"),
            "temporal": response.get("temporal"),
        }
        resolved = apply_service_ambiguity_resolution(
            slm,
            tenant_context if isinstance(tenant_context, dict) else {},
            conversation_context,
        )
        understanding = derive_turn_understanding(resolved, conversation_context)

        updated = copy.deepcopy(response)
        updated_facts = resolved.get("facts") if isinstance(resolved.get("facts"), dict) else facts
        updated["facts"] = dict(updated_facts)
        updated["service_candidates"] = list(
            resolved.get("service_candidates") or response.get("service_candidates") or []
        )
        if "service_term" in resolved:
            updated["service_term"] = resolved.get("service_term")
        updated["turn"] = {"understanding": understanding}

        slots = updated.get("slots")
        if isinstance(slots, dict):
            service_id = updated_facts.get("service_id")
            if service_id and not slots.get("service_id"):
                updated["slots"] = {**slots, "service_id": service_id}

        self.last_response = updated
        return updated


def nlu_understanding(luma_client: UnderstandingAwareScriptedLumaClient) -> Optional[str]:
    response = getattr(luma_client, "last_response", None)
    if not isinstance(response, dict):
        return None
    turn = response.get("turn")
    if isinstance(turn, dict):
        value = turn.get("understanding")
        if isinstance(value, str) and value:
            return value
    return None


def outcome_understanding(conv: BookingConversation) -> Optional[str]:
    for source in (conv.outcome, conv.plan, conv.last_body):
        if not isinstance(source, dict):
            continue
        turn = source.get("turn")
        if isinstance(turn, dict):
            value = turn.get("understanding")
            if isinstance(value, str) and value:
                return value
        nested = source.get("plan")
        if isinstance(nested, dict):
            turn = nested.get("turn")
            if isinstance(turn, dict):
                value = turn.get("understanding")
                if isinstance(value, str) and value:
                    return value
        result = source.get("result")
        if isinstance(result, dict):
            turn = result.get("turn")
            if isinstance(turn, dict):
                value = turn.get("understanding")
                if isinstance(value, str) and value:
                    return value
    # decision_trace may still carry planner plan.turn when HTTP outcome rebuilt.
    trace = conv.last_body.get("decision_trace") if isinstance(conv.last_body, dict) else None
    if isinstance(trace, dict):
        for key in ("plan", "outcome", "result"):
            section = trace.get(key)
            if not isinstance(section, dict):
                continue
            turn = section.get("turn")
            if isinstance(turn, dict):
                value = turn.get("understanding")
                if isinstance(value, str) and value:
                    return value
    return None


def assert_understanding_everywhere(
    conv: BookingConversation,
    luma_client: UnderstandingAwareScriptedLumaClient,
    expected: str,
    *,
    require_result: bool = True,
) -> None:
    """Assert NLU payload and (when required) planner/RESULT understanding agree."""
    nlu_value = nlu_understanding(luma_client)
    conv._assert(
        nlu_value == expected,
        (
            f"turn {conv.turn}: NLU turn.understanding expected {expected!r}, "
            f"got {nlu_value!r} from {getattr(luma_client, 'last_response', None)!r}"
        ),
    )
    outcome_value = outcome_understanding(conv)
    if outcome_value is None and not require_result:
        return
    conv._assert(
        outcome_value == expected,
        (
            f"turn {conv.turn}: RESULT/outcome turn.understanding expected "
            f"{expected!r}, got {outcome_value!r}"
        ),
    )


def session_fingerprint(session: Optional[Dict[str, Any]]) -> Any:
    if not isinstance(session, dict):
        return None
    availability = session.get("availability")
    if isinstance(availability, dict) and availability.get("fingerprint") is not None:
        return availability.get("fingerprint")
    return session.get("availability_fingerprint")


def session_service_id(session: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(session, dict):
        return None
    slots = session.get("slots")
    if isinstance(slots, dict) and slots.get("service_id"):
        return str(slots.get("service_id"))
    return None
