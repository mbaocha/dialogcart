"""Validation and Core-owned lifecycle creation for assistant proposals."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from core.rendering.llm_renderer import HandlerEntitySelection, HandlerRenderResult

PROPOSAL_TYPE = "ENTITY_RECOMMENDATION"
PENDING_STATUS = "PENDING"
EXPECTED_RESPONSES = ["ACCEPT", "REJECT"]
EXPIRY_POLICY = "NEXT_COMPATIBLE_USER_TURN"


@dataclass(frozen=True)
class AssistantProposalRelationship:
    """Attach-time binding of a generic response act to proposal state."""

    response_act: str
    resolution: str
    proposal: Dict[str, Any] | None = None
    reason_code: str = ""


def bind_assistant_proposal(
    *, response_act: str | None, proposals: Any, explicit_slots: Dict[str, Any]
) -> AssistantProposalRelationship | None:
    if response_act not in ("CONFIRM_ACTION", "REJECT_ACTION"):
        return None
    expected_response = (
        "ACCEPT" if response_act == "CONFIRM_ACTION" else "REJECT"
    )
    active = [
        dict(item) for item in (proposals or [])
        if isinstance(item, dict)
        and item.get("status") == PENDING_STATUS
        and expected_response in (item.get("expected_responses") or [])
    ]
    # Explicit current-turn selection is authoritative, not proposal acceptance.
    active = [
        item for item in active
        if explicit_slots.get(str(item.get("slot_key") or "")) is None
    ]
    if not active:
        return AssistantProposalRelationship(
            response_act, "NO_MATCH", reason_code="ASSISTANT_PROPOSAL_NO_MATCH"
        )
    if len(active) > 1:
        return AssistantProposalRelationship(
            response_act, "AMBIGUOUS", reason_code="ASSISTANT_PROPOSAL_AMBIGUOUS"
        )
    return AssistantProposalRelationship(
        response_act, "BOUND", proposal=active[0],
        reason_code="ASSISTANT_PROPOSAL_BOUND",
    )


def proposal_status_updates(
    proposals: Any, *, proposal_id: str | None = None, status: str
) -> List[Dict[str, str]]:
    updates = []
    for proposal in proposals or []:
        if not isinstance(proposal, dict) or proposal.get("status") != PENDING_STATUS:
            continue
        if proposal_id is None or proposal.get("proposal_id") == proposal_id:
            updates.append({"proposal_id": str(proposal.get("proposal_id")), "status": status})
    return updates


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _catalog_records(structured_context: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for collection in structured_context.values():
        if isinstance(collection, list):
            for record in collection:
                if isinstance(record, dict) and record.get("name"):
                    yield record


def _resolve_selection(
    selection: HandlerEntitySelection, structured_context: Dict[str, Any]
) -> Dict[str, Any]:
    records = [
        record
        for record in _catalog_records(structured_context)
        if _norm(record.get("type")) == _norm(selection.entity_type)
    ]
    supplied = {
        "catalog_id": selection.catalog_id,
        "canonical_id": selection.canonical_id,
        "display_name": selection.display_name,
    }
    matches = []
    for record in records:
        identifiers = {
            "catalog_id": [record.get("id"), record.get("catalog_id")],
            "canonical_id": [
                record.get("canonical_id"), record.get("canonicalKey"),
                (record.get("config") or {}).get("canonical_id")
                if isinstance(record.get("config"), dict) else None,
            ],
            "display_name": [record.get("name")],
        }
        if all(
            value is None
            or any(_norm(value) == _norm(candidate) for candidate in identifiers[key] if candidate is not None)
            for key, value in supplied.items()
        ):
            matches.append(record)
    if len(matches) != 1:
        raise ValueError("Rendered entity selection is unavailable or has conflicting identifiers")
    record = matches[0]
    if record.get("is_active") is False or record.get("isActive") is False:
        raise ValueError("Rendered entity selection is inactive")
    return record


def create_assistant_proposals(
    render_result: HandlerRenderResult,
    *,
    structured_context: Dict[str, Any],
    handler_name: str,
    transaction_id: str,
) -> List[Dict[str, Any]]:
    """Validate renderer evidence and create canonical non-booking proposals."""
    proposals = []
    for selection in render_result.selected_entities:
        record = _resolve_selection(selection, structured_context)
        display_name = str(record["name"])
        if _norm(display_name) not in _norm(render_result.text):
            raise ValueError("Rendered proposal text does not name the selected entity")
        entity_type = str(record.get("type") or selection.entity_type)
        proposals.append({
            "proposal_id": str(uuid.uuid4()),
            "proposal_type": PROPOSAL_TYPE,
            "status": PENDING_STATUS,
            "entity_type": entity_type,
            "slot_key": f"{entity_type}_id",
            "catalog_id": record.get("id", record.get("catalog_id")),
            "canonical_id": _norm(display_name),
            "display_name": display_name,
            "source": {"handler": handler_name, "transaction_id": transaction_id},
            "expected_responses": list(EXPECTED_RESPONSES),
            "expiry_policy": EXPIRY_POLICY,
        })
    return proposals
