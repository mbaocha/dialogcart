"""Per-stage invariant checks for the DialogCart turn trace pipeline."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from core.tracing.invariant_trace import InvariantResult

_PRESENTATION_FIELDS = frozenset({"text", "ui_actions", "ui_hint"})


def _slot_keys(slots: Any) -> List[str]:
    if not isinstance(slots, dict):
        return []
    return sorted(k for k, v in slots.items() if v is not None)


def check_session_load(
    *,
    session_state: Optional[Dict[str, Any]],
    user_id: str = "",
) -> Sequence[InvariantResult]:
    results: List[InvariantResult] = []
    if session_state and isinstance(session_state, dict):
        active_capability = session_state.get("active_capability")
        if active_capability == "payment":
            facts = session_state.get("facts")
            ok = isinstance(facts, dict) and "facts" in session_state
            results.append(
                InvariantResult(
                    invariant_id="session.payment_facts_present",
                    invariant_ok=ok,
                    message=(
                        ""
                        if ok
                        else f"payment capability requires persisted facts (user_id={user_id})"
                    ),
                )
            )

    results.append(
        InvariantResult(
            invariant_id="session.load_complete",
            invariant_ok=True,
            message="session loaded or absent (first turn)",
        )
    )
    return results


def check_merge(
    *,
    effective_response: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
    prior_intent: Optional[str] = None,
) -> Sequence[InvariantResult]:
    results: List[InvariantResult] = []
    missing_slots = effective_response.get("missing_slots")
    results.append(
        InvariantResult(
            invariant_id="merge.missing_slots_is_list",
            invariant_ok=isinstance(missing_slots, list),
            message=(
                ""
                if isinstance(missing_slots, list)
                else f"missing_slots must be a list, got {type(missing_slots)}"
            ),
        )
    )

    effective_intent = effective_response.get("intent", {})
    if isinstance(effective_intent, dict):
        intent_name = effective_intent.get("name", "")
    else:
        intent_name = str(effective_intent or "")

    if (
        session_state
        and intent_name in ("", "UNKNOWN")
        and prior_intent
        and prior_intent not in ("", "UNKNOWN")
    ):
        try:
            from core.policy.intent_policy import get_intent_durable

            durable = get_intent_durable(prior_intent)
        except Exception:
            durable = True
        if durable:
            results.append(
                InvariantResult(
                    invariant_id="merge.durable_intent_not_overwritten",
                    invariant_ok=False,
                    message=(
                        f"durable session intent {prior_intent!r} would be lost "
                        f"(effective intent={intent_name!r})"
                    ),
                )
            )

    raw_luma_slots = effective_response.get("_raw_luma_slots", {})
    merged_slots = effective_response.get("_effective_collected_slots") or effective_response.get(
        "slots", {}
    )
    if isinstance(raw_luma_slots, dict) and raw_luma_slots:
        raw_keys = {
            k for k, v in raw_luma_slots.items() if v is not None
        }
        merged_keys = set(merged_slots.keys()) if isinstance(merged_slots, dict) else set()
        missing_keys = raw_keys - merged_keys
        if missing_keys and intent_name == "CREATE_APPOINTMENT":
            from core.planning.temporal_proposal import (
                _CREATE_APPOINTMENT_TEMPORAL_SLOT_KEYS,
                resolve_session_proposals,
            )

            proposals = resolve_session_proposals(
                merged_luma_response=effective_response,
                previous_session_state=session_state,
            )
            proposal_covered: set = set()
            date_proposal = proposals.get("date_proposal")
            time_proposal = proposals.get("time_proposal")
            if isinstance(date_proposal, dict) and date_proposal.get("start"):
                proposal_covered |= _CREATE_APPOINTMENT_TEMPORAL_SLOT_KEYS - {"time"}
            if isinstance(time_proposal, dict) and (
                time_proposal.get("value")
                or (
                    time_proposal.get("mode") == "fuzzy"
                    and time_proposal.get("start")
                )
            ):
                proposal_covered.add("time")
            missing_keys -= proposal_covered

        results.append(
            InvariantResult(
                invariant_id="merge.luma_slots_not_dropped",
                invariant_ok=not missing_keys,
                message=(
                    ""
                    if not missing_keys
                    else f"Luma slots dropped before required-slot computation: {sorted(missing_keys)}"
                ),
            )
        )

    return results


def check_planner(*, plan: Dict[str, Any]) -> Sequence[InvariantResult]:
    results: List[InvariantResult] = []
    for field in _PRESENTATION_FIELDS:
        if field in plan:
            results.append(
                InvariantResult(
                    invariant_id="planner.no_presentation_fields",
                    invariant_ok=False,
                    message=f"plan must not contain presentation field {field!r}",
                )
            )
            break

    status = plan.get("status")
    active_capability = plan.get("active_capability")
    if status == "AWAITING_CAPABILITY":
        results.append(
            InvariantResult(
                invariant_id="planner.awaiting_capability_has_active_capability",
                invariant_ok=bool(active_capability),
                message=(
                    ""
                    if active_capability
                    else "AWAITING_CAPABILITY requires active_capability"
                ),
            )
        )

    missing_slots = plan.get("missing_slots")
    if missing_slots is not None:
        results.append(
            InvariantResult(
                invariant_id="planner.missing_slots_is_list",
                invariant_ok=isinstance(missing_slots, list),
                message=(
                    ""
                    if isinstance(missing_slots, list)
                    else f"missing_slots must be a list, got {type(missing_slots)}"
                ),
            )
        )

    if not results:
        results.append(
            InvariantResult(
                invariant_id="planner.structure_valid",
                invariant_ok=True,
            )
        )
    return results


def check_business_facts(*, facts: Any, intent_name: str = "") -> Sequence[InvariantResult]:
    results: List[InvariantResult] = []
    if facts is None:
        results.append(
            InvariantResult(
                invariant_id="business_facts.derived",
                invariant_ok=False,
                message="business facts were not derived",
            )
        )
        return results

    availability_ready = getattr(facts, "availability_ready", None)
    availability_check_required = getattr(facts, "availability_check_required", None)
    if availability_ready is True and availability_check_required is True:
        results.append(
            InvariantResult(
                invariant_id="business_facts.availability_ready_implies_not_check_required",
                invariant_ok=False,
                message=(
                    "availability_ready and availability_check_required "
                    "cannot both be true"
                ),
            )
        )

    user_confirmation_required = getattr(facts, "user_confirmation_required", None)
    user_confirmation_satisfied = getattr(
        facts, "user_confirmation_satisfied", None
    )
    if user_confirmation_satisfied and not user_confirmation_required:
        results.append(
            InvariantResult(
                invariant_id="business_facts.confirmation_consistency",
                invariant_ok=False,
                message=(
                    "user_confirmation_satisfied without user_confirmation_required "
                    f"for intent {intent_name!r}"
                ),
            )
        )

    if not results:
        results.append(
            InvariantResult(
                invariant_id="business_facts.derived",
                invariant_ok=True,
            )
        )
    return results


def check_fingerprint(
    *,
    stored_fingerprint: Optional[str],
    current_fingerprint: Optional[str],
    availability_ready: bool = False,
) -> Sequence[InvariantResult]:
    if stored_fingerprint is None and current_fingerprint is None:
        return [
            InvariantResult(
                invariant_id="fingerprint.not_required",
                invariant_ok=True,
                message="no fingerprint context this turn",
            )
        ]

    if availability_ready and stored_fingerprint and current_fingerprint:
        ok = stored_fingerprint == current_fingerprint
        return [
            InvariantResult(
                invariant_id="fingerprint.stored_matches_current_when_ready",
                invariant_ok=ok,
                message=(
                    ""
                    if ok
                    else (
                        f"availability_ready but fingerprint mismatch: "
                        f"stored={stored_fingerprint!r} current={current_fingerprint!r}"
                    )
                ),
            )
        ]

    return [
        InvariantResult(
            invariant_id="fingerprint.computed",
            invariant_ok=True,
            message=(
                f"stored={stored_fingerprint!r} current={current_fingerprint!r} "
                f"availability_ready={availability_ready}"
            ),
        )
    ]


def check_tool_execution(
    *,
    plan_action: Optional[str],
    execution_result: Optional[Dict[str, Any]],
    can_execute: bool,
) -> Sequence[InvariantResult]:
    if not plan_action:
        return [
            InvariantResult(
                invariant_id="tool_execution.not_applicable",
                invariant_ok=True,
                message="no execution action this turn",
            )
        ]

    if not can_execute:
        return [
            InvariantResult(
                invariant_id="tool_execution.policy_allowed",
                invariant_ok=False,
                message=f"execution blocked for action {plan_action!r}",
            )
        ]

    if execution_result is None:
        return [
            InvariantResult(
                invariant_id="tool_execution.result_present",
                invariant_ok=False,
                message=f"execution ran for {plan_action!r} but no result returned",
            )
        ]

    return [
        InvariantResult(
            invariant_id="tool_execution.completed",
            invariant_ok=True,
            message=f"action={plan_action!r} status={execution_result.get('status')}",
        )
    ]


def check_pagination(
    *,
    handled: bool,
    fingerprint_before: Optional[str],
    fingerprint_after: Optional[str],
    search_executed: bool = False,
) -> Sequence[InvariantResult]:
    if not handled:
        return [
            InvariantResult(
                invariant_id="pagination.not_applicable",
                invariant_ok=True,
                message="browse pagination not triggered",
            )
        ]

    results: List[InvariantResult] = [
        InvariantResult(
            invariant_id="pagination.no_search_on_browse",
            invariant_ok=not search_executed,
            message=(
                ""
                if not search_executed
                else "SEARCH_AVAILABILITY must not run during browse pagination"
            ),
        )
    ]

    if fingerprint_before is not None and fingerprint_after is not None:
        ok = fingerprint_before == fingerprint_after
        results.append(
            InvariantResult(
                invariant_id="pagination.fingerprint_unchanged",
                invariant_ok=ok,
                message=(
                    ""
                    if ok
                    else (
                        f"fingerprint changed during pagination: "
                        f"{fingerprint_before!r} -> {fingerprint_after!r}"
                    )
                ),
            )
        )

    return results


def check_persistence(
    *,
    session_state: Dict[str, Any],
    outcome: Dict[str, Any],
    previous_session: Optional[Dict[str, Any]] = None,
) -> Sequence[InvariantResult]:
    results: List[InvariantResult] = []
    missing_slots = session_state.get("missing_slots", [])
    slots = session_state.get("slots", {})
    if not isinstance(missing_slots, list):
        missing_slots = []
    if not isinstance(slots, dict):
        slots = {}

    from core.planning.planner.missing_slots import (
        get_planning_required_slots_for_intent as get_required_slots_for_intent,
    )

    intent_name = (
        session_state.get("intent_name")
        or session_state.get("intent")
        or outcome.get("intent_name")
        or ""
    )
    if isinstance(intent_name, dict):
        intent_name = intent_name.get("name", "")

    effective_collected = {
        slot_name: slot_value
        for slot_name, slot_value in slots.items()
        if slot_value is not None
    }
    if intent_name:
        required = set(get_required_slots_for_intent(intent_name))
        effective_collected = {
            k: v for k, v in effective_collected.items() if k in required
        }
    if "service_id" in slots and slots["service_id"] is not None:
        effective_collected["service_id"] = slots["service_id"]

    overlapping = [
        slot for slot in missing_slots if slot in effective_collected
    ]
    results.append(
        InvariantResult(
            invariant_id="persistence.missing_slots_disjoint_from_collected",
            invariant_ok=not overlapping,
            message=(
                ""
                if not overlapping
                else (
                    "slots present in both missing_slots and effective_collected_slots: "
                    f"{overlapping}"
                )
            ),
        )
    )

    facts_missing = None
    facts = outcome.get("facts")
    if isinstance(facts, dict):
        facts_missing = facts.get("missing_slots")
    if isinstance(facts_missing, list) and missing_slots != facts_missing:
        results.append(
            InvariantResult(
                invariant_id="persistence.missing_slots_match_facts",
                invariant_ok=False,
                message=(
                    f"persisted missing_slots={missing_slots} "
                    f"!= outcome facts missing_slots={facts_missing}"
                ),
            )
        )

    if previous_session:
        previous_intent = previous_session.get("intent_name") or previous_session.get(
            "intent"
        )
        if isinstance(previous_intent, dict):
            previous_intent = previous_intent.get("name", "")
        final_intent = intent_name
        if previous_intent and previous_intent not in ("", "UNKNOWN"):
            try:
                from core.session.durable_intents import (
                    is_durable_intent,
                )

                if is_durable_intent(previous_intent):
                    ok = bool(final_intent) and (
                        final_intent == previous_intent or final_intent != ""
                    )
                    results.append(
                        InvariantResult(
                            invariant_id="persistence.durable_intent_preserved",
                            invariant_ok=ok,
                            message=(
                                ""
                                if ok
                                else (
                                    f"durable intent {previous_intent!r} lost during "
                                    f"persistence (final={final_intent!r})"
                                )
                            ),
                        )
                    )
            except Exception:
                pass

    return results


def check_save_session(*, saved: bool, user_id: str = "") -> Sequence[InvariantResult]:
    return [
        InvariantResult(
            invariant_id="save_session.persisted",
            invariant_ok=saved,
            message="" if saved else f"session not saved for user_id={user_id}",
        )
    ]


def check_reload_session(
    *,
    saved_state: Optional[Dict[str, Any]],
    reloaded_state: Optional[Dict[str, Any]],
    user_id: str = "",
) -> Sequence[InvariantResult]:
    if saved_state is None:
        return [
            InvariantResult(
                invariant_id="reload_session.not_applicable",
                invariant_ok=True,
                message="no session saved this turn",
            )
        ]

    if reloaded_state is None:
        return [
            InvariantResult(
                invariant_id="reload_session.round_trip",
                invariant_ok=False,
                message=f"saved session not readable for user_id={user_id}",
            )
        ]

    mismatches: List[str] = []
    for key in ("intent_name", "intent", "status"):
        saved_val = saved_state.get(key)
        reloaded_val = reloaded_state.get(key)
        if saved_val is not None and saved_val != reloaded_val:
            mismatches.append(f"{key}: saved={saved_val!r} reloaded={reloaded_val!r}")

    saved_slots = _slot_keys(saved_state.get("slots"))
    reloaded_slots = _slot_keys(reloaded_state.get("slots"))
    if saved_slots != reloaded_slots:
        mismatches.append(f"slots keys: saved={saved_slots} reloaded={reloaded_slots}")

    return [
        InvariantResult(
            invariant_id="reload_session.round_trip",
            invariant_ok=not mismatches,
            message="; ".join(mismatches) if mismatches else "reload matches save",
        )
    ]
