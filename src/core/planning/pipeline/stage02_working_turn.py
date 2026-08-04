"""Stage 02 — working-turn construction (promotion, merge, proposals)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.adapters.nlu.conversation_memory import update_conversation
from core.planning.luma_facts_adapter import facts_to_slots, merge_promoted_luma_slots
from core.planning.pipeline.requests import AttachedRequest
from core.planning.pipeline.types import WorkingTurn
from core.planning.temporal_contract import attach_temporal, get_temporal
from core.planning.temporal_proposal import extract_nlu_proposals, merge_session_proposals
from core.session.effective_slots import _compute_effective_collected_slots
from core.session.merge import merge_luma_with_session, should_merge_session_context
from core.session.session_schema_v2 import get_conversation_memory

logger = logging.getLogger(__name__)


def build_working_turn(
    *,
    luma_response: Dict[str, Any],
    raw_luma_response_deep_copy: Optional[Dict[str, Any]],
    attached_request: AttachedRequest,
    session_state: Optional[Dict[str, Any]],
    original_session_state: Optional[Dict[str, Any]],
    source_text: str,
    tenant_context: Optional[Dict[str, Any]],
    apply_domain_filter: bool,
    entity_schema: Optional[Dict[str, Any]] = None,
) -> WorkingTurn:
    planning_intent = attached_request.planning_intent
    payload = attach_temporal(dict(luma_response))
    temporal = get_temporal(payload)

    # Public intent name is planning intent (workflow attachment). Attachment
    # metadata lives on AttachedRequest — not projected onto this payload.
    payload["intent"] = {"name": planning_intent}

    schema = entity_schema
    if schema is None and isinstance(luma_response.get("_entity_schema"), dict):
        schema = luma_response.get("_entity_schema")
    if schema is not None:
        payload["_entity_schema"] = schema

    _updated = update_conversation(
        session_state or {},
        user_text=source_text,
        intent=planning_intent or "UNKNOWN",
        search_query=luma_response.get("search_query"),
    )
    payload["_conversation"] = get_conversation_memory(_updated)

    facts_obj = luma_response.get("facts", {})
    promoted_slots = (
        facts_to_slots(
            facts_obj,
            intent_name=planning_intent,
            source_text=source_text,
            entity_schema=schema,
        )
        if isinstance(facts_obj, dict)
        else {}
    )
    merged_authoritative_slots = luma_response.get("slots") or {}
    if not isinstance(merged_authoritative_slots, dict):
        merged_authoritative_slots = {}
    nested_from_facts: Dict[str, Any] = {}
    if isinstance(facts_obj, dict) and "slots" in facts_obj:
        nested_from_facts = facts_obj.get("slots") or {}
        if not isinstance(nested_from_facts, dict):
            nested_from_facts = {}
    nested_slots = {**nested_from_facts, **merged_authoritative_slots}
    payload["slots"] = merge_promoted_luma_slots(
        nested_slots,
        promoted_slots,
        facts_obj if isinstance(facts_obj, dict) else None,
        temporal=temporal,
    )
    payload["_source_text"] = source_text

    proposal_session = original_session_state if original_session_state else {}
    nlu_proposals = extract_nlu_proposals(payload)
    current_turn_has_time = bool(nlu_proposals.get("time_proposal"))
    nlu_date_proposal = nlu_proposals.get("date_proposal")
    current_turn_has_date = bool(
        isinstance(nlu_date_proposal, dict) and nlu_date_proposal.get("start")
    )
    payload["_current_turn_has_time"] = current_turn_has_time
    payload["_current_turn_has_date"] = current_turn_has_date

    if current_turn_has_date and isinstance(nlu_date_proposal, dict):
        from core.workflows.availability.presentation import normalize_search_date

        payload["_current_turn_date"] = normalize_search_date(
            nlu_date_proposal.get("start")
        )
    else:
        payload.pop("_current_turn_date", None)

    if current_turn_has_time:
        tp = nlu_proposals.get("time_proposal")
        if isinstance(tp, dict) and tp.get("mode") == "exact" and tp.get("value"):
            payload["_current_turn_time"] = tp.get("value")
        elif temporal.get("start_time"):
            payload["_current_turn_time"] = temporal.get("start_time")
        else:
            payload.pop("_current_turn_time", None)
    else:
        payload.pop("_current_turn_time", None)

    # Capture current-turn availability-criteria facts (not session-carried).
    from core.adapters.nlu.entity_schema_builder import (
        SEARCH_CRITERIA_KEY_ALIASES,
        canonicalize_search_criteria_key,
        search_criteria_slot_keys_from_entity_schema,
    )

    facts_obj = facts_obj if isinstance(facts_obj, dict) else {}
    criteria_keys = set(search_criteria_slot_keys_from_entity_schema(schema))
    criteria_keys.update(SEARCH_CRITERIA_KEY_ALIASES.keys())
    seen_canons = set()
    for key in sorted(criteria_keys):
        value = facts_obj.get(key)
        if value is None and isinstance(luma_response.get("slots"), dict):
            value = luma_response["slots"].get(key)
        canon = canonicalize_search_criteria_key(key)
        if value is not None and value != "":
            payload[f"_current_turn_{canon}"] = value
            seen_canons.add(canon)
        elif canon not in seen_canons and key == canon:
            payload.pop(f"_current_turn_{canon}", None)

    merged_proposals = merge_session_proposals(
        proposal_session,
        nlu_proposals["date_proposal"],
        nlu_proposals["time_proposal"],
    )

    payload["date_proposal"] = merged_proposals["date_proposal"]
    # Availability ops must not rebind a stale session time when this turn
    # did not mention a time. Current-turn time evidence is preserved.
    from core.planning.pipeline.requests import is_availability_turn_operation

    if (
        is_availability_turn_operation(attached_request.turn_operation)
        and not current_turn_has_time
    ):
        payload["time_proposal"] = None
    else:
        payload["time_proposal"] = merged_proposals["time_proposal"]

    if (
        tenant_context
        and "aliases" in tenant_context
        and "service_id" in payload.get("slots", {})
    ):
        aliases = tenant_context["aliases"]
        raw_service_id = payload["slots"]["service_id"]
        if isinstance(raw_service_id, str) and raw_service_id.lower() in aliases:
            mapped = aliases[raw_service_id.lower()]
            if isinstance(mapped, int):
                payload["slots"]["_catalog_item_id"] = mapped
            else:
                payload["slots"]["_canonical_service_id"] = mapped
            payload["slots"]["service_id"] = raw_service_id

    if not isinstance(payload.get("slots"), dict):
        payload["slots"] = {}

    if raw_luma_response_deep_copy is not None:
        payload["_raw_luma_response"] = raw_luma_response_deep_copy

    # --- Planning evidence: compute once before merge mixes session state ---
    from core.planning.planning_evidence import stamp_planning_evidence

    raw_src = (
        raw_luma_response_deep_copy
        if isinstance(raw_luma_response_deep_copy, dict)
        else luma_response
    )
    raw_dialogue_act = ""
    if isinstance(raw_src, dict):
        _raw_intent = raw_src.get("intent")
        if isinstance(_raw_intent, dict):
            raw_dialogue_act = str(_raw_intent.get("name") or "")
        elif isinstance(_raw_intent, str):
            raw_dialogue_act = _raw_intent

    session_for_merge = original_session_state or session_state
    session_slots_for_delta: Dict[str, Any] = {}
    if isinstance(session_for_merge, dict):
        _ss = session_for_merge.get("slots")
        if isinstance(_ss, dict):
            session_slots_for_delta = _ss

    turn_slots_snapshot = dict(payload.get("slots") or {})
    stamp_planning_evidence(
        payload,
        session_slots=session_slots_for_delta,
        raw_turn_slots=turn_slots_snapshot,
        entity_schema=schema,
        raw_dialogue_act=raw_dialogue_act,
        operation=(
            payload.get("operation")
            if isinstance(payload.get("operation"), str)
            else attached_request.turn_operation
        ),
    )

    should_merge = should_merge_session_context(
        session_for_merge,
        session_reset_occurred=attached_request.session_reset_occurred,
    )

    if should_merge and session_for_merge:
        payload = merge_luma_with_session(
            payload,
            session_for_merge,
            apply_domain_filter=apply_domain_filter,
            turn_operation=attached_request.turn_operation,
        )
    elif payload:
        payload = _compute_effective_collected_slots(
            payload, apply_domain_filter=apply_domain_filter
        )
        if "missing_slots" not in payload:
            payload["missing_slots"] = []

    effective_collected = payload.get("_effective_collected_slots")
    if not isinstance(effective_collected, dict):
        effective_collected = payload.get("slots", {})
        if not isinstance(effective_collected, dict):
            effective_collected = {}

    return WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(effective_collected),
        raw_luma_response_deep_copy=raw_luma_response_deep_copy,
    )
