"""AvailabilityWorkflow — availability domain boundary.

Phase 1: thin delegation facade.
Phase 2: owns all post-search processing (fingerprint, time resolution,
         presentation payloads, session persistence).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class AvailabilityWorkflow:
    """Facade for all availability-domain operations.

    Phase 1: thin delegation to the existing implementations in
             core.orchestration.availability_pagination,
             core.orchestration.availability_fingerprint,
             and core.orchestration.execution.clients.availability_client.

    Availability is a CORE workflow, not an extension.
    """

    # ------------------------------------------------------------------
    # Browse / pagination
    # ------------------------------------------------------------------

    def try_handle_browse_turn(
        self,
        *,
        plan: Dict[str, Any],
        session_state: Optional[Dict[str, Any]],
        session_store: Optional[Any],
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Attempt to handle a browse/pagination turn.

        Returns a full turn result when the turn is a browse operation,
        or None when normal planning is required.
        """
        from core.orchestration.availability_pagination import (
            try_handle_availability_browse_turn,
        )

        return try_handle_availability_browse_turn(
            plan=plan,
            session_state=session_state,
            session_store=session_store,
            user_id=user_id,
        )

    # ------------------------------------------------------------------
    # Fingerprint
    # ------------------------------------------------------------------

    def compute_fingerprint(self, slots: Dict[str, Any]) -> str:
        """Compute the canonical availability fingerprint for *slots*."""
        from core.orchestration.availability_fingerprint import (
            compute_availability_fingerprint,
        )

        return compute_availability_fingerprint(slots)

    def slots_match_fingerprint(
        self,
        slots: Dict[str, Any],
        fingerprint: str,
    ) -> bool:
        """Return True when *slots* produce the same fingerprint as stored."""
        from core.orchestration.availability_fingerprint import (
            slots_match_availability_fingerprint,
        )

        return slots_match_availability_fingerprint(slots, fingerprint)

    # ------------------------------------------------------------------
    # Search execution
    # ------------------------------------------------------------------

    def search(
        self,
        plan: Dict[str, Any],
        client: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Execute an availability search for *plan* using *client*.

        *client* defaults to a fresh AvailabilityClient when not supplied.
        """
        from core.orchestration.execution.clients.availability_client import (
            AvailabilityClient,
        )
        from core.orchestration.execution.dispatcher import execute

        return execute(
            plan=plan,
            availability_client=client or AvailabilityClient(),
        )

    # ------------------------------------------------------------------
    # Post-search result processing (Phase 2 ownership transfer)
    # ------------------------------------------------------------------

    def process_search_result(
        self,
        execution_result: Dict[str, Any],
        plan: Dict[str, Any],
        slots: Dict[str, Any],
        session_state: Optional[Dict[str, Any]],
        session_store: Optional[Any],
        user_id: str,
        organization_id: Optional[int] = None,
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """Process a successful availability search result in-place.

        Computes the fingerprint, resolves time matching, builds presentation
        payloads, and persists session keys.  All availability post-processing
        that previously lived in handle_message() now belongs here.

        Mutates *execution_result* and *plan* in-place.
        Returns ``(updated_slots, updated_session_state)`` because both may be
        rebound during time-match resolution.
        """
        from core.orchestration.session_ops import _persist_to_session
        from core.orchestration.availability_fingerprint import (
            build_availability_fingerprint_slots,
            compute_availability_fingerprint,
        )
        from core.orchestration.temporal_proposal import (
            enrich_last_execution_result,
            resolve_execution_proposals,
        )
        from core.orchestration.time_resolution import (
            TIME_MATCH_EXACT,
            TIME_MATCH_MISMATCH,
            apply_time_match_exact_to_plan,
            apply_time_match_mismatch_to_plan,
            resolve_time_after_availability,
        )
        from core.rendering.availability_renderer import (
            build_availability_presentation,
            build_presented_availability,
        )

        # ---- fingerprint ------------------------------------------------
        plan_intent_name = plan.get("intent_name") or plan.get("intent")

        _exec_proposals = resolve_execution_proposals(plan, session_state)
        fingerprint_slots = build_availability_fingerprint_slots(
            slots,
            intent_name=plan_intent_name,
            organization_id=organization_id,
            date_proposal=_exec_proposals["date_proposal"],
            time_proposal=_exec_proposals["time_proposal"],
            session_state=session_state,
        )
        availability_fingerprint = compute_availability_fingerprint(
            fingerprint_slots, intent_name=plan_intent_name
        )

        if availability_fingerprint:
            execution_result["availability_fingerprint"] = availability_fingerprint
            session_state = _persist_to_session(
                session_store,
                user_id,
                session_state or {},
                "availability_fingerprint",
                availability_fingerprint,
            )
            logger.debug(
                "[AVAILABILITY_FINGERPRINT] fingerprint=%s service_id=%s date=%s time=%s",
                availability_fingerprint,
                slots.get("service_id"),
                slots.get("date"),
                slots.get("time"),
            )

        # ---- time resolution --------------------------------------------
        search_date = None
        if slots.get("date"):
            search_date = str(slots["date"]).split("T")[0].split(" ")[0]
        elif isinstance(_exec_proposals.get("date_proposal"), dict):
            _dp_start = _exec_proposals["date_proposal"].get("start")
            if isinstance(_dp_start, str) and _dp_start:
                search_date = _dp_start.split("T")[0].split(" ")[0]

        _resolution_payload = resolve_time_after_availability(
            offers=execution_result.get("slots") or [],
            time_proposal=_exec_proposals.get("time_proposal"),
            date_proposal=_exec_proposals.get("date_proposal"),
            search_date=search_date,
            slots=slots,
        )
        _time_resolution = _resolution_payload.get("time_resolution")
        if isinstance(_time_resolution, dict):
            execution_result["time_resolution"] = _time_resolution
        _bind_result = _resolution_payload.get("bind_result")
        _resolution_outcome = (
            _time_resolution.get("outcome")
            if isinstance(_time_resolution, dict)
            else None
        )
        if (
            _resolution_outcome == TIME_MATCH_EXACT
            and isinstance(_bind_result, dict)
            and _bind_result
        ):
            execution_result["resolved_datetime_range"] = _bind_result.get(
                "resolved_datetime_range"
            )
            slots = _bind_result.get("slots") or slots
            plan["slots"] = slots
            apply_time_match_exact_to_plan(
                plan,
                bind_result=_bind_result,
                time_resolution=_time_resolution,
            )
            session_state = _persist_to_session(
                session_store,
                user_id,
                session_state or {},
                "resolved_datetime_range",
                _bind_result.get("resolved_datetime_range"),
            )
            try:
                from core.session.confirmation_gate import set_confirmation_state

                session_state = set_confirmation_state(session_state or {}, "pending")
            except ImportError:
                pass
        elif _resolution_outcome == TIME_MATCH_MISMATCH and isinstance(
            _time_resolution, dict
        ):
            apply_time_match_mismatch_to_plan(
                plan,
                time_resolution=_time_resolution,
                time_proposal=_exec_proposals.get("time_proposal"),
            )

        # ---- presentation payloads --------------------------------------
        last_execution_payload = enrich_last_execution_result(
            execution_result, search_date=search_date
        )
        if isinstance(_time_resolution, dict):
            last_execution_payload["time_resolution"] = _time_resolution
        presented_payload = build_presented_availability(
            execution_result.get("slots") or [],
            search_date=last_execution_payload.get("search_date") or search_date,
        )
        session_state = _persist_to_session(
            session_store,
            user_id,
            session_state or {},
            "last_execution_result",
            last_execution_payload,
        )
        session_state = _persist_to_session(
            session_store,
            user_id,
            session_state or {},
            "presented_availability",
            presented_payload,
        )
        presentation_payload = build_availability_presentation(
            execution_result.get("slots") or []
        )
        session_state = _persist_to_session(
            session_store,
            user_id,
            session_state or {},
            "availability_presentation",
            presentation_payload,
        )

        # ---- attach fingerprint / datetime_range to plan ----------------
        # Ensures these survive even when session_store is None
        # (build_session_state_from_outcome reads them from plan).
        if execution_result.get("availability_fingerprint"):
            plan["availability_fingerprint"] = execution_result["availability_fingerprint"]
            logger.debug(
                "[AVAILABILITY_FINGERPRINT] Attached to plan: %s",
                execution_result["availability_fingerprint"],
            )
        if execution_result.get("resolved_datetime_range"):
            plan["resolved_datetime_range"] = execution_result["resolved_datetime_range"]
            logger.debug(
                "[DATETIME_RANGE] Attached to plan: %s",
                execution_result["resolved_datetime_range"].get("start"),
            )

        return slots, session_state
