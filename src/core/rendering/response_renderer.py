"""Single entry point for rendering planning and canonical execution results."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.rendering.llm_renderer import LlmRenderRequest, render_llm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helper — used by all injection functions below
# ---------------------------------------------------------------------------

def _structured_context_from_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Extract business context dict from a decision's facts.org."""
    org = {}
    facts = decision.get("facts") if isinstance(decision, dict) else None
    if isinstance(facts, dict):
        org = facts.get("org") or {}
    if not isinstance(org, dict):
        org = {}
    return {
        "business_name": org.get("businessName") or org.get("business_name"),
        "business_about": org.get("businessAbout") or org.get("about") or org.get("business_about"),
        "business_phone": org.get("businessPhone") or org.get("business_phone"),
    }


# ---------------------------------------------------------------------------
# Rendering injection helpers (extracted from orchestrator.py)
# ---------------------------------------------------------------------------

def _inject_rendering_text(
    result: Dict[str, Any],
    decision: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
) -> None:
    """Inject clarification/missing-slot LLM text into result (best-effort)."""
    try:
        from core.tracing.decision_trace import measure_stage
    except ImportError:
        from contextlib import contextmanager

        @contextmanager  # type: ignore[misc]
        def measure_stage(_stage: str):
            yield

    with measure_stage("renderer"):
        _inject_rendering_text_impl(result, decision, session_state=session_state)


def _service_name_from_decision_or_session(
    decision: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Best-effort service label already present on the turn (never invents)."""
    for source in (
        (decision.get("facts") or {}).get("slots")
        if isinstance(decision.get("facts"), dict)
        else None,
        decision.get("slots"),
        (decision.get("plan") or {}).get("slots")
        if isinstance(decision.get("plan"), dict)
        else None,
        (session_state or {}).get("slots") if isinstance(session_state, dict) else None,
        ((session_state or {}).get("planning") or {}).get("slots")
        if isinstance(session_state, dict)
        and isinstance((session_state or {}).get("planning"), dict)
        else None,
    ):
        if isinstance(source, dict) and source.get("service_id"):
            return str(source["service_id"])
    return None


def _time_resolution_from_decision(decision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    resolution = decision.get("time_resolution")
    if isinstance(resolution, dict):
        return resolution
    plan = decision.get("plan")
    if isinstance(plan, dict) and isinstance(plan.get("time_resolution"), dict):
        return plan["time_resolution"]
    facts = decision.get("facts")
    if isinstance(facts, dict) and isinstance(facts.get("time_resolution"), dict):
        return facts["time_resolution"]
    return None


def _mismatch_fallback_text(time_resolution: Optional[Dict[str, Any]]) -> str:
    """Deterministic wording when LLM mismatch render produces no text."""
    from core.rendering.availability_renderer import resolve_time_mismatch_text

    if not isinstance(time_resolution, dict):
        return resolve_time_mismatch_text()
    alternatives = time_resolution.get("alternatives") or []
    return resolve_time_mismatch_text(
        requested_time=(
            str(time_resolution["requested_time"])
            if time_resolution.get("requested_time") is not None
            else None
        ),
        alternatives=list(alternatives) if isinstance(alternatives, list) else None,
        mismatch_location=(
            str(time_resolution["mismatch_location"])
            if time_resolution.get("mismatch_location") is not None
            else None
        ),
        search_date=(
            str(time_resolution["search_date"])
            if time_resolution.get("search_date") is not None
            else None
        ),
        recovery_actions=(
            time_resolution.get("recovery_actions")
            if isinstance(time_resolution.get("recovery_actions"), list)
            else None
        ),
    )


def _apply_mismatch_render_text(result: Dict[str, Any], text: str) -> None:
    if not text or not str(text).strip():
        return
    result["text"] = text
    if isinstance(result.get("outcome"), dict):
        result["outcome"]["text"] = text


def _inject_time_match_mismatch_text(
    result: Dict[str, Any],
    decision: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
) -> None:
    """Render TIME_MATCH_MISMATCH clarification; never fall through to missing-slots."""
    from core.planning.time_resolution import (
        build_execution_result_for_time_resolution_render,
    )
    from core.rendering.availability_renderer import build_availability_render_request
    from core.workflows.availability.presentation import (
        ensure_presented_availability,
        presented_availability_from_session,
    )

    time_resolution = _time_resolution_from_decision(decision)
    service_name = _service_name_from_decision_or_session(decision, session_state)
    exec_payload = build_execution_result_for_time_resolution_render(
        session_state if isinstance(session_state, dict) else None,
        time_resolution=time_resolution,
        service_name=service_name,
    )
    if exec_payload is None and isinstance(time_resolution, dict):
        # Still render from decision evidence alone when session cache is absent.
        exec_payload = {
            "status": "succeeded",
            "availability": {
                "slots": [],
                "time_resolution": time_resolution,
            },
            "subject": {"service_name": service_name or "your appointment"},
        }

    if exec_payload:
        conversation_history = (session_state or {}).get("messages", []) if isinstance(
            session_state, dict
        ) else []
        avail = exec_payload.get("availability") or {}
        presented = presented_availability_from_session(session_state)
        if presented is None:
            presented = ensure_presented_availability(
                session_state=session_state,
                raw_slots=avail.get("slots"),
                search_date=avail.get("search_date")
                if isinstance(avail, dict)
                else None,
            )
        if presented is None:
            presented = {
                "search_date": avail.get("search_date")
                if isinstance(avail, dict)
                else None,
                "slots": list(avail.get("slots") or [])
                if isinstance(avail, dict)
                else [],
                "times": [],
                "more_count": 0,
                "total_unique": 0,
            }
        render_request = build_availability_render_request(
            decision,
            exec_payload,
            presented=presented,
            structured_context=_structured_context_from_decision(decision),
            conversation_history=conversation_history,
            time_resolution=time_resolution,
        )
        if render_request:
            rendered_text = render_llm(render_request)
            if rendered_text:
                _apply_mismatch_render_text(result, rendered_text)

    if not (isinstance(result.get("text"), str) and result["text"].strip()):
        _apply_mismatch_render_text(result, _mismatch_fallback_text(time_resolution))


def _inject_rendering_text_impl(
    result: Dict[str, Any],
    decision: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
) -> None:
    from core.planning.time_resolution import TIME_MATCH_MISMATCH

    time_match_outcome = (
        decision.get("time_match_outcome")
        or decision.get("plan", {}).get("time_match_outcome")
        or (decision.get("facts") or {}).get("time_match_outcome")
    )
    awaiting = (
        decision.get("awaiting")
        or decision.get("plan", {}).get("awaiting")
        or (decision.get("facts") or {}).get("awaiting")
    )
    # Hard guardrail: mismatch / TIME_SELECTION clarification must never fall
    # through to empty missing-slots clarification (which returns with no text).
    if time_match_outcome == TIME_MATCH_MISMATCH or awaiting == "TIME_SELECTION":
        try:
            _inject_time_match_mismatch_text(result, decision, session_state)
        except Exception as e:
            logger.warning(
                "Failed to render time-mismatch text: %s. Using deterministic fallback.",
                e,
            )
            if not (isinstance(result.get("text"), str) and result["text"].strip()):
                _apply_mismatch_render_text(
                    result,
                    _mismatch_fallback_text(_time_resolution_from_decision(decision)),
                )
        return

    try:
        decision["_session"] = session_state or {}
        if session_state and isinstance(session_state, dict):
            slot_attempts = session_state.get("slot_attempts")
            if isinstance(slot_attempts, dict):
                decision["slot_attempts"] = slot_attempts
                facts = decision.get("facts", {})
                if isinstance(facts, dict):
                    facts["slot_attempts"] = slot_attempts

        facts = decision.get("facts", {})
        plan = decision.get("plan") if isinstance(decision.get("plan"), dict) else {}
        missing_slots = next(
            (
                list(value)
                for value in (
                    decision.get("missing_slots"),
                    plan.get("missing_slots"),
                    facts.get("missing_slots") if isinstance(facts, dict) else None,
                )
                if isinstance(value, list)
            ),
            [],
        )
        promptable_slots = next(
            (
                list(value)
                for value in (
                    decision.get("promptable_slots"),
                    plan.get("promptable_slots"),
                    facts.get("promptable_slots") if isinstance(facts, dict) else None,
                )
                if isinstance(value, list)
            ),
            [],
        )
        if not missing_slots and not promptable_slots:
            # Still allow ask_next-only promptable clarification
            ask_probe = (
                decision.get("ask_next")
                or (decision.get("plan") or {}).get("ask_next")
                or (facts.get("ask_next") if isinstance(facts, dict) else None)
            )
            if not (isinstance(ask_probe, str) and ask_probe.strip()):
                return
        intent_name = (
            decision.get("intent_name")
            or decision.get("plan", {}).get("intent_name")
            or "your request"
        )
        slot_attempts = decision.get("slot_attempts") or {}
        if not isinstance(slot_attempts, dict):
            slot_attempts = {}
        selected_ask_next = (
            decision.get("ask_next")
            or plan.get("ask_next")
            or (facts.get("ask_next") if isinstance(facts, dict) else None)
        )
        valid_targets = {
            str(slot) for slot in (*missing_slots, *promptable_slots) if slot
        }
        ask_next = (
            selected_ask_next.strip()
            if isinstance(selected_ask_next, str)
            and selected_ask_next.strip() in valid_targets
            else None
        )
        if ask_next is None:
            from core.planning.planner.missing_slots import derive_ask_next

            ask_next = derive_ask_next(missing_slots, promptable_slots)
        if not ask_next:
            return
        attempt_count = slot_attempts.get(ask_next, 0)
        last_filled = (
            (session_state or {}).get("last_filled_slot") if session_state else None
        )
        ack_note = (
            f" Start by briefly acknowledging you received {last_filled}."
            if last_filled and attempt_count < 1
            else ""
        )
        retry_note = (
            " The user was already asked — rephrase naturally." if attempt_count >= 1 else ""
        )
        service_candidates = (
            decision.get("service_candidates")
            or decision.get("facts", {}).get("service_candidates")
            or []
        )
        from core.adapters.nlu.entity_schema_builder import (
            catalog_candidates_for_slot,
            description_for_planning_slot,
        )
        from core.planning.planner.promptable import catalog_labels_for_planning_slot

        entity_schema = None
        if isinstance(decision.get("_entity_schema"), dict):
            entity_schema = decision.get("_entity_schema")
        elif isinstance((decision.get("facts") or {}).get("_entity_schema"), dict):
            entity_schema = decision["facts"]["_entity_schema"]
        elif isinstance((session_state or {}).get("_entity_schema"), dict):
            entity_schema = session_state.get("_entity_schema")

        catalog_candidates = catalog_candidates_for_slot(
            decision, ask_next, entity_schema=entity_schema
        )
        if not catalog_candidates and ask_next == "service_id":
            catalog_candidates = (
                service_candidates if isinstance(service_candidates, list) else []
            )
        if not catalog_candidates:
            catalog_candidates = catalog_labels_for_planning_slot(
                entity_schema, ask_next
            )

        is_promptable_ask = ask_next in {
            str(s) for s in promptable_slots if s
        } or (
            ask_next
            and ask_next not in {str(s) for s in missing_slots if s}
            and ask_next
            in {
                str(s)
                for s in (
                    (decision.get("plan") or {}).get("promptable_slots") or []
                )
                if s
            }
        )

        render_missing = [ask_next]
        if catalog_candidates:
            labels = []
            for item in catalog_candidates:
                if isinstance(item, str) and item.strip():
                    labels.append(item.strip())
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("name") or item.get("id")
                    if isinstance(text, str) and text.strip():
                        labels.append(text.strip())
            if is_promptable_ask and "No preference" not in labels:
                labels.append("No preference")
            if labels:
                candidates_str = ", ".join(f'"{c}"' for c in labels)
                service_hint = (
                    f" Present these options for them to choose from: {candidates_str}."
                )
            else:
                service_hint = ""
        else:
            service_hint = ""
            if is_promptable_ask:
                service_hint = (
                    ' Include "No preference" as an explicit option they may choose.'
                )
        description = description_for_planning_slot(entity_schema, ask_next)
        field_hint = ""
        if (
            ask_next not in ("service_id", "date", "time", "date_range")
            and description
            and not service_hint
        ):
            field_hint = f" The missing field is: {description}."
        ask_kind = (
            "optional preference (they may choose No preference)"
            if is_promptable_ask
            else "specific missing fields"
        )
        render_instruction = (
            f"The user wants to {intent_name.lower().replace('_', ' ')}. "
            f"Ask ONLY for these {ask_kind} (nothing else): {', '.join(render_missing)}."
            f"{service_hint}{field_hint}{ack_note}{retry_note} "
            "Do not ask for any other information. Be natural and brief."
        )
        conversation_history = (session_state or {}).get("messages", [])
        rendered_text = render_llm(
            LlmRenderRequest(
                render_instruction=render_instruction,
                facts={
                    "structured_context": _structured_context_from_decision(decision),
                    "rendering_purpose": "clarification",
                    "ask_next": ask_next,
                    "missing_slots": list(missing_slots),
                    "promptable_slots": list(promptable_slots),
                },
                conversation_history=conversation_history,
            )
        )
        if rendered_text:
            result["text"] = rendered_text
    except Exception as e:
        logger.warning(
            "Failed to render clarification text: %s. Rendering is best-effort and will be omitted.",
            e,
        )


def _current_turn_presented(
    result: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Presented window produced by this turn's availability workflow, if any."""
    workflow_result = result.get("_workflow_result")
    if not isinstance(workflow_result, dict):
        return None
    presented = workflow_result.get("presented_availability")
    if isinstance(presented, dict) and isinstance(presented.get("slots"), list):
        return presented
    return None


def _inject_availability_text(
    result: Dict[str, Any],
    decision: Optional[Dict[str, Any]],
    execution_result: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
) -> None:
    """Inject rendered availability list text into result (best-effort)."""
    if (
        execution_result.get("status") != "succeeded"
        or not isinstance(execution_result.get("availability"), dict)
    ):
        return
    try:
        from core.rendering.availability_renderer import build_availability_render_request
        from core.workflows.availability.presentation import (
            ensure_presented_availability,
        )

        conversation_history = (session_state or {}).get("messages", [])
        # Prefer this turn's workflow presented window over session state.
        # SessionProjector has not persisted yet after SEARCH_AVAILABILITY, so
        # session.presented_availability can still be the previous search.
        presented = _current_turn_presented(result)
        if presented is None:
            avail = execution_result.get("availability") or {}
            presented = ensure_presented_availability(
                session_state=session_state,
                raw_slots=avail.get("slots"),
                search_date=(
                    avail.get("search_date") if isinstance(avail, dict) else None
                ),
            )
        if presented is None:
            return
        render_request = build_availability_render_request(
            decision,
            execution_result,
            presented=presented,
            structured_context=_structured_context_from_decision(decision or {}),
            conversation_history=conversation_history,
        )
        if not render_request:
            return
        rendered_text = render_llm(render_request)
        if rendered_text:
            from core.rendering.booking_confirmation_renderer import (
                prefix_with_revision_acknowledgement,
            )

            revision_summary = None
            merged = result.get("_merged_luma_response")
            if isinstance(merged, dict):
                revision_summary = merged.get("_revision_summary")
            rendered_text = prefix_with_revision_acknowledgement(
                rendered_text, revision_summary
            )
            result["text"] = rendered_text
    except Exception as e:
        logger.debug(
            "Failed to render availability text: %s. Rendering is best-effort.", e
        )


def _inject_outcome_text(
    result: Dict[str, Any],
    decision: Optional[Dict[str, Any]],
    outcome: Dict[str, Any],
) -> None:
    """Inject booking execution outcome text into result (best-effort)."""
    outcome_status = outcome.get("status")
    if outcome_status not in ("succeeded", "failed", "partial"):
        return
    try:
        intent_name = outcome.get("intent_name") or "your request"
        if outcome_status == "succeeded":
            render_instruction = (
                f"Tell the user their {intent_name.lower().replace('_', ' ')} was successful. "
                "Include the booked service, appointment date and time, and booking reference "
                "when those values are present in the execution evidence. "
                "Do not invent missing details. Be warm and brief."
            )
        elif outcome_status == "failed":
            render_instruction = (
                f"Tell the user their {intent_name.lower().replace('_', ' ')} could not be completed. "
                "Be empathetic and suggest they try again."
            )
        else:
            render_instruction = (
                f"Tell the user their {intent_name.lower().replace('_', ' ')} was only "
                "partially completed. Explain only the details present in the evidence "
                "and be brief."
            )

        rendered_text = render_llm(
            LlmRenderRequest(
                render_instruction=render_instruction,
                facts={
                    "structured_context": _structured_context_from_decision(
                        decision or {}
                    ),
                    "execution": outcome,
                },
            )
        )
        if rendered_text:
            result["text"] = rendered_text
    except Exception as e:
        logger.debug(
            "Failed to render outcome text: %s. Rendering is best-effort and will be omitted.", e
        )


def _inject_execution_text(
    result: Dict[str, Any],
    decision: Optional[Dict[str, Any]],
    execution_result: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
) -> None:
    """Render a canonical execution result without interpreting action schemas."""
    if not isinstance(execution_result, dict):
        return
    if execution_result.get("schema_version") != 1:
        logger.warning("Ignoring unsupported execution result schema")
        return
    if (
        execution_result.get("status") == "succeeded"
        and isinstance(execution_result.get("availability"), dict)
    ):
        _inject_availability_text(
            result, decision, execution_result, session_state=session_state
        )
        return
    _inject_outcome_text(result, decision, execution_result)


def _inject_system_text(result: Dict[str, Any], decision: Dict[str, Any]) -> None:
    """Inject greeting/system text for GREETING or WELCOME intents (best-effort)."""
    try:
        intent_name = decision.get("intent_name", "")
        if not intent_name or intent_name.upper() not in ("GREETING", "WELCOME"):
            return
        render_instruction = (
            "Greet the user warmly and let them know you can help with bookings "
            "and related inquiries. Keep it brief and friendly."
        )
        rendered_text = render_llm(
            LlmRenderRequest(
                render_instruction=render_instruction,
                facts={"structured_context": _structured_context_from_decision(decision)},
            )
        )
        if rendered_text:
            result["text"] = rendered_text
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Phase 1 boundary class
# ---------------------------------------------------------------------------

class ResponseRenderer:
    """Turn-level response rendering boundary."""

    def render_clarification(
        self,
        result: Dict[str, Any],
        decision: Dict[str, Any],
        session_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Inject clarification/missing-slot text (best-effort)."""
        _inject_rendering_text(result, decision, session_state)

    def render_system(
        self,
        result: Dict[str, Any],
        decision: Dict[str, Any],
    ) -> None:
        """Inject greeting/system intent text (best-effort)."""
        _inject_system_text(result, decision)

    def render_execution(
        self,
        result: Dict[str, Any],
        decision: Optional[Dict[str, Any]],
        execution_result: Dict[str, Any],
        session_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Inject text from the canonical execution result (best-effort)."""
        _inject_execution_text(result, decision, execution_result, session_state)

    def render_recovery(
        self,
        result: Dict[str, Any],
        *,
        plan: Optional[Dict[str, Any]] = None,
        session_state: Optional[Dict[str, Any]] = None,
        user_input: Optional[str] = None,
        availability_client_present: bool = True,
        decision: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Inject LLM recovery text when planning left no reply (best-effort)."""
        from core.rendering.recovery_renderer import inject_recovery_text

        inject_recovery_text(
            result,
            plan=plan,
            session_state=session_state,
            user_input=user_input,
            availability_client_present=availability_client_present,
            decision=decision,
        )

    def render_off_topic(
        self,
        result: Dict[str, Any],
        *,
        outcome: Optional[Dict[str, Any]] = None,
        session_state: Optional[Dict[str, Any]] = None,
        user_input: Optional[str] = None,
    ) -> None:
        """Inject OFF_TOPIC digression text from Stage-2 evidence on the outcome (best-effort).

        Core conversational path — not an extension handler.
        """
        from core.rendering.off_topic_renderer import inject_off_topic_text

        inject_off_topic_text(
            result,
            outcome=outcome,
            session_state=session_state,
            user_input=user_input,
        )
