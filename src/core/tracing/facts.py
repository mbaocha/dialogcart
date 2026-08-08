"""Business facts decision trace emitters (observational only)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Mapping, Optional

from core.planning.facts.business_fact_registry import BusinessFacts, PlanningFactContext
from core.tracing.decision_trace import Candidate, TurnTrace, decide, emit_evidence
from core.tracing.reason_codes import (
    AVAILABILITY_CHECK_REQUIRED,
    AVAILABILITY_READY,
    CLARIFICATION_REQUIRED,
    CONFIRMATION_REQUIRED,
    INPUT_IGNORED_NOT_APPLICABLE,
    TIME_SELECTION_READY,
)

FACTS_EVIDENCE_INPUTS_ID = "evidence.facts.inputs"
FACTS_DERIVE_ALL_ID = "decision.facts.derive_all"
FACTS_AVAILABILITY_READY_ID = "decision.facts.availability_ready"
FACTS_AVAILABILITY_CHECK_REQUIRED_ID = "decision.facts.availability_check_required"
FACTS_TIME_SELECTION_READY_ID = "decision.facts.time_selection_ready"
FACTS_USER_CONFIRMATION_REQUIRED_ID = "decision.facts.user_confirmation_required"

FACTS_NODE_IDS = (
    FACTS_DERIVE_ALL_ID,
    FACTS_AVAILABILITY_READY_ID,
    FACTS_AVAILABILITY_CHECK_REQUIRED_ID,
    FACTS_TIME_SELECTION_READY_ID,
    FACTS_USER_CONFIRMATION_REQUIRED_ID,
)

INFERENCE_CATEGORY = "inference"


def facts_dependencies() -> List[str]:
    trace = TurnTrace.current()
    if trace is None:
        return []
    return [node_id for node_id in FACTS_NODE_IDS if trace.has_record(node_id)]


def _slot_keys(slots: Mapping[str, Any]) -> List[str]:
    return sorted(key for key, value in slots.items() if value is not None)


def emit_business_facts_trace(
    context: PlanningFactContext,
    facts: BusinessFacts,
) -> None:
    """Emit business-fact derivation graph for one planning cycle."""
    trace = TurnTrace.current()
    if trace is None:
        return
    if trace.has_record(FACTS_DERIVE_ALL_ID):
        return

    slots = context.slots if isinstance(context.slots, dict) else {}
    session_state = context.session_state if isinstance(context.session_state, dict) else {}
    from core.workflows.availability.presentation import (
        availability_fingerprint_from_session,
    )

    stored_fingerprint = availability_fingerprint_from_session(session_state)

    inputs_id = emit_evidence(
        "FACTS_INPUTS",
        subsystem="planning",
        facts={
            "intent_name": (context.intent_name or "").upper(),
            "slot_keys": _slot_keys(slots),
            "missing_slots": list(context.missing_slots or []),
            "needs_clarification": context.needs_clarification,
            "confirmation_state": context.confirmation_state,
            "organization_id": context.organization_id,
            "stored_fingerprint_present": stored_fingerprint is not None,
        },
        node_id=FACTS_EVIDENCE_INPUTS_ID,
        source="business_fact_registry",
        observed_at_stage="business_facts",
    )

    deps: List[str] = []
    if inputs_id:
        deps.append(inputs_id)

    from core.tracing.fingerprint import FINGERPRINT_TRUST_ID, fingerprint_dependencies

    deps.extend(fingerprint_dependencies())

    facts_dict = asdict(facts)
    derive_id = decide(
        "DERIVE_BUSINESS_FACTS",
        subsystem="planning",
        winner=facts_dict,
        reason_code="FACTS_DERIVED",
        reason_text="Derived planner business facts for policy execution flags",
        node_id=FACTS_DERIVE_ALL_ID,
        depends_on=deps,
        category=INFERENCE_CATEGORY,
        inputs_evaluated={
            "intent_name": (context.intent_name or "").upper(),
            "missing_slots": list(context.missing_slots or []),
            "needs_clarification": context.needs_clarification,
            "confirmation_state": context.confirmation_state,
        },
    )

    child_deps = [dep for dep in (derive_id, *deps) if dep]

    decide(
        "AVAILABILITY_READY",
        subsystem="planning",
        winner=facts.availability_ready,
        reason_code=AVAILABILITY_READY if facts.availability_ready else "AVAILABILITY_NOT_READY",
        reason_text=(
            "Availability outcome trusted for current search criteria"
            if facts.availability_ready
            else "Availability not yet resolved for current search criteria"
        ),
        node_id=FACTS_AVAILABILITY_READY_ID,
        depends_on=child_deps,
        category=INFERENCE_CATEGORY,
        candidates=[
            Candidate(
                id="availability_ready",
                matched=facts.availability_ready,
                reason_code=AVAILABILITY_READY if facts.availability_ready else "AVAILABILITY_NOT_READY",
                reason_text="Fingerprint match, bound datetime, or continuation bypass",
            )
        ],
    )

    decide(
        "AVAILABILITY_CHECK_REQUIRED",
        subsystem="planning",
        winner=facts.availability_check_required,
        reason_code=(
            AVAILABILITY_CHECK_REQUIRED
            if facts.availability_check_required
            else INPUT_IGNORED_NOT_APPLICABLE
        ),
        reason_text=(
            "SEARCH_AVAILABILITY prerequisites met and check still required"
            if facts.availability_check_required
            else "Availability check not required for current state"
        ),
        node_id=FACTS_AVAILABILITY_CHECK_REQUIRED_ID,
        depends_on=child_deps,
        category=INFERENCE_CATEGORY,
    )

    decide(
        "TIME_SELECTION_READY",
        subsystem="planning",
        winner=facts.time_selection_ready,
        reason_code=(
            TIME_SELECTION_READY
            if facts.time_selection_ready
            else "TIME_SELECTION_NOT_READY"
        ),
        reason_text=(
            "Booking datetime is bound"
            if facts.time_selection_ready
            else "Booking datetime not yet bound"
        ),
        node_id=FACTS_TIME_SELECTION_READY_ID,
        depends_on=child_deps,
        category=INFERENCE_CATEGORY,
    )

    decide(
        "USER_CONFIRMATION_REQUIRED",
        subsystem="planning",
        winner=facts.user_confirmation_required,
        reason_code=(
            CONFIRMATION_REQUIRED
            if facts.user_confirmation_required
            else INPUT_IGNORED_NOT_APPLICABLE
        ),
        reason_text=(
            "Explicit user confirmation required before commit"
            if facts.user_confirmation_required
            else "User confirmation not required"
        ),
        node_id=FACTS_USER_CONFIRMATION_REQUIRED_ID,
        depends_on=child_deps,
        category=INFERENCE_CATEGORY,
        candidates=[
            Candidate(
                id="clarification_blocks",
                matched=bool(context.needs_clarification or context.missing_slots),
                reason_code=CLARIFICATION_REQUIRED,
                reason_text="Clarification or missing slots block confirmation requirement",
            ),
            Candidate(
                id="confirmation_pending",
                matched=context.confirmation_state == "pending",
                reason_code=CONFIRMATION_REQUIRED,
                reason_text="Confirmation gate already pending",
            ),
        ],
    )
