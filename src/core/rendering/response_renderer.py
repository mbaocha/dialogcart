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


def _inject_rendering_text_impl(
    result: Dict[str, Any],
    decision: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        from core.planning.time_resolution import (
            TIME_MATCH_MISMATCH,
            build_execution_result_for_time_resolution_render,
        )
        from core.rendering.availability_renderer import build_availability_render_request

        time_match_outcome = (
            decision.get("time_match_outcome")
            or decision.get("plan", {}).get("time_match_outcome")
            or (decision.get("facts") or {}).get("time_match_outcome")
        )
        if time_match_outcome == TIME_MATCH_MISMATCH:
            from core.workflows.availability.presentation import (
                ensure_presented_availability,
            )

            exec_payload = build_execution_result_for_time_resolution_render(
                session_state,
                time_resolution=(
                    decision.get("time_resolution")
                    or (decision.get("facts") or {}).get("time_resolution")
                ),
            )
            if exec_payload:
                conversation_history = (session_state or {}).get("messages", [])
                avail = exec_payload.get("availability") or {}
                presented = ensure_presented_availability(
                    session_state=session_state,
                    raw_slots=avail.get("slots"),
                ) or {
                    "search_date": None,
                    "slots": [],
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
                )
                if render_request:
                    rendered_text = render_llm(render_request)
                    if rendered_text:
                        result["text"] = rendered_text
                        if isinstance(result.get("outcome"), dict):
                            result["outcome"]["text"] = rendered_text
                    return

        decision["_session"] = session_state or {}
        if session_state and isinstance(session_state, dict):
            slot_attempts = session_state.get("slot_attempts")
            if isinstance(slot_attempts, dict):
                decision["slot_attempts"] = slot_attempts
                facts = decision.get("facts", {})
                if isinstance(facts, dict):
                    facts["slot_attempts"] = slot_attempts

        facts = decision.get("facts", {})
        facts_missing = facts.get("missing_slots") if isinstance(facts, dict) else None
        if not isinstance(facts_missing, list):
            return
        missing_slots = facts_missing
        if not missing_slots:
            return
        intent_name = (
            decision.get("intent_name")
            or decision.get("plan", {}).get("intent_name")
            or "your request"
        )
        slot_attempts = decision.get("slot_attempts") or {}
        if not isinstance(slot_attempts, dict):
            slot_attempts = {}
        first_missing = missing_slots[0] if missing_slots else None
        attempt_count = slot_attempts.get(first_missing, 0) if first_missing else 0
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
        if "service_id" in missing_slots:
            render_missing = ["service_id"]
            if service_candidates:
                candidates_str = ", ".join(f'"{c}"' for c in service_candidates)
                service_hint = f" Present these options for them to choose from: {candidates_str}."
            else:
                service_hint = ""
        else:
            render_missing = missing_slots
            service_hint = ""
        render_instruction = (
            f"The user wants to {intent_name.lower().replace('_', ' ')}. "
            f"Ask ONLY for these specific missing fields (nothing else): {', '.join(render_missing)}."
            f"{service_hint}{ack_note}{retry_note} "
            "Do not ask for any other information. Be natural and brief."
        )
        conversation_history = (session_state or {}).get("messages", [])
        rendered_text = render_llm(
            LlmRenderRequest(
                render_instruction=render_instruction,
                facts={"structured_context": _structured_context_from_decision(decision)},
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
