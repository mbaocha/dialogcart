"""Direct canonical Session V2 projection from finalized turn artifacts.

SessionProjectorV2 production path uses ``project_session_v2`` then
``hydrate_v1_compat_shims``.

Canonical field precedence (documented table)
--------------------------------------------
Lifecycle gates (return None / preserve previous):
  1. Empty/failed NLU with no valid outcome intent → preserve previous
  2. Invalid outcome (non-dict / missing intent) → None
  3. Ephemeral READY / ephemeral EXECUTED clear helpers → None

Planning section (after lifecycle assembly):
  Prefer finalized planning outcome/plan over merged working-turn slots over
  previous durable session. Assembly is performed by
  ``assemble_session_projection_fields`` (shared with the V1 oracle wrapper);
  nested V2 mapping below writes only canonical sections.

Booking section:
  Successful committed identifiers from assembled slots / outcome materialization
  → ``booking.booking_id`` / ``booking.booking_code`` only.
  Never retained under ``planning.slots``.

Capability / customer / conversation memory:
  From assembled capability facts, previous ``customer_id``, and NLU
  ``_conversation`` memory carried by assembly.

Availability / confirmation / conversation.history:
  Applied by SessionProjectorV2 overlays after this pure V2 document is built
  (workflow_result, merged confirmation gate, conversation_messages).

This module performs no storage I/O, no V1 mirror hydration, and no planning.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.session.session_schema_v2 import (
    empty_session_v2,
    normalize_session_to_v2,
    validate_session_v2_sections,
)


# Top-level keys that must not appear on the pure V2 document before hydration.
_V1_TOP_LEVEL_MIRRORS = frozenset(
    {
        "slots",
        "resolved_datetime_range",
        "availability_fingerprint",
        "last_execution_result",
        "presented_availability",
        "availability_presentation",
        "messages",
        "facts",
        "intent_name",
        "intent",
        "status",
        "missing_slots",
        "ask_next",
        "declined_slots",
        "slot_attempts",
        "last_filled_slot",
        "date_proposal",
        "time_proposal",
        "awaiting_slot",
        "active_capability",
        "service_candidates",
        "catalogue_presentation",
        "_modification_context",
        "temporal",
        "context",
    }
)


def assert_pure_v2_without_mirrors(session: Dict[str, Any]) -> None:
    """Raise when a projected document still carries V1 top-level mirrors."""
    validate_session_v2_sections(session)
    present = sorted(key for key in _V1_TOP_LEVEL_MIRRORS if key in session)
    if present:
        raise AssertionError(
            f"Pure Session V2 must not contain V1 top-level mirrors before hydration: {present}"
        )


def project_session_v2(
    *,
    previous_session_state: Optional[Dict[str, Any]] = None,
    working_session_state: Optional[Dict[str, Any]] = None,
    outcome: Dict[str, Any],
    outcome_status: str,
    merged_luma_response: Optional[Dict[str, Any]] = None,
    workflow_result: Optional[Dict[str, Any]] = None,
    capability_result: Optional[Dict[str, Any]] = None,
    handler_conversation_update: Optional[Dict[str, Any]] = None,
    conversation_messages: Optional[List[Dict[str, Any]]] = None,
    assistant_proposals: Optional[List[Dict[str, Any]]] = None,
    assistant_proposal_updates: Optional[List[Dict[str, Any]]] = None,
    organization_id: int,
    user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Project finalized turn artifacts into pure canonical Session V2.

    Returns ``None`` when lifecycle rules clear the session. Does not hydrate
    V1 compatibility mirrors. Optional overlay inputs are accepted for signature
    stability with ``SessionProjectorV2.project`` but availability / confirmation
    / history overlays remain projector-owned after this call.
    """
    _ = (
        working_session_state,
        workflow_result,
        capability_result,
        handler_conversation_update,
        conversation_messages,
        assistant_proposals,
        assistant_proposal_updates,
    )

    from core.session.persist import assemble_session_projection_fields

    assembled = assemble_session_projection_fields(
        outcome=outcome,
        outcome_status=outcome_status,
        organization_id=organization_id,
        merged_luma_response=merged_luma_response,
        previous_session_state=previous_session_state,
        user_id=user_id,
        session_store=None,
    )
    if assembled is None:
        return None

    # Empty-NLU preserve returns the previous document reference as-is.
    if previous_session_state is not None and assembled is previous_session_state:
        v2 = normalize_session_to_v2(previous_session_state)
        assert_pure_v2_without_mirrors(v2)
        return v2

    v2 = _assembled_planning_bag_to_v2(assembled)
    assert_pure_v2_without_mirrors(v2)
    return v2


def _assembled_planning_bag_to_v2(assembled: Dict[str, Any]) -> Dict[str, Any]:
    """Map the shared lifecycle assembly bag onto nested Session V2 only.

    Uses ``normalize_session_to_v2`` as the deterministic field mapper for the
    assembled planning artifact bag (same conversion the prior V1 round-trip
    used before hydration). The result is pure V2 — no top-level mirrors.
    """
    if not isinstance(assembled, dict):
        return empty_session_v2()
    return normalize_session_to_v2(assembled)
