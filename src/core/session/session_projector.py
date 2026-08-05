"""Project execution/planning outcomes onto canonical Session V2 state."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.session.session_schema_v2 import (
    hydrate_v1_compat_shims,
    prepare_session_for_load,
)
from core.session.session_v2_projection import project_session_v2


class SessionProjectorV2:
    """Deterministic, side-effect-free projector for canonical Session V2.

    Inputs:
    - ``previous_session_state``: immutable request-start session (V1 or V2)
    - ``working_session_state``: request-scoped in-memory Session V2
    - ``outcome`` / ``outcome_status``: planning result and lifecycle status
    - ``merged_luma_response``: NLU merge payload used by persistence
    - ``workflow_result``: explicit workflow/browse mutation artifact
    - ``capability_result``: optional capability continuation artifact
    - ``handler_conversation_update``: delegated-handler conversation memory
    - ``conversation_messages``: rendered user/assistant history entries

    Output:
    - Working session dict with ``schema_version=2`` nested sections plus legacy
      compatibility mirrors for in-memory consumers. Pure V2 is produced at
      save time via ``prepare_session_for_persist``.

    Production path projects canonical V2 via ``project_session_v2`` then
    ``hydrate_v1_compat_shims``.

    Does not call Redis or any session store directly.
    """

    def project(
        self,
        outcome: Dict[str, Any],
        outcome_status: str,
        organization_id: int,
        merged_luma_response: Optional[Dict[str, Any]] = None,
        previous_session_state: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        session_store: Optional[Any] = None,
        *,
        working_session_state: Optional[Dict[str, Any]] = None,
        workflow_result: Optional[Dict[str, Any]] = None,
        capability_result: Optional[Dict[str, Any]] = None,
        handler_conversation_update: Optional[Dict[str, Any]] = None,
        conversation_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        _ = session_store  # Compatibility-only; projection never performs storage I/O.
        projection_outcome = dict(outcome)
        if isinstance(workflow_result, dict):
            projection_outcome["_workflow_result"] = workflow_result

        if outcome_status == "HANDLER_DELEGATED" or outcome_status == "OFF_TOPIC":
            base = working_session_state or previous_session_state or {}
            working = prepare_session_for_load(base)
        else:
            pure_v2 = project_session_v2(
                previous_session_state=previous_session_state,
                working_session_state=working_session_state,
                outcome=projection_outcome,
                outcome_status=outcome_status,
                merged_luma_response=merged_luma_response,
                workflow_result=workflow_result,
                capability_result=capability_result,
                handler_conversation_update=handler_conversation_update,
                conversation_messages=conversation_messages,
                organization_id=organization_id,
                user_id=user_id,
            )
            if pure_v2 is None:
                return None
            working = hydrate_v1_compat_shims(pure_v2)

        prior_for_auth = previous_session_state or working_session_state
        from core.session.confirmation_gate import (
            get_confirmation_state as _get_conf,
            set_confirmation_state as _set_conf,
        )

        def _identity_reconfirm_signal(state: Optional[Dict[str, Any]]) -> bool:
            if not isinstance(state, dict):
                return False
            if state.get("identity_reconfirm_required"):
                return True
            booking = state.get("booking")
            return isinstance(booking, dict) and bool(
                booking.get("identity_reconfirm_required")
            )

        # Only CUSTOMER_ID_REQUIRED (stamped identity_reconfirm_required) keeps
        # pending across NEEDS_CLARIFICATION — not reject / normal clarify.
        preserve_pending_identity_block = (
            outcome_status == "NEEDS_CLARIFICATION"
            and _get_conf(prior_for_auth) == "pending"
            and (
                _identity_reconfirm_signal(merged_luma_response)
                or _identity_reconfirm_signal(working_session_state)
            )
        )
        self._apply_optional_inputs(
            working,
            working_session_state=working_session_state,
            merged_luma_response=merged_luma_response,
            workflow_result=workflow_result,
            capability_result=capability_result,
            handler_conversation_update=handler_conversation_update,
            conversation_messages=conversation_messages,
            # Digressions (RAG HANDLER_DELEGATED, Core OFF_TOPIC) must not clear
            # booking authorization / bound datetime from an empty NLU payload.
            # Identity-blocked confirm also keeps pending + bound datetime.
            preserve_booking_authorization=(
                outcome_status in ("HANDLER_DELEGATED", "OFF_TOPIC")
                or preserve_pending_identity_block
            ),
        )
        if preserve_pending_identity_block and isinstance(prior_for_auth, dict):
            # V2 assembly may have dropped pending; restore from pre-turn session.
            _set_conf(working, "pending")
            prior_bound = prior_for_auth.get("resolved_datetime_range")
            if not isinstance(prior_bound, dict):
                prior_planning = prior_for_auth.get("planning")
                if isinstance(prior_planning, dict):
                    prior_bound = prior_planning.get("bound_datetime")
            if isinstance(prior_bound, dict) and prior_bound.get("start"):
                planning = working.setdefault("planning", {})
                if isinstance(planning, dict):
                    planning["bound_datetime"] = prior_bound
                working["resolved_datetime_range"] = prior_bound
            if prior_for_auth.get("identity_reconfirm_required") or (
                isinstance(merged_luma_response, dict)
                and merged_luma_response.get("identity_reconfirm_required")
            ):
                working["identity_reconfirm_required"] = True
                booking = working.setdefault("booking", {})
                if isinstance(booking, dict):
                    booking["identity_reconfirm_required"] = True
        elif isinstance(merged_luma_response, dict) and merged_luma_response.get(
            "identity_reconfirm_required"
        ):
            working["identity_reconfirm_required"] = True
            booking = working.setdefault("booking", {})
            if isinstance(booking, dict):
                booking["identity_reconfirm_required"] = True
        elif isinstance(working_session_state, dict) and (
            "identity_reconfirm_required" in working_session_state
            or (
                isinstance(working_session_state.get("booking"), dict)
                and "identity_reconfirm_required"
                in working_session_state["booking"]
            )
        ):
            # Propagate Stage 01 clear/set — including False after identity resume.
            flag = bool(working_session_state.get("identity_reconfirm_required"))
            nested = working_session_state.get("booking")
            if isinstance(nested, dict) and "identity_reconfirm_required" in nested:
                flag = bool(nested.get("identity_reconfirm_required"))
            working["identity_reconfirm_required"] = flag
            booking = working.setdefault("booking", {})
            if isinstance(booking, dict):
                booking["identity_reconfirm_required"] = flag
        return working

    @staticmethod
    def _apply_optional_inputs(
        working: Dict[str, Any],
        *,
        working_session_state: Optional[Dict[str, Any]],
        merged_luma_response: Optional[Dict[str, Any]],
        workflow_result: Optional[Dict[str, Any]],
        capability_result: Optional[Dict[str, Any]],
        handler_conversation_update: Optional[Dict[str, Any]],
        conversation_messages: Optional[List[Dict[str, Any]]],
        preserve_booking_authorization: bool = False,
    ) -> None:
        """Apply explicit same-turn artifacts without consulting storage."""
        availability = working.setdefault("availability", {})
        cache = availability.setdefault("cache", {})
        presentation = availability.setdefault("presentation", {})

        if isinstance(working_session_state, dict):
            from core.workflows.availability.presentation import (
                apply_availability_artifacts,
                availability_cache_from_session,
                availability_fingerprint_from_session,
                availability_pagination_from_session,
                presented_availability_from_session,
            )

            # Canonical accessors own nested-vs-historical-flat resolution.
            apply_availability_artifacts(
                working,
                fingerprint=availability_fingerprint_from_session(
                    working_session_state
                ),
                search_result=availability_cache_from_session(
                    working_session_state
                ),
                presented=presented_availability_from_session(
                    working_session_state
                ),
                presentation=availability_pagination_from_session(
                    working_session_state
                ),
            )

            if working_session_state.get("customer_id") is not None:
                working["customer_id"] = working_session_state["customer_id"]

        if isinstance(merged_luma_response, dict) and not preserve_booking_authorization:
            from core.session.confirmation_gate import (
                get_confirmation_state,
                set_confirmation_state,
            )

            merged_bound = merged_luma_response.get("resolved_datetime_range")
            planning = working.setdefault("planning", {})
            if isinstance(merged_bound, dict) and merged_bound.get("start"):
                planning["bound_datetime"] = merged_bound
                working["resolved_datetime_range"] = merged_bound
            else:
                planning["bound_datetime"] = None
                working.pop("resolved_datetime_range", None)

            set_confirmation_state(working, get_confirmation_state(merged_luma_response))

        if isinstance(workflow_result, dict):
            # Workflow envelope still uses flat key names for one-turn transfer;
            # map into nested availability only.
            fingerprint = workflow_result.get("availability_fingerprint")
            if fingerprint is not None:
                availability["fingerprint"] = fingerprint

            search_result = workflow_result.get("last_execution_result")
            if search_result is not None:
                cache["search_result"] = search_result

            presented = workflow_result.get("presented_availability")
            if presented is not None:
                presentation["presented"] = presented

            presentation_payload = workflow_result.get("availability_presentation")
            if isinstance(presentation_payload, dict):
                presentation["page_index"] = presentation_payload.get("page_index", 0)
                presentation["page_size"] = presentation_payload.get("page_size")

            bound_datetime = workflow_result.get("resolved_datetime_range")
            if bound_datetime is not None:
                working.setdefault("planning", {})["bound_datetime"] = bound_datetime
                working["resolved_datetime_range"] = bound_datetime

        if isinstance(capability_result, dict):
            capability = working.setdefault("capability", {})
            if capability.get("active") is None and capability_result.get("active"):
                capability["active"] = capability_result.get("active")
            results = capability.setdefault("results", {})
            for key, value in capability_result.items():
                if key != "active" and key not in results and value is not None:
                    results[key] = value

        conversation = working.setdefault("conversation", {})
        if isinstance(handler_conversation_update, dict):
            memory = handler_conversation_update.get("memory")
            if isinstance(memory, dict):
                conversation["memory"] = memory
            elif "turns" in handler_conversation_update:
                conversation["memory"] = handler_conversation_update

        if isinstance(conversation_messages, list):
            history = list(conversation.get("history") or working.get("messages") or [])
            history.extend(
                message for message in conversation_messages if isinstance(message, dict)
            )
            history = history[-10:]
            conversation["history"] = history
            working["messages"] = list(history)
