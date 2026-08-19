"""Project execution/planning outcomes onto canonical Session V2 state."""

from __future__ import annotations

import copy
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
        post_commit_transition: Optional[Dict[str, Any]] = None,
        capability_result: Optional[Dict[str, Any]] = None,
        handler_conversation_update: Optional[Dict[str, Any]] = None,
        conversation_messages: Optional[List[Dict[str, Any]]] = None,
        assistant_proposals: Optional[List[Dict[str, Any]]] = None,
        assistant_proposal_updates: Optional[List[Dict[str, Any]]] = None,
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
                assistant_proposals=assistant_proposals,
                assistant_proposal_updates=assistant_proposal_updates,
                organization_id=organization_id,
                user_id=user_id,
            )
            if pure_v2 is None:
                return None
            working = hydrate_v1_compat_shims(pure_v2)

        self._apply_optional_inputs(
            working,
            working_session_state=working_session_state,
            merged_luma_response=merged_luma_response,
            workflow_result=workflow_result,
            post_commit_transition=post_commit_transition,
            capability_result=capability_result,
            handler_conversation_update=handler_conversation_update,
            conversation_messages=conversation_messages,
            assistant_proposals=assistant_proposals,
            assistant_proposal_updates=assistant_proposal_updates,
            # Digressions (RAG HANDLER_DELEGATED, Core OFF_TOPIC) must not clear
            # booking authorization / bound datetime from an empty NLU payload.
            preserve_booking_authorization=(
                outcome_status in ("HANDLER_DELEGATED", "OFF_TOPIC")
            ),
        )
        return working

    @staticmethod
    def _apply_optional_inputs(
        working: Dict[str, Any],
        *,
        working_session_state: Optional[Dict[str, Any]],
        merged_luma_response: Optional[Dict[str, Any]],
        workflow_result: Optional[Dict[str, Any]],
        post_commit_transition: Optional[Dict[str, Any]],
        capability_result: Optional[Dict[str, Any]],
        handler_conversation_update: Optional[Dict[str, Any]],
        conversation_messages: Optional[List[Dict[str, Any]]],
        assistant_proposals: Optional[List[Dict[str, Any]]],
        assistant_proposal_updates: Optional[List[Dict[str, Any]]],
        preserve_booking_authorization: bool = False,
    ) -> None:
        """Apply explicit same-turn artifacts without consulting storage."""
        availability = working.setdefault("availability", {})
        cache = availability.setdefault("cache", {})
        presentation = availability.setdefault("presentation", {})

        if isinstance(working_session_state, dict):
            if isinstance(working_session_state.get("metadata"), dict):
                working["metadata"] = copy.deepcopy(working_session_state["metadata"])
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
            if isinstance(working_session_state.get("customer_contact"), dict):
                working["customer_contact"] = copy.deepcopy(
                    working_session_state["customer_contact"]
                )
            source_planning = working_session_state.get("planning")
            if isinstance(source_planning, dict):
                working.setdefault("planning", {})["pending_profile_request"] = (
                    source_planning.get("pending_profile_request")
                )

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

            set_confirmation_state(working, get_confirmation_state(merged_luma_response))

        if isinstance(workflow_result, dict):
            # Workflow envelope still uses flat key names for one-turn transfer;
            # map into nested availability only.
            is_availability_search = (
                workflow_result.get("kind") == "availability_search"
            )
            search_result = workflow_result.get("last_execution_result")
            authoritative_search = (
                is_availability_search
                and workflow_result.get("status") == "succeeded"
                and search_result is not None
            )

            if is_availability_search and not authoritative_search:
                from core.workflows.availability.presentation import (
                    clear_availability_artifacts,
                )

                clear_availability_artifacts(working)
                metadata = working.setdefault("metadata", {})
                metadata.setdefault("artifacts", {})["availability"] = None
                workflow_result = {}
                search_result = None

            fingerprint = workflow_result.get("availability_fingerprint")
            if fingerprint is not None:
                availability["fingerprint"] = fingerprint

            if search_result is not None:
                cache["search_result"] = search_result

            # The availability workflow owns this typed result discriminator.
            # Pagination and unrelated execution results must preserve cache age.
            if authoritative_search:
                from core.session.freshness import stamp_availability_created

                stamp_availability_created(working)

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
        source_conversation = (
            working_session_state.get("conversation")
            if isinstance(working_session_state, dict)
            else None
        )
        if (
            isinstance(source_conversation, dict)
            and isinstance(source_conversation.get("pending_proposals"), list)
            and not conversation.get("pending_proposals")
        ):
            conversation["pending_proposals"] = copy.deepcopy(
                source_conversation["pending_proposals"]
            )
        if (
            isinstance(source_conversation, dict)
            and isinstance(source_conversation.get("history"), list)
            and not conversation.get("history")
        ):
            conversation["history"] = copy.deepcopy(source_conversation["history"])
            working["messages"] = copy.deepcopy(source_conversation["history"])
        if isinstance(assistant_proposals, list):
            conversation["pending_proposals"] = copy.deepcopy(assistant_proposals)
        if isinstance(assistant_proposal_updates, list):
            updates = {
                item.get("proposal_id"): item.get("status")
                for item in assistant_proposal_updates
                if isinstance(item, dict) and item.get("proposal_id") and item.get("status")
            }
            for proposal in conversation.get("pending_proposals") or []:
                if isinstance(proposal, dict) and proposal.get("proposal_id") in updates:
                    proposal["status"] = updates[proposal["proposal_id"]]
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

        # Apply lifecycle closure last so no optional compatibility overlay can
        # restore authorization after a successful create.
        if isinstance(post_commit_transition, dict):
            from core.session.booking_lifecycle import apply_post_commit_transition_v2

            apply_post_commit_transition_v2(working, post_commit_transition)

        from core.session.freshness import sync_confirmation_freshness

        sync_confirmation_freshness(working)
