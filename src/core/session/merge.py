"""
Session merge: combine NLU turn deltas with persisted session state for planning.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.session.durable_intents import (
    filter_slots_for_intent,
    is_durable_intent,
)
from core.session.schema import (
    debug_log,
    debug_persistence_enabled,
    filter_serializable_facts,
    normalize_session_guards,
)
from core.session.effective_slots import _compute_effective_collected_slots_internal

logger = logging.getLogger(__name__)
turn_logger = logging.getLogger("core.turn_log")
turn_logger.setLevel(logging.INFO)


@dataclass
class _MergeContext:
    """Internal context bundle for merge_luma_with_session helpers.

    Created once per call to merge_luma_with_session after initial variable
    setup.  All helpers accept this instead of repeating the common parameter
    group (merged, session_state, apply_domain_filter, …).

    ``merged`` is the mutable accumulator dict; callers always hold a reference
    to the same object, so mutations through ctx.merged are visible everywhere.
    """

    merged: Dict[str, Any]
    session_state: Dict[str, Any]
    apply_domain_filter: bool
    session_intent: Any                 # str | dict | None
    session_intent_name: Optional[str]  # normalised string form
    session_status: str
    luma_intent_name: str
    initial_session_slots: Dict[str, Any]
    user_id: str
    turn_operation: Optional[str] = None
    """Attach-phase turn_operation from AttachedRequest (not a payload field)."""


def should_merge_session_context(
    session_state: Optional[Dict[str, Any]],
    *,
    session_reset_occurred: bool = False,
) -> bool:
    """Whether to merge session context into this turn.

    Invariant: status does not decide merge eligibility. Merge when a durable
    booking flow is active (or pre-intent slots await materialization) and the
    turn is not a destructive intent reset.
    """
    if not session_state or session_reset_occurred:
        return False

    intent_name = session_state.get(
        "intent_name") or session_state.get("intent")
    if isinstance(intent_name, dict):
        intent_name = intent_name.get("name")
    intent_name = intent_name or ""

    if intent_name and intent_name != "UNKNOWN":
        try:
            if is_durable_intent(intent_name):
                return True
        except Exception:
            return False
        return False

    # Pre-intent materialization: slots collected before durable intent exists.
    slots = session_state.get("slots")
    return isinstance(slots, dict) and bool(slots)


def _finalize_merged_luma_response(
    merged: Dict[str, Any],
    luma_response: Dict[str, Any],
) -> Dict[str, Any]:
    """Attach transient per-turn signals before returning from merge."""
    from core.workflows.availability.browse import apply_availability_browse_signal

    apply_availability_browse_signal(merged, luma_response)
    return merged


def _extract_date_from_luma_response(luma_resp: Dict[str, Any]) -> Optional[str]:
    """
    Extract date from Luma response, checking all possible locations.

    Returns the first date found, or None if not found.
    """
    # Priority 1: Direct slots.date
    if "slots" in luma_resp and isinstance(luma_resp["slots"], dict):
        if "date" in luma_resp["slots"]:
            date_val = luma_resp["slots"]["date"]
            if date_val:
                return str(date_val) if not isinstance(date_val, str) else date_val

    # Priority 1.5: Check issues field (sometimes Luma provides date in issues for UNKNOWN intents)
    if "issues" in luma_resp and isinstance(luma_resp["issues"], dict):
        # Check if issues contains date information
        for key, value in luma_resp["issues"].items():
            if "date" in key.lower() and value:
                if (
                    isinstance(value, str)
                    and len(value) >= 10
                    and value[4] == "-"
                    and value[7] == "-"
                ):
                    return value.split("T")[0].split(" ")[0]
                elif isinstance(value, dict):
                    # Check nested date fields
                    for date_field in [
                        "date",
                        "value",
                        "resolved",
                        "start",
                        "start_date",
                    ]:
                        if date_field in value:
                            date_val = value[date_field]
                            if date_val:
                                date_str = str(date_val)
                                if "T" in date_str:
                                    return date_str.split("T")[0]
                                if " " in date_str:
                                    return date_str.split(" ")[0]
                                if (
                                    len(date_str) >= 10
                                    and date_str[4] == "-"
                                    and date_str[7] == "-"
                                ):
                                    return date_str

    # Priority 2: Check all semantic locations for date_refs
    semantic_paths = [
        ("semantic", "date_refs"),
        ("semantic", "resolved_booking", "date_refs"),
        ("stages", "semantic", "resolved_booking", "date_refs"),
        ("stages", "semantic", "date_refs"),
        ("trace", "semantic", "date_refs"),
        ("trace", "semantic", "resolved_booking", "date_refs"),
        ("trace", "stages", "semantic", "resolved_booking", "date_refs"),
    ]

    for path in semantic_paths:
        current = luma_resp
        try:
            for key in path:
                if isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    current = None
                    break

            if current and isinstance(current, list) and len(current) > 0:
                # Get the last date_ref (most recent/resolved)
                date_candidate = current[-1]
                if isinstance(date_candidate, str):
                    # If it's a string, check if it's ISO date format
                    if (
                        len(date_candidate) >= 10
                        and date_candidate[4] == "-"
                        and date_candidate[7] == "-"
                    ):
                        # Extract date part
                        return date_candidate.split("T")[0].split(" ")[0]
                    return date_candidate
                elif isinstance(date_candidate, dict):
                    # If it's an object, check common date fields
                    for date_field in [
                        "resolved",
                        "date",
                        "value",
                        "start",
                        "start_date",
                    ]:
                        if date_field in date_candidate:
                            date_val = date_candidate[date_field]
                            if date_val:
                                date_str = str(date_val)
                                # Extract date part if it's datetime
                                if "T" in date_str:
                                    return date_str.split("T")[0]
                                if " " in date_str:
                                    return date_str.split(" ")[0]
                                return date_str
        except (KeyError, TypeError, AttributeError):
            continue

    # Priority 3: Check entities.date
    if "entities" in luma_resp and isinstance(luma_resp["entities"], dict):
        if "date" in luma_resp["entities"]:
            date_val = luma_resp["entities"]["date"]
            if date_val:
                return str(date_val) if not isinstance(date_val, str) else date_val

    # Priority 4: Check booking.datetime_range.start
    if "booking" in luma_resp and isinstance(luma_resp["booking"], dict):
        booking = luma_resp["booking"]
        if "datetime_range" in booking and isinstance(
            booking["datetime_range"], dict
        ):
            start = booking["datetime_range"].get("start")
            if start:
                date_str = str(start)
                # Extract date part
                if "T" in date_str:
                    return date_str.split("T")[0]
                if " " in date_str:
                    return date_str.split(" ")[0]
                return date_str

    return None


def _merge_facts(
    merged: Dict[str, Any],
    session_state: Dict[str, Any],
) -> None:
    """Merge session facts with Luma facts (Luma overrides session on conflict)."""
    # Facts are a first-class, durable part of session state (same status as slots)
    # This ensures capability facts (e.g., payment_satisfied) persist across turns
    session_facts = session_state.get("facts", {}) if session_state else {}
    if not isinstance(session_facts, dict):
        session_facts = {}

    luma_facts = merged.get("facts", {})
    if not isinstance(luma_facts, dict):
        luma_facts = {}

    # Merge: new facts from Luma override old facts from session
    # This allows capabilities to update facts (e.g., payment_satisfied: True)
    merged["facts"] = {**session_facts, **luma_facts}


def _carry_forward_temporal(
    merged: Dict[str, Any],
    session_state: Dict[str, Any],
) -> None:
    """Fieldwise merge current-turn Temporal with session Temporal."""
    from core.planning.temporal_contract import get_temporal, merge_temporals

    if not session_state:
        return
    current = get_temporal(merged)
    session_t = get_temporal(session_state)
    merged["temporal"] = merge_temporals(session_t, current)

    merged.pop("time_constraint", None)
    merged.pop("date_constraint", None)
    logger.debug(
        "merge_luma_with_session: carried temporal mode=%s start_date=%s start_time=%s",
        merged["temporal"].get("mode"),
        merged["temporal"].get("start_date"),
        merged["temporal"].get("start_time"),
    )


def _detect_modification_context(
    merged: Dict[str, Any],
    raw_luma_slots: Dict[str, Any],
    merged_intent_name: str,
    session_state: Dict[str, Any],
) -> None:
    """Detect and persist modification context for MODIFY_* intents.

    Writes merged["_modification_context"] when the current intent is MODIFY_BOOKING
    or MODIFY_RESERVATION, falling back to session-persisted context if neither matches.
    """
    modification_context = None
    if merged_intent_name == "MODIFY_BOOKING":
        # Detect modification type from raw_luma_slots (before promotion)
        # This is intent-driven: we detect MODIFY_BOOKING intent, then check for signals
        has_time = "time" in raw_luma_slots and raw_luma_slots.get(
            "time") is not None
        has_date = "date" in raw_luma_slots and raw_luma_slots.get(
            "date") is not None

        # Always set modification context for MODIFY_BOOKING (intent-driven)
        # If no slots detected, set default context that will be refined later
        modification_context = {
            "modifying_time": has_time, "modifying_date": has_date}
        # Persist modification context to merged response (will be persisted to session)
        merged["_modification_context"] = modification_context

    elif merged_intent_name == "MODIFY_RESERVATION":
        # Detect modification type from raw_luma_slots (before promotion)
        # This is intent-driven: we detect MODIFY_RESERVATION intent, then check for signals
        has_start_date = (
            "start_date" in raw_luma_slots
            and raw_luma_slots.get("start_date") is not None
        )
        has_end_date = (
            "end_date" in raw_luma_slots and raw_luma_slots.get(
                "end_date") is not None
        )
        has_date = "date" in raw_luma_slots and raw_luma_slots.get(
            "date") is not None

        # Always set modification context for MODIFY_RESERVATION (intent-driven)
        # If no slots detected, set default context that will be refined later
        modification_context = {
            "modifying_start_date": has_start_date,
            "modifying_end_date": has_end_date,
            "modifying_date": has_date,
        }
        # Persist modification context to merged response (will be persisted to session)
        merged["_modification_context"] = modification_context

    # If no modification context detected in current turn, check session for persisted context
    if not modification_context and session_state:
        persisted_context = session_state.get("_modification_context")
        if persisted_context:
            modification_context = persisted_context
            merged["_modification_context"] = modification_context


def _finalize_effective_slots_and_trace(
    ctx: _MergeContext,
    effective_intent: str,
    durable_slots_for_persist: Dict[str, Any],
) -> None:
    """Compute _effective_collected_slots, assert intent invariant, emit merge trace."""
    merged = ctx.merged

    # ARCHITECTURAL FIX: Store effective collected slots (post-promotion) for persistence
    # These are the slots that actually satisfy required slots after promotion
    # This ensures slots explicitly satisfied in a turn are persisted so they're not re-computed as missing
    effective_collected_slots = _compute_effective_collected_slots_internal(
        durable_slots_for_persist,
        effective_intent,
        apply_domain_filter=ctx.apply_domain_filter,
        entity_schema=(
            merged.get("_entity_schema")
            if isinstance(merged.get("_entity_schema"), dict)
            else None
        ),
    )
    merged["_effective_collected_slots"] = effective_collected_slots

    # CONTRACT: missing_slots are derived later by finalize_turn_state from intent policy
    # + effective durable slots. Merge prepares normalized slots only.

    # Assertion: session.intent determines planner path exclusively
    # Verify that merged intent matches session intent (when session exists and not reset)
    # CRITICAL: UNKNOWN is a placeholder intent and must be allowed to materialize into concrete intents
    # Only enforce equality for concrete session intents (not UNKNOWN)
    merged_intent = merged.get("intent", {})
    merged_intent_name = (
        merged_intent.get("name", "") if isinstance(merged_intent, dict) else ""
    )
    if ctx.session_intent and ctx.session_status != "READY":
        session_intent_str = (
            ctx.session_intent
            if isinstance(ctx.session_intent, str)
            else ctx.session_intent.get("name", "")
        )
        # Relax assertion for UNKNOWN → concrete intent materialization
        # UNKNOWN is a placeholder and must be allowed to upgrade to concrete intents
        # Only enforce equality when session intent is concrete (safety check for concrete→concrete mismatches)
        if session_intent_str != "UNKNOWN":
            assert merged_intent_name == session_intent_str, (
                f"Session intent mismatch: session.intent={session_intent_str}, "
                f"merged.intent={merged_intent_name}. Session intent must determine planner path exclusively."
            )

    try:
        from core.tracing.decision_trace import TurnTrace
        from core.tracing.merge import MERGE_ELIGIBILITY_ID, emit_merge_slot_trace

        merge_eligibility_id = None
        trace = TurnTrace.current()
        if trace and trace.has_record(MERGE_ELIGIBILITY_ID):
            merge_eligibility_id = MERGE_ELIGIBILITY_ID
        emit_merge_slot_trace(
            session_slots=ctx.initial_session_slots,
            merged_slots=merged.get("slots") if isinstance(merged.get("slots"), dict) else {},
            merge_eligibility_id=merge_eligibility_id,
        )
    except ImportError:
        pass


def _rehydrate_confirmation_state(
    merged: Dict[str, Any],
    session_state: Dict[str, Any],
) -> None:
    """Rehydrate persisted confirmation_state into merged for multi-turn confirm flows."""
    from core.session.confirmation_gate import (
        get_confirmation_state,
        set_confirmation_state,
    )

    session_confirmation = get_confirmation_state(session_state)
    if session_confirmation is not None and get_confirmation_state(merged) is None:
        set_confirmation_state(merged, session_confirmation)


def _promote_and_bind(
    ctx: _MergeContext,
    merged_slots: Dict[str, Any],
    effective_intent: str,
) -> tuple:
    """Promote slots, apply domain filtering, and bind offered time selection.

    Returns (durable_slots_for_persist: Dict, datetime_bound_this_turn: bool).
    Mutates ctx.merged["slots"], ctx.merged["context"], and related keys.
    """
    merged = ctx.merged
    session_state = ctx.session_state

    from core.session.slot_operations import (
        filter_slots_by_domain,
        promote_slots_for_intent,
    )

    # STEP 4.1: Promote slots (in-memory, non-persistent)
    # FIX 3: Merge session context (including date_roles) into merged context for derivation
    context = merged.get("context", {})
    if not isinstance(context, dict):
        context = {}

    # CRITICAL: Merge session context (including date_roles) into merged context for derivation
    if session_state and isinstance(session_state, dict):
        session_context = session_state.get("context", {})
        if isinstance(session_context, dict):
            for key, value in session_context.items():
                if key not in context or not context.get(key):
                    context[key] = value
            if "date_roles" in session_context:
                logger.debug(
                    f"Merged date_roles from session context: {session_context['date_roles']}"
                )

    merged["context"] = context

    # CRITICAL: Promotion starts from merged_slots (session slots + luma slots)
    promoted_slots = promote_slots_for_intent(merged_slots, effective_intent, context)

    # CRITICAL: Verify promotion didn't remove any existing slots
    merged_slot_keys = set(merged_slots.keys())
    promoted_slot_keys = set(promoted_slots.keys())
    if merged_slot_keys:
        lost_in_promotion = merged_slot_keys - promoted_slot_keys
        if lost_in_promotion:
            logger.error(
                f"[SLOT_DURABILITY] VIOLATION: Slots lost during promotion! "
                f"Lost slots: {list(lost_in_promotion)}, "
                f"merged_slots={list(merged_slot_keys)}, "
                f"promoted_slots={list(promoted_slot_keys)}"
            )

    # STEP 4.1.1: Modification context fallback (detection already ran before informational turn)
    modification_context = merged.get("_modification_context")
    if not modification_context and session_state:
        modification_context = session_state.get("_modification_context")
        if modification_context:
            merged["_modification_context"] = modification_context
            logger.debug(
                f"[SESSION_MERGE] Using persisted modification context from session (fallback): {modification_context}"
            )

    # CRITICAL: Promotion MUST write into session.slots
    merged["slots"] = promoted_slots

    datetime_bound_this_turn = False
    if effective_intent == "CREATE_APPOINTMENT":
        from core.planning.temporal_proposal import try_bind_offered_time_selection
        from core.planning.booking_revision import detect_booking_revision
        from core.session.confirmation_gate import get_confirmation_state
        from core.session.invalidation import InvalidationTrigger, apply_invalidation

        revision = detect_booking_revision(
            merged,
            session_state,
            entity_schema=(
                merged.get("_entity_schema")
                if isinstance(merged.get("_entity_schema"), dict)
                else None
            ),
        )
        skip_bind_after_criteria_revision = False
        if revision.any:
            current_turn_promoted_slots = dict(promoted_slots)
            merged["slots"] = dict(promoted_slots)
            apply_invalidation(
                merged,
                InvalidationTrigger.BOOKING_REVISION,
                revision=revision,
                reason="merge_revision",
            )
            # Invalidation removes stale durable values. Restore replacements from
            # this turn so normal planning receives the new service/date facts.
            invalidated_slots = dict(merged["slots"])
            if revision.service:
                for key in ("service_id", "_canonical_service_id"):
                    if current_turn_promoted_slots.get(key) is not None:
                        invalidated_slots[key] = current_turn_promoted_slots[key]
            if revision.criteria:
                for change in revision.changes:
                    if change.field in ("service", "date", "time"):
                        continue
                    value = current_turn_promoted_slots.get(change.field)
                    if value is not None:
                        invalidated_slots[change.field] = value
            if revision.date:
                # New date often arrives only as date_proposal. Do not restore the
                # pre-revision session date that additive merge preserved.
                new_date = None
                for change in revision.changes:
                    if change.field == "date":
                        raw = change.to_value
                        if isinstance(raw, str) and raw:
                            new_date = raw.split("T")[0].split(" ")[0]
                        break
                for key in ("date", "date_range", "start_date", "end_date"):
                    value = current_turn_promoted_slots.get(key)
                    if value is None or not new_date:
                        continue
                    normalized = str(value).split("T")[0].split(" ")[0]
                    if normalized == new_date:
                        invalidated_slots[key] = value
            merged["slots"] = invalidated_slots
            promoted_slots = merged["slots"]
            if revision.invalidates_availability:
                # Genuine mid-flow replacements only (detect_booking_revision
                # excludes first acquisition / same-value restatement). Carried
                # time proposals would rebind against stale presented offers.
                if not merged.get("_current_turn_has_time"):
                    merged.pop("time_constraint", None)
                    merged.pop("time_proposal", None)
                # Service/staff criteria revision keeps the active search
                # date_proposal so the new criteria are searched on the same day.
                # Date revisions replace the proposal via current-turn merge.
                merged["_revision_invalidated_availability"] = True
                skip_bind_after_criteria_revision = True
            logger.info(
                "[BOOKING_CONFIRMATION] Applied revision service=%s date=%s "
                "time=%s criteria=%s",
                revision.service,
                revision.date,
                revision.time,
                revision.criteria,
            )

        from core.planning.pipeline.requests import is_availability_turn_operation

        availability_op = is_availability_turn_operation(ctx.turn_operation)
        skip_stale_availability_bind = availability_op and not merged.get(
            "_current_turn_has_time"
        )
        if skip_stale_availability_bind:
            # Do not rebind a prior selection on an availability browse/search turn.
            slots_without_time = dict(promoted_slots)
            for key in ("time", "has_datetime", "datetime_range"):
                slots_without_time.pop(key, None)
            promoted_slots = slots_without_time
            merged["slots"] = promoted_slots
            merged.pop("resolved_datetime_range", None)
            merged.pop("time_match_outcome", None)
            merged.pop("time_resolution", None)
            skip_bind_after_criteria_revision = True

        bind_result = None
        if not skip_bind_after_criteria_revision:
            bind_result = try_bind_offered_time_selection(
                promoted_slots,
                session_state,
                date_proposal=merged.get("date_proposal"),
                time_proposal=merged.get("time_proposal"),
                temporal=merged.get("temporal"),
                turn_payload=merged,
            )
        if bind_result:
            promoted_slots = bind_result["slots"]
            merged["slots"] = promoted_slots
            merged["resolved_datetime_range"] = bind_result["resolved_datetime_range"]
            datetime_bound_this_turn = True
            from core.planning.time_resolution import TIME_MATCH_EXACT

            merged["time_match_outcome"] = TIME_MATCH_EXACT
            # Fresh bind replaces prior selection; plan_builder may re-enter pending.
            apply_invalidation(
                merged,
                InvalidationTrigger.TIME_REBOUND,
                reason="rebound_selection",
            )
        elif merged.get("time_proposal") and not skip_bind_after_criteria_revision:
            from core.planning.time_resolution import (
                TIME_MATCH_EXACT,
                apply_post_bind_time_resolution,
            )

            post_bind_resolution = apply_post_bind_time_resolution(merged, session_state)
            if (
                post_bind_resolution
                and merged.get("time_match_outcome") == TIME_MATCH_EXACT
            ):
                promoted_slots = merged.get("slots") or promoted_slots
                datetime_bound_this_turn = True

        # User revised date/time proposals — drop stale pending confirmation.
        if not datetime_bound_this_turn and (
            merged.get("date_proposal") or merged.get("time_proposal")
        ):
            if get_confirmation_state(merged) == "pending":
                apply_invalidation(
                    merged,
                    InvalidationTrigger.UNBOUND_PROPOSAL_WHILE_PENDING,
                    reason="unbound_proposal_revision",
                )

    # STEP 4.1.5: Apply domain slot filtering BEFORE required-slot computation
    entity_schema = (
        merged.get("_entity_schema")
        if isinstance(merged.get("_entity_schema"), dict)
        else None
    )
    domain_filtered_slots = filter_slots_by_domain(
        promoted_slots,
        effective_intent,
        apply_domain_filter=ctx.apply_domain_filter,
        entity_schema=entity_schema,
    )

    from core.planning.temporal_proposal import (
        strip_unconfirmed_temporal_slots,
        temporal_slots_confirmed,
    )

    durable_slots_for_persist = strip_unconfirmed_temporal_slots(
        domain_filtered_slots,
        effective_intent,
        session_state,
        confirmed=datetime_bound_this_turn or temporal_slots_confirmed(session_state),
    )
    merged["slots"] = durable_slots_for_persist

    return durable_slots_for_persist, datetime_bound_this_turn


def _handle_informational_turn_and_effective_intent(
    ctx: _MergeContext,
    merged_slots: Dict[str, Any],
    merged_intent_name: str,
    raw_luma_slots: Dict[str, Any],
) -> tuple:
    """Handle intent-change filtering, modification context, informational-turn early return,
    and effective_intent resolution.

    Returns (should_early_return: bool, effective_intent: str, merged_slots: Dict).
    When should_early_return is True the caller must immediately return
    _finalize_merged_luma_response(ctx.merged, luma_response).
    merged_slots may differ from the input if intent-change filtering was applied.
    Mutates ctx.merged throughout.
    """
    merged = ctx.merged
    session_state = ctx.session_state
    session_intent = ctx.session_intent

    from core.session.slot_operations import filter_collected_slots_for_intent

    # STEP 3.6: Handle intent change (hard boundary)
    # CRITICAL MERGE ORDER: Session slots MUST be fully merged into merged_slots BEFORE
    # intent-change filtering is applied.
    session_slots = session_state.get("slots", {}) if session_state else {}
    if not isinstance(session_slots, dict):
        session_slots = {}

    session_intent_name = (
        session_intent
        if isinstance(session_intent, str)
        else (
            session_intent.get("name", "") if isinstance(session_intent, dict) else ""
        )
    )
    intent_changed = (
        merged_intent_name
        and session_intent_name
        and merged_intent_name != session_intent_name
        and merged_intent_name != "UNKNOWN"
    )

    if intent_changed:
        logger.info(
            f"[INTENT_CHANGE] Intent changed: previous={session_intent_name} -> new={merged_intent_name}"
        )

        # CRITICAL: Ensure all session slots are in merged_slots before filtering
        if session_slots:
            missing_from_merge = set(session_slots.keys()) - set(merged_slots.keys())
            if missing_from_merge:
                logger.warning(
                    f"[INTENT_CHANGE] Session slots missing from merged_slots before filtering! "
                    f"Missing: {list(missing_from_merge)}, restoring..."
                )
                for key in missing_from_merge:
                    merged_slots[key] = session_slots[key]
                    logger.info(
                        f"[INTENT_CHANGE] Restored session slot before filtering: {key} = {session_slots[key]}"
                    )

        slots_before_filtering = merged_slots.copy()
        logger.info(
            f"[INTENT_CHANGE] Slots before filtering: {list(slots_before_filtering.keys())} = {slots_before_filtering}"
        )

        merged_slots = filter_collected_slots_for_intent(
            merged_slots, session_intent_name, merged_intent_name
        )
        merged["slots"] = merged_slots

        logger.info(
            f"[INTENT_CHANGE] Slots after filtering: {list(merged_slots.keys())} = {merged_slots}"
        )

        dropped_slots = set(slots_before_filtering.keys()) - set(merged_slots.keys())
        if dropped_slots:
            logger.info(
                f"[INTENT_CHANGE] Dropped slots: {list(dropped_slots)}")

        # Clear context.date_roles on intent change (they are intent-specific)
        context = merged.get("context", {})
        if isinstance(context, dict) and "date_roles" in context:
            del context["date_roles"]
            merged["context"] = context
            logger.debug("[INTENT_CHANGE] Cleared date_roles (intent-specific)")

        # Delete stale missing_slots — finalize_turn_state derives canonical value for new intent
        if "missing_slots" in merged:
            del merged["missing_slots"]
        if "_force_recompute_missing_slots" in merged:
            del merged["_force_recompute_missing_slots"]
        logger.debug(
            "[INTENT_CHANGE] Cleared stale missing_slots for new intent contract"
        )

    # STEP 3.4.1: Detect and persist modification context for MODIFY_* intents
    # CRITICAL: This must run BEFORE informational-turn early return and BEFORE slot promotion
    _detect_modification_context(merged, raw_luma_slots, merged_intent_name, session_state)

    # STEP 3.5: Detect informational turns explicitly
    # ARCHITECTURAL INVARIANT: Informational turns must NEVER mutate slots or recompute missing_slots
    core_intents = {
        "CREATE_APPOINTMENT",
        "CREATE_RESERVATION",
        "MODIFY_BOOKING",
        "CANCEL_BOOKING",
    }

    is_informational_intent = (
        merged_intent_name
        and merged_intent_name not in core_intents
        and merged_intent_name != "UNKNOWN"
    )

    has_active_planning = (
        session_state
        and isinstance(session_state, dict)
        and session_state.get("status") == "NEEDS_CLARIFICATION"
        and session_intent_name
        and session_intent_name in core_intents
    )

    turn_meta = merged.get("turn") if isinstance(merged.get("turn"), dict) else {}
    understanding = turn_meta.get("understanding") or merged.get("understanding")
    has_durable_booking = bool(
        session_state
        and isinstance(session_state, dict)
        and session_intent_name
        and session_intent_name in core_intents
    )

    session_slots_dict = (
        session_state.get("slots", {})
        if (session_state and isinstance(session_state, dict))
        else {}
    )
    current_turn_has_new_slots = bool(
        merged_slots and any(key not in session_slots_dict for key in merged_slots)
    )

    from core.planning.booking_revision import has_actionable_booking_facts
    from core.planning.planning_evidence import require_planning_evidence

    has_actionable_this_turn = current_turn_has_new_slots or has_actionable_booking_facts(
        merged, session_state
    )

    # Read-only: Stage 02 stamps planning evidence before merge. Never recompute.
    has_planning_evidence = require_planning_evidence(merged)

    is_modify_intent = merged_intent_name in ("MODIFY_BOOKING", "MODIFY_RESERVATION")
    # Preserve only unrecognized turns with no structured planning evidence.
    # UNDERSTOOD + no evidence must not restore session over intentional drops.
    preserve_booking_state = (
        has_durable_booking
        and not is_modify_intent
        and understanding == "UNRECOGNIZED_INPUT"
        and not has_planning_evidence
    )
    if preserve_booking_state:
        logger.info(
            f"[INFORMATIONAL_TURN] Detected informational turn: "
            f"luma_intent={merged_intent_name}, session_intent={session_intent_name}, "
            f"has_planning_evidence=False understanding={understanding!r} "
            f"has_active_planning={bool(has_active_planning)} "
            f"has_actionable={bool(has_actionable_this_turn)}"
        )

        if session_state and isinstance(session_state, dict):
            session_slots_to_preserve = session_state.get("slots", {})
            if isinstance(session_slots_to_preserve, dict):
                for slot_name, slot_value in session_slots_to_preserve.items():
                    if slot_name not in merged_slots:
                        merged_slots[slot_name] = slot_value
                merged["slots"] = merged_slots
                logger.info(
                    f"[INFORMATIONAL_TURN] Preserved slots: {list(session_slots_to_preserve.keys())}"
                )

        previous_missing_slots: list = []
        if session_state and isinstance(session_state, dict):
            from core.planning.temporal_proposal import resolve_session_proposals

            _proposals = resolve_session_proposals(previous_session_state=session_state)
            if _proposals["date_proposal"] is not None:
                merged["date_proposal"] = _proposals["date_proposal"]
            if _proposals["time_proposal"] is not None:
                merged["time_proposal"] = _proposals["time_proposal"]

            # Propagate stored missing_slots only — canonical derivation is finalize_turn_state.
            stored_missing = session_state.get("missing_slots", [])
            if isinstance(stored_missing, list):
                previous_missing_slots = stored_missing

        merged["missing_slots"] = previous_missing_slots
        logger.info(
            f"[INFORMATIONAL_TURN] Preserved missing_slots: {previous_missing_slots}"
        )

        effective_collected_slots = {
            slot_name: slot_value
            for slot_name, slot_value in session_slots_dict.items()
            if slot_value is not None
        }
        merged["_effective_collected_slots"] = effective_collected_slots

        # Signal caller to return immediately
        return True, "", merged_slots

    # Resolved intent for downstream promotion — authoritative intent.name only.
    effective_intent = merged.get("intent", {}).get("name", "")
    if not effective_intent:
        intent_obj = merged.get("intent", {})
        if isinstance(intent_obj, dict):
            effective_intent = intent_obj.get("name", "")
        else:
            effective_intent = merged_intent_name or ""

    if is_informational_intent and has_actionable_this_turn and session_intent_name:
        effective_intent = session_intent_name
        merged["intent"] = {"name": session_intent_name}
        logger.info(
            f"[INFORMATIONAL_TURN] Non-core intent with actionable facts: "
            f"luma_intent={merged_intent_name}, session_intent={session_intent_name}, "
            f"new_slots={[k for k in merged_slots.keys() if k not in session_slots_dict]}"
        )

    return False, effective_intent, merged_slots


def _merge_slots_additive(
    ctx: _MergeContext,
    luma_slots: Dict[str, Any],
) -> tuple:
    """Additively merge session slots with Luma slots; handle service_id, proposals, and booking re-injection.

    Returns (merged_slots, merged_intent_name).
    Mutates ctx.merged["slots"], ctx.merged["date_proposal"], ctx.merged["time_proposal"],
    and ctx.merged["booking"] as side effects.
    """
    merged = ctx.merged
    session_state = ctx.session_state

    # STEP 3: Merge slots: Start with session slots, then merge new entities from Luma
    # CRITICAL: This must be additive and non-destructive - preserve all existing slots
    # ARCHITECTURAL INVARIANT: session.slots is the single source of truth for collected slots
    # Slots present in session MUST be preserved across turns unless intent changes
    session_slots = session_state.get("slots", {}) if session_state else {}
    if not isinstance(session_slots, dict):
        session_slots = {}

    logger.info(
        f"[SLOT_DURABILITY] session.slots before merge: {list(session_slots.keys())} = {session_slots}"
    )

    # Start with session slots (preserve all previously resolved slots)
    # CRITICAL MERGE ORDER: This merge MUST happen BEFORE intent-change filtering
    # to ensure valid slots from previous turns are preserved when transitioning intents.
    merged_slots = session_slots.copy()

    # CRITICAL: Preserve raw service_id from session if Luma doesn't provide it
    raw_service_id_from_session = session_slots.get("service_id")
    canonical_service_id_from_session = session_slots.get("_canonical_service_id")

    # Additively merge Luma slots into session slots
    # Luma slots are delta updates - they add new information or refine existing slots
    # But never delete slots that exist in session but not in Luma response
    for key, value in luma_slots.items():
        # CRITICAL: If time is a dict (from time_constraint), extract start value
        if key == "time" and isinstance(value, dict):
            time_start = value.get("start")
            if time_start:
                merged_slots[key] = time_start
                logger.debug(
                    f"Normalized time slot from dict to start value: {time_start}"
                )
            else:
                merged_slots[key] = value
        elif value is not None:  # Only merge non-None values
            merged_slots[key] = value

    # Preserve raw service_id from session only when user did NOT mention a service this turn.
    # service_candidates non-empty means the user said a service term that matched ambiguously.
    current_candidates = (
        merged.get("service_candidates")
        or (merged.get("facts") or {}).get("service_candidates")
        or []
    )
    if raw_service_id_from_session:
        if current_candidates:
            from core.session.invalidation import (
                InvalidationTrigger,
                apply_invalidation,
            )

            apply_invalidation(
                merged,
                InvalidationTrigger.AMBIGUOUS_SERVICE,
                merged_slots=merged_slots,
                raw_service_id_from_session=raw_service_id_from_session,
                current_candidates=current_candidates,
            )
        elif luma_slots.get("service_id") is None:
            merged_slots["service_id"] = raw_service_id_from_session
            logger.debug(
                f"Preserved raw service_id from session: {raw_service_id_from_session}"
            )

    # Preserve canonical service_id from session only when raw service was also
    # preserved (Luma did not supply a new service_id this turn).
    if (
        canonical_service_id_from_session
        and "_canonical_service_id" not in merged_slots
        and "service_id" in merged_slots
        and luma_slots.get("service_id") is None
    ):
        merged_slots["_canonical_service_id"] = canonical_service_id_from_session
        logger.debug(
            f"Preserved canonical service_id from session: {canonical_service_id_from_session}"
        )

    # Log merge for debugging
    session_slot_keys = set(session_slots.keys())
    luma_slot_keys = set(luma_slots.keys())
    merged_slot_keys = set(merged_slots.keys())
    added_slots = merged_slot_keys - session_slot_keys
    preserved_slots = session_slot_keys & merged_slot_keys

    logger.debug(
        f"Slot merge: session={list(session_slot_keys)}, luma={list(luma_slot_keys)}, "
        f"merged={list(merged_slot_keys)}, added={list(added_slots)}, preserved={list(preserved_slots)}"
    )

    # CRITICAL: Verify all session slots are preserved
    if session_slot_keys:
        intentionally_dropped = merged.get("_intentionally_dropped_slots") or set()
        lost_slots = session_slot_keys - merged_slot_keys - intentionally_dropped
        if lost_slots:
            logger.error(
                f"[SLOT_DURABILITY] VIOLATION: Session slots lost during merge! "
                f"Lost slots: {list(lost_slots)}, "
                f"session_slots={list(session_slot_keys)}, "
                f"merged_slots={list(merged_slot_keys)}"
            )

    # NEW BOOKING REQUEST DETECTION: Clear booking_id when user provides a new booking request
    merged_intent_name = (
        merged.get("intent", {}).get("name", "")
        if isinstance(merged.get("intent"), dict)
        else ""
    )
    if merged_intent_name == "CREATE_APPOINTMENT" and "booking_id" in merged_slots:
        from core.session.invalidation import InvalidationTrigger, apply_invalidation

        apply_invalidation(
            merged,
            InvalidationTrigger.NEW_BOOKING_REQUEST,
            merged_slots=merged_slots,
            session_state=session_state,
            merged_intent_name=merged_intent_name,
            luma_slots=luma_slots,
        )

    # CONTRACT ENFORCEMENT: Lift explicit user-provided dates from context into slots
    context = merged.get("context", {})
    if isinstance(context, dict):
        # FIX 77: Priority order for date extraction:
        # 1. Extract from context.start_date as date (if date not already in merged_slots)
        # 2. Extract from context.date as date (if date not already in merged_slots)
        # 3. Extract from merged_slots.start_date as date (if start_date exists but date doesn't)

        # Priority 1: Extract start_date from context as date (raw slot) for persistence
        if "start_date" in context and "date" not in merged_slots:
            merged_slots["date"] = context["start_date"]
            logger.debug(
                f"[FIX77] Extracted date from context.start_date into slots for persistence: {context['start_date']}"
            )

        # Priority 2: Direct date value in context (only if date not already extracted)
        if "date" in context and "date" not in merged_slots:
            merged_slots["date"] = context["date"]
            logger.debug(
                f"[FIX77] Extracted date from context.date into slots for persistence: {context['date']}"
            )

        # Priority 3: Extract from merged_slots.start_date as date
        if "start_date" in merged_slots and "date" not in merged_slots:
            merged_slots["date"] = merged_slots["start_date"]
            logger.debug(
                f"[FIX77] Extracted date from merged_slots.start_date for persistence: {merged_slots['start_date']}"
            )

        # Extract date_range from context if provided (e.g., "next week", "this weekend")
        if "date_range" in context and "date_range" not in merged_slots:
            merged_slots["date_range"] = context["date_range"]
            logger.debug(
                f"Extracted date_range from context.date_range into slots for persistence: {context['date_range']}"
            )

        # Ensure date_roles are preserved in context for derivation layer
        if "date_roles" in context:
            pass

    # CRITICAL: Use effective_intent (computed EARLY after UNKNOWN override) for all operations
    merged_intent_name = (
        merged.get("intent", {}).get("name", "")
        if isinstance(merged.get("intent"), dict)
        else ""
    )

    # Update merged response with merged slots (non-destructive)
    merged["slots"] = merged_slots

    from core.planning.temporal_proposal import (
        extract_nlu_proposals,
        merge_session_proposals,
    )

    _nlu_proposals = extract_nlu_proposals(merged)
    _merged_proposals = merge_session_proposals(
        session_state,
        _nlu_proposals["date_proposal"],
        _nlu_proposals["time_proposal"],
    )
    if _merged_proposals["date_proposal"] is not None:
        merged["date_proposal"] = _merged_proposals["date_proposal"]
    if _merged_proposals["time_proposal"] is not None:
        merged["time_proposal"] = _merged_proposals["time_proposal"]

    # Assertion: All session slots must be preserved in merged slots
    # ARCHITECTURAL INVARIANT: Slots are durable facts - they must never be lost
    if session_slots:
        intentionally_dropped = merged.get("_intentionally_dropped_slots") or set()
        missing_session_slots = (
            set(session_slots.keys()) - set(merged_slots.keys()) - intentionally_dropped
        )
        if missing_session_slots:
            logger.error(
                f"[SLOT_DURABILITY] VIOLATION: Session slots were lost during merge! "
                f"Missing: {list(missing_session_slots)}, "
                f"session_slots={list(session_slots.keys())}, "
                f"merged_slots={list(merged_slots.keys())}"
            )
            # Restore missing session slots (fail-safe)
            for key in missing_session_slots:
                merged_slots[key] = session_slots[key]
                logger.warning(
                    f"[SLOT_DURABILITY] Restored lost slot: {key} = {session_slots[key]}"
                )
            merged["slots"] = merged_slots

    # STEP 3.5: Re-inject service_id into booking.services for service bookings
    # When Luma returns datetime_range/time updates without repeating service,
    # we must preserve service_id from session and inject it into booking object
    if merged_intent_name == "CREATE_APPOINTMENT":
        service_id_in_slots = merged_slots.get("service_id")
        booking_obj = merged.get("booking")

        if service_id_in_slots:
            if not isinstance(booking_obj, dict):
                booking_obj = {}
                merged["booking"] = booking_obj

            booking_services = booking_obj.get("services")
            if not booking_services or (
                isinstance(booking_services, list) and len(booking_services) == 0
            ):
                booking_obj["services"] = [{"text": service_id_in_slots}]
                logger.debug(
                    f"Re-injected service_id into booking.services during merge: {service_id_in_slots}"
                )

    return merged_slots, merged_intent_name


def _extract_semantic_slots(ctx: _MergeContext, luma_slots: Dict[str, Any]) -> None:
    """Extract date/time slots from entities, semantic trace, and booking object.

    Mutates luma_slots in place.  Reads only from ctx.merged and ctx.session_state.
    """
    merged = ctx.merged

    # Derive effective intent (already set by STEP 1 authority block)
    merged_intent_name = (
        merged.get("intent", {}).get("name", "")
        if isinstance(merged.get("intent"), dict)
        else ""
    )

    # DEBUG: Log Luma response structure for date extraction debugging
    logger.debug(
        f"merge_luma_with_session: Checking for date/time in Luma response. "
        f"slots={list(luma_slots.keys())}, "
        f"has_trace={bool(merged.get('trace'))}, "
        f"has_stages={bool(merged.get('stages'))}, "
        f"has_entities={bool(merged.get('entities'))}"
    )

    # Extract date using the helper (checks all possible locations)
    extracted_date = _extract_date_from_luma_response(merged)
    if extracted_date and "date" not in luma_slots:
        luma_slots["date"] = extracted_date
        logger.debug(
            f"Extracted date using comprehensive helper: {extracted_date}")

    # DEBUG: Log if date extraction failed (for weekday debugging)
    debug_weekday = os.getenv("DEBUG_WEEKDAY", "0") == "1"
    if (
        debug_weekday
        and "date" not in luma_slots
        and ctx.session_state
        and ctx.session_state.get("status") == "NEEDS_CLARIFICATION"
    ):
        logger.warning(
            f"DEBUG_WEEKDAY: Date extraction failed for follow-up. "
            f"luma_slots={list(luma_slots.keys())}, "
            f"merged_keys={list(merged.keys())}"
        )

    # Extract semantic fields for slot extraction (when slots is empty/partial)
    # Check multiple locations for date/time information (Luma may provide in different places)
    trace = merged.get("trace", {})
    semantic_data = None

    # Try trace.semantic first
    if isinstance(trace, dict):
        semantic_data = trace.get("semantic", {})

    # Try stages.semantic.resolved_booking as fallback
    if not semantic_data:
        stages = merged.get("stages", {})
        if isinstance(stages, dict):
            semantic_stage = stages.get("semantic", {})
            if isinstance(semantic_stage, dict):
                semantic_data = semantic_stage.get("resolved_booking", {})

    # Also check if semantic data exists directly in stages.semantic (not just resolved_booking)
    if not semantic_data:
        stages = merged.get("stages", {})
        if isinstance(stages, dict):
            semantic_stage = stages.get("semantic", {})
            if isinstance(semantic_stage, dict) and semantic_stage:
                semantic_data = semantic_stage

    # Also check entities for date/time (Luma may provide date directly in entities)
    entities = merged.get("entities", {})
    if isinstance(entities, dict):
        # Check if date is in entities but not yet in slots
        if "date" in entities and "date" not in luma_slots:
            date_value = entities.get("date")
            if date_value:
                luma_slots["date"] = date_value
                logger.debug(
                    f"Extracted date from entities.date: {date_value}")
        # TIME_CONSTRAINT RULE: For CREATE_APPOINTMENT, do NOT extract time from entities
        # time_constraint is authoritative; slots.time is legacy-only and must not drive planning
        if "time" in entities and "time" not in luma_slots:
            # Only extract time for non-CREATE_APPOINTMENT intents
            # For CREATE_APPOINTMENT, time_constraint is authoritative (handled separately)
            if merged_intent_name != "CREATE_APPOINTMENT":
                time_value = entities.get("time")
                if time_value:
                    luma_slots["time"] = time_value
                    logger.debug(
                        f"Extracted time from entities.time: {time_value}")
                else:
                    logger.debug(
                        f"Skipped time extraction from entities for CREATE_APPOINTMENT (time_constraint is authoritative)"
                    )

    # Check if semantic data exists but wasn't found in trace/stages (try direct access)
    # Sometimes Luma provides semantic data at root level or in different structure
    if not semantic_data:
        # Try merged.get("semantic") directly
        root_semantic = merged.get("semantic")
        if isinstance(root_semantic, dict):
            semantic_data = root_semantic
            logger.debug("Found semantic data at root level")

    # If we still have semantic_data, process it (this handles the case where we found it in a different location)
    # Process semantic data even if slots.date exists (may need to extract role-specific slots from date_roles)
    if isinstance(semantic_data, dict):
        date_refs = semantic_data.get("date_refs", [])
        date_mode = semantic_data.get("date_mode", "")
        time_constraint = semantic_data.get("time_constraint")
        time_refs = semantic_data.get("time_refs", [])
        date_roles = semantic_data.get("date_roles", [])

        # Process date_refs if found
        # CONTRACT ENFORCEMENT: For CREATE_RESERVATION, extract role-specific slots when date_roles available
        # If no date_roles, extract as date (will be normalized to start_date later)
        if date_refs and isinstance(date_refs, list) and len(date_refs) > 0:
            if merged_intent_name == "CREATE_RESERVATION":
                # For reservations, extract role-specific slots when date_roles explicitly labels them
                if date_roles:
                    if "START_DATE" in date_roles and "start_date" not in luma_slots:
                        luma_slots["start_date"] = date_refs[0]
                        logger.debug(
                            f"Extracted start_date from semantic.date_refs with START_DATE role: {date_refs[0]}"
                        )
                    if "END_DATE" in date_roles and "end_date" not in luma_slots:
                        # END_DATE might be in a later position in date_refs
                        if isinstance(date_refs, list):
                            # Find index of END_DATE in date_roles to match with date_refs
                            try:
                                end_date_idx = list(
                                    date_roles).index("END_DATE")
                                if end_date_idx < len(date_refs):
                                    luma_slots["end_date"] = date_refs[end_date_idx]
                                    logger.debug(
                                        f"Extracted end_date from semantic.date_refs with END_DATE role: {date_refs[end_date_idx]}"
                                    )
                            except (ValueError, IndexError):
                                # Fallback to last date if END_DATE role exists and we have multiple dates
                                if len(date_refs) > 1:
                                    luma_slots["end_date"] = date_refs[-1]
                                    logger.debug(
                                        f"Extracted end_date from semantic.date_refs (last date, END_DATE role): {date_refs[-1]}"
                                    )
                # FIX: For CREATE_RESERVATION, do NOT extract generic "date" slot when date_roles is missing
                # Only extract role-specific slots (start_date, end_date) when explicitly labeled by date_roles
                # If Luma returns only date without date_roles, keep it as date in context but do NOT satisfy start_date requirement
                # This prevents auto-promotion of generic date to start_date
            elif date_mode == "single_day" or not date_mode:
                # For service appointments, extract date if single_day mode
                if "date" not in luma_slots and "start_date" not in luma_slots:
                    luma_slots["date"] = date_refs[0]
                    logger.debug(
                        f"Extracted date from semantic.date_refs (root/found): {date_refs[0]}"
                    )

        # Process time if found
        # TIME_CONSTRAINT RULE: For CREATE_APPOINTMENT, do NOT extract time from time_constraint/time_refs
        # time_constraint is authoritative; slots.time is legacy-only and must not drive planning
        # Only derive slots.time AFTER planning for backward compatibility (done in planning pipeline)
        if (time_refs or time_constraint) and "time" not in luma_slots:
            # Only extract time for non-CREATE_APPOINTMENT intents
            # For CREATE_APPOINTMENT, time_constraint is authoritative (handled separately in planning)
            if merged_intent_name != "CREATE_APPOINTMENT":
                if time_constraint:
                    # If time_constraint is a dict with start/mode, extract start (e.g., "12:00" for "noon")
                    if isinstance(time_constraint, dict):
                        constraint_start = time_constraint.get("start")
                        constraint_mode = time_constraint.get("mode", "")
                        if constraint_start:
                            luma_slots["time"] = constraint_start
                            logger.debug(
                                f"Extracted time from semantic.time_constraint.start: {constraint_start} (mode={constraint_mode})"
                            )
                        else:
                            # Fallback: use time_constraint dict as-is if no start
                            luma_slots["time"] = time_constraint
                            logger.debug(
                                f"Extracted time from semantic.time_constraint (dict): {time_constraint}"
                            )
                    else:
                        # time_constraint is a string, use directly
                        luma_slots["time"] = time_constraint
                        logger.debug(
                            f"Extracted time from semantic.time_constraint: {time_constraint}"
                        )
                elif time_refs and isinstance(time_refs, list) and len(time_refs) > 0:
                    luma_slots["time"] = time_refs[0]
                    logger.debug(
                        f"Extracted time from semantic.time_refs: {time_refs[0]}"
                    )
            else:
                # CREATE_APPOINTMENT: Skip time extraction - time_constraint is authoritative
                logger.debug(
                    f"Skipped time extraction from time_constraint/time_refs for CREATE_APPOINTMENT (time_constraint is authoritative)"
                )

    # Project semantic fields into slots for follow-ups
    # Extract from trace.semantic or stages.semantic.resolved_booking
    if isinstance(semantic_data, dict):
        date_refs = semantic_data.get("date_refs", [])
        date_mode = semantic_data.get("date_mode", "")
        time_constraint = semantic_data.get("time_constraint")
        time_refs = semantic_data.get("time_refs", [])
        date_roles = semantic_data.get("date_roles", [])

        # If date_refs exists:
        if date_refs:
            # Check date_roles to determine which slot to fill
            if date_roles:
                if "START_DATE" in date_roles and "start_date" not in luma_slots:
                    if isinstance(date_refs, list) and len(date_refs) > 0:
                        luma_slots["start_date"] = date_refs[0]
                if "END_DATE" in date_roles and "end_date" not in luma_slots:
                    if isinstance(date_refs, list):
                        # Find index of END_DATE in date_roles to match with date_refs
                        try:
                            end_date_idx = list(date_roles).index("END_DATE")
                            if end_date_idx < len(date_refs):
                                luma_slots["end_date"] = date_refs[end_date_idx]
                        except (ValueError, IndexError):
                            # Fallback to last date if END_DATE role exists and we have multiple dates
                            if len(date_refs) > 1:
                                luma_slots["end_date"] = date_refs[-1]
                    # CONTRACT ENFORCEMENT: Do NOT infer end_date from single date
                    # end_date must be explicitly provided or extracted from date_refs with END_DATE role

            # CONTRACT ENFORCEMENT: For CREATE_RESERVATION, do NOT extract generic "date" slot
            # Only extract role-specific slots (start_date, end_date) when explicitly labeled
            if merged_intent_name == "CREATE_RESERVATION":
                # For reservations, only extract if date_roles explicitly provides role labels
                # Do NOT extract generic "date" slot
                if date_roles:
                    # Role-specific extraction is handled above (lines 314-316)
                    pass
                # Do NOT fall through to generic date extraction for reservations
            else:
                # For service appointments (CREATE_APPOINTMENT), extract date slot
                # single_day → slots["date"] (for service appointments)
                if (
                    date_mode == "single_day"
                    and "date" not in luma_slots
                    and "start_date" not in luma_slots
                ):
                    if isinstance(date_refs, list) and len(date_refs) > 0:
                        luma_slots["date"] = date_refs[0]
                # range → slots["date_range"] or start_date/end_date
                elif date_mode == "range":
                    if (
                        "date_range" not in luma_slots
                        and "start_date" not in luma_slots
                    ):
                        if isinstance(date_refs, list):
                            if len(date_refs) >= 2:
                                # Only assign if we have both dates - no inference
                                luma_slots["start_date"] = date_refs[0]
                                luma_slots["end_date"] = date_refs[-1]
                            # CONTRACT ENFORCEMENT: Do NOT infer start_date or end_date from single date in range mode
                            # Both dates must be explicitly provided
                # If no date_mode specified but date_refs exist, assume single_day for service appointments
                elif not date_mode and date_refs:
                    if "date" not in luma_slots and "start_date" not in luma_slots:
                        if isinstance(date_refs, list) and len(date_refs) > 0:
                            luma_slots["date"] = date_refs[0]

        # If time_refs or time_constraint exists → slots["time"]
        # TIME_CONSTRAINT RULE: For CREATE_APPOINTMENT, do NOT extract time from time_constraint/time_refs
        # time_constraint is authoritative; slots.time is legacy-only and must not drive planning
        # Only derive slots.time AFTER planning for backward compatibility (done in planning pipeline)
        if (time_refs or time_constraint) and "time" not in luma_slots:
            # Only extract time for non-CREATE_APPOINTMENT intents
            # For CREATE_APPOINTMENT, time_constraint is authoritative (handled separately in planning)
            if merged_intent_name != "CREATE_APPOINTMENT":
                if time_constraint:
                    # If time_constraint is a dict with start/mode, extract start (e.g., "12:00" for "noon")
                    if isinstance(time_constraint, dict):
                        constraint_start = time_constraint.get("start")
                        constraint_mode = time_constraint.get("mode", "")
                        if constraint_start:
                            luma_slots["time"] = constraint_start
                            logger.debug(
                                f"Extracted time from semantic.time_constraint.start (projection): {constraint_start} (mode={constraint_mode})"
                            )
                        else:
                            # Fallback: use time_constraint dict as-is if no start
                            luma_slots["time"] = time_constraint
                            logger.debug(
                                f"Extracted time from semantic.time_constraint (dict, projection): {time_constraint}"
                            )
                    else:
                        # time_constraint is a string, use directly
                        luma_slots["time"] = time_constraint
                        logger.debug(
                            f"Extracted time from semantic.time_constraint (projection): {time_constraint}"
                        )
                elif time_refs and isinstance(time_refs, list) and len(time_refs) > 0:
                    luma_slots["time"] = time_refs[0]
                    logger.debug(
                        f"Extracted time from semantic.time_refs (projection): {time_refs[0]}"
                    )
            else:
                # CREATE_APPOINTMENT: Skip time extraction - time_constraint is authoritative
                logger.debug(
                    f"Skipped time extraction from time_constraint/time_refs (projection) for CREATE_APPOINTMENT (time_constraint is authoritative)"
                )

    # Additional fallback: Check if Luma provided date/time directly in merged response
    # (Sometimes Luma provides date in slots even without semantic data)
    if "date" not in luma_slots:
        # Check if date exists in merged response slots (Luma might have added it)
        direct_date = merged.get("slots", {}).get("date")
        if direct_date:
            luma_slots["date"] = direct_date
            logger.debug(
                f"Extracted date from merged.slots.date: {direct_date}")

    # TIME_CONSTRAINT RULE: For CREATE_APPOINTMENT, do NOT extract time from merged.slots.time
    # time_constraint is authoritative; slots.time is legacy-only and must not drive planning
    if "time" not in luma_slots:
        # Check if time exists in merged response slots
        # Only extract time for non-CREATE_APPOINTMENT intents
        # For CREATE_APPOINTMENT, time_constraint is authoritative (handled separately in planning)
        if merged_intent_name != "CREATE_APPOINTMENT":
            direct_time = merged.get("slots", {}).get("time")
            if direct_time:
                luma_slots["time"] = direct_time
                logger.debug(
                    f"Extracted time from merged.slots.time: {direct_time}")
        else:
            # CREATE_APPOINTMENT: Skip time extraction - time_constraint is authoritative
            logger.debug(
                f"Skipped time extraction from merged.slots.time for CREATE_APPOINTMENT (time_constraint is authoritative)"
            )

    # Check booking object for date/time (Luma might provide in booking.datetime_range)
    booking_obj = merged.get("booking")
    if isinstance(booking_obj, dict) and "date" not in luma_slots:
        booking_date = booking_obj.get("date") or (
            booking_obj.get("datetime_range", {}).get("start")
            if isinstance(booking_obj.get("datetime_range"), dict)
            else None
        )
        if booking_date:
            # Extract date part if it's a datetime
            if isinstance(booking_date, str):
                date_part = booking_date.split("T")[0].split(" ")[0]
                luma_slots["date"] = date_part
                logger.debug(
                    f"Extracted date from booking object: {date_part}")


def _extract_raw_luma_slots(ctx: _MergeContext) -> Dict[str, Any]:
    """Promote Luma facts to slots, reconcile service_id, and return raw_luma_slots.

    Writes merged["_raw_luma_slots"] as a snapshot for invariant checks.
    Returns the raw slot dict (before semantic extraction and session merge).
    """
    merged = ctx.merged

    from core.planning.luma_facts_adapter import (
        facts_to_slots,
        merge_promoted_luma_slots,
    )

    facts_obj = merged.get("facts", {})
    # CRITICAL: Use effective_intent (set early after UNKNOWN override) for all operations.
    effective_intent_for_promotion = (
        merged.get("intent", {}).get("name", "")
        if isinstance(merged.get("intent"), dict)
        else ctx.luma_intent_name
    )
    promoted_slots_from_facts = (
        facts_to_slots(
            facts_obj,
            intent_name=effective_intent_for_promotion,
            source_text=merged.get("_source_text"),
            entity_schema=(
                merged.get("_entity_schema")
                if isinstance(merged.get("_entity_schema"), dict)
                else None
            ),
        )
        if isinstance(facts_obj, dict)
        else {}
    )

    # Extract slots from facts.facts.slots if present (nested format)
    # Otherwise fall back to legacy slots field
    nested_slots: Dict[str, Any] = {}
    if isinstance(facts_obj, dict) and "slots" in facts_obj:
        nested_slots = facts_obj.get("slots", {})
    else:
        nested_slots = merged.get("slots", {})

    # Merge promoted slots; strip date keys when Fix 4 applies (flexible + same-turn service)
    raw_luma_slots = merge_promoted_luma_slots(
        nested_slots,
        promoted_slots_from_facts,
        facts_obj if isinstance(facts_obj, dict) else None,
        temporal=merged.get("temporal") if isinstance(merged.get("temporal"), dict) else None,
    )

    # CRITICAL: Preserve raw service_id from raw Luma facts if present
    # If raw Luma facts have service_id, use that as the raw tenant value
    # Store normalized value (if any) as canonical
    raw_luma_response = merged.get("_raw_luma_response", {})
    if isinstance(raw_luma_response, dict):
        raw_luma_facts = raw_luma_response.get("facts", {})
        if isinstance(raw_luma_facts, dict) and "service_id" in raw_luma_facts:
            # Raw Luma facts contain service_id - use as raw tenant value
            raw_service_id_from_facts = raw_luma_facts["service_id"]
            raw_luma_slots["service_id"] = raw_service_id_from_facts

            # Check if nested_slots or promoted_slots has a normalized/canonical value
            # This happens when service_id is normalized before merge
            if (
                isinstance(nested_slots, dict)
                and "_canonical_service_id" in nested_slots
            ):
                # Canonical already computed - preserve it
                raw_luma_slots["_canonical_service_id"] = nested_slots[
                    "_canonical_service_id"
                ]
            elif isinstance(nested_slots, dict) and "service_id" in nested_slots:
                nested_service_id = nested_slots["service_id"]
                # If nested value differs from raw, it's likely normalized - store as canonical
                if nested_service_id != raw_service_id_from_facts:
                    raw_luma_slots["_canonical_service_id"] = nested_service_id
            # Also check promoted_slots_from_facts for canonical
            if "_canonical_service_id" not in raw_luma_slots and isinstance(
                promoted_slots_from_facts, dict
            ):
                if "_canonical_service_id" in promoted_slots_from_facts:
                    raw_luma_slots["_canonical_service_id"] = promoted_slots_from_facts[
                        "_canonical_service_id"
                    ]
                elif "service_id" in promoted_slots_from_facts:
                    promoted_service_id = promoted_slots_from_facts["service_id"]
                    if promoted_service_id != raw_service_id_from_facts:
                        raw_luma_slots["_canonical_service_id"] = promoted_service_id
    else:
        # No raw Luma response - check if we have normalized value that should be canonical
        if isinstance(nested_slots, dict) and "service_id" in nested_slots:
            nested_service_id = nested_slots["service_id"]
            # Check if this looks like a normalized value (contains dots, e.g., "beauty_and_wellness.haircut")
            if "." in str(nested_service_id):
                # Likely normalized - but we don't have raw, so use as both
                raw_luma_slots["service_id"] = nested_service_id
            elif (
                isinstance(promoted_slots_from_facts, dict)
                and "service_id" in promoted_slots_from_facts
            ):
                # Use promoted as raw, nested as canonical if different
                promoted_service_id = promoted_slots_from_facts["service_id"]
                if promoted_service_id != nested_service_id:
                    raw_luma_slots["service_id"] = promoted_service_id
                    raw_luma_slots["_canonical_service_id"] = nested_service_id
                else:
                    raw_luma_slots["service_id"] = nested_service_id

    if not isinstance(raw_luma_slots, dict):
        raw_luma_slots = {}

    # Store raw_luma_slots for turn outcome snapshot logging
    merged["_raw_luma_slots"] = raw_luma_slots.copy()

    # MERGE_RESULT: Log merged slots and their sources
    slot_sources: Dict[str, str] = {}
    for key in raw_luma_slots.keys():
        source = []
        if key in nested_slots:
            source.append("nested_slots")
        if key in promoted_slots_from_facts:
            source.append("promoted_from_facts")
        slot_sources[key] = "|".join(source) if source else "unknown"
    logger.info(
        "[MERGE_RESULT] user_id=%s merged_slots=%s slot_sources=%s",
        ctx.user_id,
        json.dumps(raw_luma_slots, default=str, ensure_ascii=True),
        json.dumps(slot_sources, ensure_ascii=True),
    )

    return raw_luma_slots


def _enforce_intent_authority(ctx: _MergeContext) -> None:
    """Preserve reconciled intent.name — merge does not re-resolve intent."""
    merged = ctx.merged
    if not isinstance(merged.get("intent"), dict):
        merged["intent"] = {}
    existing_intent_name = merged.get("intent", {}).get("name", "")
    if existing_intent_name:
        logger.debug(
            "merge_luma_with_session: preserved authoritative intent=%s",
            existing_intent_name,
        )
        return
    merged["intent"]["name"] = ctx.luma_intent_name or ""


def merge_luma_with_session(
    luma_response: Dict[str, Any],
    session_state: Dict[str, Any],
    *,
    apply_domain_filter: bool = True,
    turn_operation: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Merge Luma response with session state for follow-up handling.

    Merge rules (STRICT):
    1. Session intent is immutable - if Luma intent != session intent, session should be reset (handled in orchestrator)
    2. If luma.intent == UNKNOWN: use session.intent (don't modify session intent)
    3. Extract slots from Luma slots dict AND trace.semantic fields
    4. Start with session slots, merge new entities from Luma (do NOT overwrite existing session values)
    5. Update missing_slots after merge (must shrink on follow-up turns)

    IMPORTANT: This function assumes luma.intent == UNKNOWN or luma.intent == session.intent.
    Intent mismatch should be handled by resetting session BEFORE calling this function.

    Args:
        luma_response: Luma API response (may contain newly extracted entities even if intent=UNKNOWN)
        session_state: Session state from previous turn (status: "NEEDS_CLARIFICATION" or "READY")

    Returns:
        Modified Luma response with merged slots and session intent (ready for planning pipeline)
    """
    user_id = session_state.get(
        "user_id", "unknown") if session_state else "unknown"
    session_slots = session_state.get("slots", {}) if session_state else {}
    initial_session_slots = dict(session_slots) if isinstance(
        session_slots, dict) else {}
    session_missing_slots = (
        session_state.get("missing_slots", []) if session_state else []
    )
    logger.info(
        "[SESSION_BEFORE] user_id=%s slots=%s missing_slots=%s",
        user_id,
        json.dumps(session_slots, default=str, ensure_ascii=True),
        session_missing_slots,
    )

    # Create a copy to avoid mutating the original
    merged = luma_response.copy()

    # Preserve debugging fields (e.g., _raw_luma_response) - these must NOT be mutated or normalized
    # _raw_luma_response is attached by orchestrator for debugging and must be preserved through merge

    # STEP 1: Handle intent - Session intent is immutable unless session is reset
    # If luma.intent == UNKNOWN: use session.intent (don't modify session intent)
    # CRITICAL: Read from "intent_name" first (canonical persisted field), fallback to "intent" (legacy/compat)
    # This matches Core's persisted session schema where intent is stored as "intent_name"
    # Handle None explicitly (ephemeral intents are stored as None, not empty string)
    session_intent = session_state.get("intent_name")
    if session_intent is None:
        session_intent = session_state.get("intent")

    session_status = session_state.get("status", "")

    # Extract Luma intent
    luma_intent_obj = merged.get("intent", {})
    luma_intent_name = (
        luma_intent_obj.get("name", "") if isinstance(
            luma_intent_obj, dict) else ""
    )

    logger.debug(
        f"merge_luma_with_session: luma_intent={luma_intent_name} "
        f"session_intent={session_intent} session_status={session_status}"
    )

    # Rehydrate persisted confirmation authorization for multi-turn confirm flows.
    _rehydrate_confirmation_state(merged, session_state)

    # Extract session intent name for comparison (MUST be done before first use)
    # CRITICAL: Handle both string and dict formats, and handle None/empty strings correctly
    # None means ephemeral (no durable intent persisted)
    session_intent_name = None
    # Explicitly check for None (not just truthy)
    if session_intent is not None:
        if isinstance(session_intent, str):
            # String format: use directly if non-empty
            session_intent_name = session_intent if session_intent else None
        elif isinstance(session_intent, dict):
            # Dict format: extract "name" field
            session_intent_name = session_intent.get("name")
            # Only use if it's a non-empty string
            if not session_intent_name or not isinstance(session_intent_name, str):
                session_intent_name = None
    # If session_intent is None, session_intent_name remains None (ephemeral)

    # Build context bundle; used by all Phase-2 helpers and _finalize_effective_slots_and_trace.
    # merged is a mutable dict — ctx.merged and the local ``merged`` alias are the same object.
    ctx = _MergeContext(
        merged=merged,
        session_state=session_state,
        apply_domain_filter=apply_domain_filter,
        session_intent=session_intent,
        session_intent_name=session_intent_name,
        session_status=session_status,
        luma_intent_name=luma_intent_name,
        initial_session_slots=initial_session_slots,
        user_id=user_id,
        turn_operation=turn_operation,
    )

    # STEP 1.5 + STEP 1: Early slot check + intent authority enforcement
    _enforce_intent_authority(ctx)

    # STEP 2: Merge session facts with Luma facts (new facts override old)
    _merge_facts(merged, session_state)

    # STEP 3: Extract slots from Luma response (facts promotion + service_id reconciliation)
    raw_luma_slots = _extract_raw_luma_slots(ctx)

    # STEP 2.5: Carry Temporal from session when current turn has no temporal material
    _carry_forward_temporal(merged, session_state)

    # Keep luma_slots alias for backward compatibility with existing code
    luma_slots = raw_luma_slots

    # Extract date/time from entities, semantic trace, and booking object into luma_slots
    _extract_semantic_slots(ctx, luma_slots)

    # STEP 3: Additive slot merge — session + Luma → merged_slots; handle proposals and booking re-injection
    merged_slots, merged_intent_name = _merge_slots_additive(ctx, luma_slots)

    # STEP 3.6 + informational-turn early return + effective_intent resolution
    early_return, effective_intent, merged_slots = _handle_informational_turn_and_effective_intent(
        ctx, merged_slots, merged_intent_name, raw_luma_slots
    )
    if early_return:
        return _finalize_merged_luma_response(merged, luma_response)

    # STEP 4.1 + 4.1.5: Promote slots, bind time selection, apply domain filtering
    durable_slots_for_persist, datetime_bound_this_turn = _promote_and_bind(
        ctx, merged_slots, effective_intent
    )
    effective_slots_for_computation = durable_slots_for_persist.copy()
    _ = effective_slots_for_computation  # Prepared for planning; missing_slots owned by finalize_turn_state

    # Compute effective collected slots, assert intent invariant, emit merge trace
    _finalize_effective_slots_and_trace(ctx, effective_intent, durable_slots_for_persist)

    return _finalize_merged_luma_response(merged, luma_response)
