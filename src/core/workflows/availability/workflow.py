"""AvailabilityWorkflow — availability domain boundary.

Owns browse/pagination, fingerprints, and post-search processing.
Tool dispatch (SEARCH_AVAILABILITY) is owned by the execution dispatcher;
this workflow must not initiate execution.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class AvailabilityWorkflow:
    """Availability-domain operations after planning/eligibility.

    Browse short-circuit and post-search processing only.
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
        organization_id: int,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Attempt to handle a browse/pagination turn.

        Returns a full turn result when the turn is a browse operation,
        or None when normal planning is required.
        """
        from core.workflows.availability.pagination import (
            try_handle_availability_browse_turn,
        )

        return try_handle_availability_browse_turn(
            plan=plan,
            session_state=session_state,
            session_store=session_store,
            organization_id=organization_id,
            user_id=user_id,
        )

    # ------------------------------------------------------------------
    # Fingerprint
    # ------------------------------------------------------------------

    def compute_fingerprint(self, slots: Dict[str, Any]) -> str:
        """Compute the canonical availability fingerprint for *slots*."""
        from core.workflows.availability.fingerprint import (
            compute_availability_fingerprint,
        )

        return compute_availability_fingerprint(slots)

    def slots_match_fingerprint(
        self,
        slots: Dict[str, Any],
        fingerprint: str,
    ) -> bool:
        """Return True when *slots* produce the same fingerprint as stored."""
        from core.workflows.availability.fingerprint import (
            slots_match_availability_fingerprint,
        )

        return slots_match_availability_fingerprint(slots, fingerprint)

    # ------------------------------------------------------------------
    # Post-search result processing
    # ------------------------------------------------------------------

    def process_search_result(
        self,
        execution_result: Dict[str, Any],
        plan: Dict[str, Any],
        slots: Dict[str, Any],
        session_state: Optional[Dict[str, Any]],
        session_store: Optional[Any],
        user_id: str,
        organization_id: int,
    ) -> Tuple[
        Dict[str, Any],
        Optional[Dict[str, Any]],
        Dict[str, Any],
    ]:
        """Process a successful availability search result in-place.

        Computes the fingerprint, resolves time matching, and returns explicit
        projection artifacts. Session materialization belongs to SessionProjector.

        Mutates *execution_result* and *plan* in-place.
        Returns updated slots, working session, and the explicit projection
        artifact produced by availability post-processing.
        """
        _ = session_store, user_id  # Compatibility-only; no workflow storage I/O.
        from core.workflows.availability.fingerprint import (
            build_availability_fingerprint_slots,
            compute_availability_fingerprint,
        )
        from core.planning.temporal_proposal import (
            enrich_last_execution_result,
            resolve_execution_proposals,
        )
        from core.planning.time_resolution import (
            TIME_MATCH_EXACT,
            TIME_MATCH_MISMATCH,
            resolve_time_after_availability,
        )
        from core.workflows.availability.discovery.bridge import (
            present_via_discovery,
            search_via_discovery,
        )
        from core.workflows.availability.presentation import (
            presentation_meta_from_presented,
            resolve_criteria_span,
        )

        availability = execution_result.get("availability")
        if not isinstance(availability, dict):
            return slots, session_state, {}
        session_state = session_state if isinstance(session_state, dict) else {}
        workflow_result: Dict[str, Any] = {"kind": "availability_search"}
        offers = availability.get("slots")
        if not isinstance(offers, list):
            offers = []
            availability["slots"] = offers

        # ---- fingerprint ------------------------------------------------
        plan_intent_name = plan.get("intent_name") or plan.get("intent")

        _exec_proposals = resolve_execution_proposals(
            plan,
            session_state,
            context=plan.get("execution_proposal_context"),
        )
        entity_schema = None
        plan_facts = plan.get("facts")
        if isinstance(plan_facts, dict) and isinstance(
            plan_facts.get("_entity_schema"), dict
        ):
            entity_schema = plan_facts.get("_entity_schema")
        elif isinstance(plan.get("_entity_schema"), dict):
            entity_schema = plan.get("_entity_schema")

        fingerprint_slots = build_availability_fingerprint_slots(
            slots,
            intent_name=plan_intent_name,
            organization_id=organization_id,
            date_proposal=_exec_proposals["date_proposal"],
            time_proposal=_exec_proposals["time_proposal"],
            session_state=session_state,
            temporal=plan.get("temporal")
            if isinstance(plan.get("temporal"), dict)
            else None,
            entity_schema=entity_schema,
        )
        availability_fingerprint = compute_availability_fingerprint(
            fingerprint_slots,
            intent_name=plan_intent_name,
            entity_schema=entity_schema,
        )

        if availability_fingerprint:
            workflow_result["availability_fingerprint"] = availability_fingerprint
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

        # After an availability-affecting revision, only current-turn explicit
        # time may rematch. Stale session proposals must not auto-confirm.
        _proposal_ctx = plan.get("execution_proposal_context")
        if not isinstance(_proposal_ctx, dict):
            _proposal_ctx = {}
        _criteria_invalidated = bool(_proposal_ctx.get("availability_invalidated"))
        _bound_datetime_cleared = bool(_proposal_ctx.get("bound_datetime_cleared"))
        _current_turn_time = bool(
            _proposal_ctx.get("current_turn_has_explicit_time")
        )
        _time_proposal_for_match = _exec_proposals.get("time_proposal")
        if (
            (_criteria_invalidated or _bound_datetime_cleared)
            and not _current_turn_time
        ):
            _time_proposal_for_match = None

        _resolution_payload = resolve_time_after_availability(
            offers=offers,
            time_proposal=_time_proposal_for_match,
            date_proposal=_exec_proposals.get("date_proposal"),
            search_date=search_date,
            slots=slots,
        )
        _time_resolution = _resolution_payload.get("time_resolution")
        if isinstance(_time_resolution, dict):
            availability["time_resolution"] = _time_resolution
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
            resolved_range = _bind_result.get("resolved_datetime_range")
            subject = execution_result.get("subject")
            if isinstance(subject, dict) and isinstance(resolved_range, dict):
                subject["starts_at"] = resolved_range.get("start")
                subject["ends_at"] = resolved_range.get("end")
            slots = _bind_result.get("slots") or slots
            plan["slots"] = slots
            from core.planning.pipeline.requests import (
                is_availability_turn_operation,
            )

            availability_op = is_availability_turn_operation(plan.get("turn_operation"))
            from core.planning.pipeline.decision_finalization import (
                TimeResolutionEvidence,
                finalize_decision_after_time_resolution,
            )

            # Criteria revision / bound-clear without current-turn time: present offers only.
            enter_confirmation = not availability_op and not (
                (_criteria_invalidated or _bound_datetime_cleared)
                and not _current_turn_time
            )
            finalize_decision_after_time_resolution(
                plan,
                evidence=TimeResolutionEvidence(
                    outcome=TIME_MATCH_EXACT,
                    time_resolution=_time_resolution,
                    bind_result=_bind_result,
                    enter_confirmation=enter_confirmation,
                    apply_confirmation_transition=enter_confirmation,
                ),
            )
            if enter_confirmation:
                workflow_result["resolved_datetime_range"] = _bind_result.get(
                    "resolved_datetime_range"
                )
        elif _resolution_outcome == TIME_MATCH_MISMATCH and isinstance(
            _time_resolution, dict
        ):
            from core.planning.pipeline.decision_finalization import (
                TimeResolutionEvidence,
                finalize_decision_after_time_resolution,
            )

            finalize_decision_after_time_resolution(
                plan,
                evidence=TimeResolutionEvidence(
                    outcome=TIME_MATCH_MISMATCH,
                    time_resolution=_time_resolution,
                    time_proposal=_time_proposal_for_match,
                    apply_confirmation_transition=True,
                ),
            )

        # ---- presentation payloads (Discovery Search + Navigator) -------
        legacy_availability_result: Dict[str, Any] = {
            "type": "availability",
            "status": "success",
            "slots": offers,
        }
        if availability_fingerprint:
            legacy_availability_result[
                "availability_fingerprint"
            ] = availability_fingerprint
        last_execution_payload = enrich_last_execution_result(
            legacy_availability_result, search_date=search_date
        )
        if isinstance(_time_resolution, dict):
            last_execution_payload["time_resolution"] = _time_resolution

        fingerprint_criteria = dict(fingerprint_slots)
        if availability_fingerprint:
            # Keep identity aligned with the fingerprint already computed above.
            fingerprint_criteria.setdefault("service_id", slots.get("service_id"))

        _, span_start, _ = resolve_criteria_span(
            slots=slots,
            date_proposal=_exec_proposals.get("date_proposal"),
            fingerprint_slots=fingerprint_slots,
            search_date=search_date or last_execution_payload.get("search_date"),
        )
        # search_date is execution metadata on the trusted cache; span is not persisted.
        if span_start:
            last_execution_payload["search_date"] = span_start

        def _execute_search(_criteria: Dict[str, Any]) -> list:
            return list(offers)

        discovery_cache, _ = search_via_discovery(
            fingerprint_criteria,
            execute_search=_execute_search,
            existing_cache=None,
        )
        if availability_fingerprint:
            discovery_cache["fingerprint"] = availability_fingerprint
        resolved_search_date = (
            last_execution_payload.get("search_date") or search_date
        )
        if resolved_search_date:
            discovery_cache["search_date"] = resolved_search_date
        if isinstance(_time_resolution, dict):
            discovery_cache["time_resolution"] = _time_resolution

        presented_payload = present_via_discovery(
            discovery_cache,
            search_date=resolved_search_date,
            slots=slots,
            date_proposal=_exec_proposals.get("date_proposal"),
            fingerprint_slots=fingerprint_slots,
        )
        presentation_payload = presentation_meta_from_presented(presented_payload)
        workflow_result.update(
            {
                "last_execution_result": last_execution_payload,
                "presented_availability": presented_payload,
                "availability_presentation": presentation_payload,
            }
        )

        # ---- attach fingerprint / datetime_range to plan ----------------
        # Ensures these survive even when session_store is None
        # (build_session_state_from_outcome reads them from plan).
        if availability_fingerprint:
            plan["availability_fingerprint"] = availability_fingerprint
            logger.debug(
                "[AVAILABILITY_FINGERPRINT] Attached to plan: %s",
                availability_fingerprint,
            )
        resolved_range = _bind_result.get("resolved_datetime_range") if isinstance(
            _bind_result, dict
        ) else None
        if isinstance(resolved_range, dict):
            plan["resolved_datetime_range"] = resolved_range
            logger.debug(
                "[DATETIME_RANGE] Attached to plan: %s",
                resolved_range.get("start"),
            )

        return slots, session_state, workflow_result
