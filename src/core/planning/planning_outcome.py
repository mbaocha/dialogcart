"""Planning turn outcome assembly — public planning response contract.

Owns planning-only flattening, AWAITING_* envelopes, metadata propagation,
and rendering-text injection tied to planning outcomes.

TurnPlanner sequences the decision then calls ``build_planning_turn_outcome``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from core.engine.outcome_builder import build_outcome_from_decision
from core.rendering.response_renderer import _inject_rendering_text, _inject_system_text
from core.session.durable_intents import is_durable_intent

logger = logging.getLogger(__name__)


def build_planning_turn_outcome(
    *,
    decision: Dict[str, Any],
    plan: Dict[str, Any],
    plan_status: str,
    awaiting: Any,
    effective_response: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]],
    user_id: str,
    organization_id: Optional[int] = None,
    planning_only: bool = False,
) -> Dict[str, Any]:
    """Build the planning-stage result envelope from a planner decision."""

    # When planning_only=True, build the flattened planning outcome contract
    # (no execution; ConversationEngine owns the turn pipeline).
    # SINGLE SOURCE OF TRUTH: Construct flattened response directly from plan and facts
    if planning_only:
        intent_name = decision.get("intent_name", "")

        # Extract stage and action from plan (single source of truth)
        stage = plan.get("stage")
        action = plan.get("action")

        # FIND REAL SOURCES: Check ALL possible locations for missing_slots and slots
        # Priority order: decision.facts > decision top-level > effective_response > plan
        # NOTE: plan does NOT contain missing_slots/slots (only stage/action/status)
        missing_slots = None
        slots = None

        # 1. Check decision.facts first (primary source from process_luma_response)
        facts = decision.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}
        if facts.get("missing_slots") is not None:
            missing_slots = facts.get("missing_slots")
        if facts.get("slots") is not None:
            slots = facts.get("slots")

        # 2. Check decision top-level (may have data in some paths)
        if missing_slots is None and decision.get("missing_slots") is not None:
            missing_slots = decision.get("missing_slots")
        if slots is None and decision.get("slots") is not None:
            slots = decision.get("slots")

        # 3. Fallback to effective_response (source before process_luma_response)
        # This is guaranteed to have missing_slots (asserted earlier)
        if (
            missing_slots is None
            and effective_response.get("missing_slots") is not None
        ):
            missing_slots = effective_response.get("missing_slots")
        if slots is None and effective_response.get("slots") is not None:
            slots = effective_response.get("slots")

        # 4. Check plan (unlikely, but check for completeness)
        if missing_slots is None and plan.get("missing_slots") is not None:
            missing_slots = plan.get("missing_slots")
        if slots is None and plan.get("slots") is not None:
            slots = plan.get("slots")

        # 5. Final fallback: empty defaults (should never happen due to assertion)
        if missing_slots is None:
            missing_slots = []
        if slots is None:
            slots = {}

        # Ensure correct types
        if not isinstance(missing_slots, list):
            missing_slots = []
        if not isinstance(slots, dict):
            slots = {}

        # CRITICAL: For planning_only, use RAW Luma fact values (not normalized aliases)
        # Tests expect raw values like "massage", not normalized like "beauty_and_wellness.massage"
        # Normalized values remain internal-only for execution paths
        # This applies ONLY to planning_only responses - execution paths still use normalized values
        if effective_response and isinstance(effective_response, dict):
            raw_luma_response = effective_response.get(
                "_raw_luma_response", {})
            if isinstance(raw_luma_response, dict):
                raw_luma_facts = raw_luma_response.get("facts", {})
                if isinstance(raw_luma_facts, dict) and raw_luma_facts:
                    # Start with normalized slots (has time, date, etc. from normalization)
                    raw_slots = slots.copy() if isinstance(slots, dict) else {}

                    # Override service_id with raw fact value (tests expect raw, not normalized alias)
                    if "service_id" in raw_luma_facts:
                        raw_slots["service_id"] = raw_luma_facts["service_id"]
                    elif isinstance(effective_response.get("facts"), dict):
                        raw_facts_in_effective = effective_response.get(
                            "facts", {}
                        ).get("service_id")
                        if raw_facts_in_effective:
                            raw_slots["service_id"] = raw_facts_in_effective

                    slots = raw_slots

        # CRITICAL: Always extract stage and action from decision.plan (authoritative source)
        # This ensures plan.stage and plan.action are always present in the outcome
        # Do not rely on plan_message() or other sources - decision.plan is the single source of truth
        decision_plan = decision.get("plan", {}) if decision else {}
        if not isinstance(decision_plan, dict):
            decision_plan = {}

        # Extract stage and action from decision.plan (always use this source)
        stage = decision_plan.get("stage")
        action = decision_plan.get("action")

        # CRITICAL: Always populate plan object with all required fields
        # This ensures plan.stage and plan.action are always present (no silent failures)
        # HARD RULE: Include both intent and intent_name for session persistence
        populated_plan = {
            "intent": intent_name,
            # For session persistence - build_session_state_from_outcome reads plan.intent_name
            "intent_name": intent_name,
            "stage": stage,  # Always from decision.plan
            "action": action,  # Always from decision.plan
            "missing_slots": missing_slots,
            "slots": slots,
            "status": plan_status,
            "executable_actions": plan.get("executable_actions", []),
            "allowed_actions": plan.get("allowed_actions", []),
            "blocked_actions": plan.get("blocked_actions", []),
        }

        # CAPABILITY GATING: Override plan status if payment is required but not satisfied
        # This happens in post-planning finalization (right before outcome is built)
        # Check organization payment requirements and payment satisfaction status
        org_data = None
        # Check decision.facts.org first (if process_luma_response copied it)
        if decision and isinstance(decision.get("facts"), dict):
            org_data = decision["facts"].get("org")
        # Fall back to effective_response.facts.org (where org data is added before process_luma_response)
        if (
            not org_data
            and effective_response
            and isinstance(effective_response.get("facts"), dict)
        ):
            org_data = effective_response["facts"].get("org")

        payment_required = False
        if org_data and isinstance(org_data, dict):
            payment_required = org_data.get("payment_required", False)

        payment_satisfied = False
        # INSTRUMENTATION: Log all three data sources to identify which one is missing payment_satisfied
        decision_facts_payment_satisfied = None
        session_facts_payment_satisfied = None
        outcome_facts_payment_satisfied = None

        # Check decision.facts.payment_satisfied first
        if decision and isinstance(decision.get("facts"), dict):
            decision_facts_payment_satisfied = decision["facts"].get(
                "payment_satisfied"
            )
            payment_satisfied = (
                decision_facts_payment_satisfied
                if decision_facts_payment_satisfied
                else False
            )

        # Check session_state.facts.payment_satisfied
        if session_state and isinstance(session_state.get("facts"), dict):
            session_facts_payment_satisfied = session_state["facts"].get(
                "payment_satisfied"
            )

        # Check effective_response.facts.payment_satisfied (for test scenarios)
        if effective_response and isinstance(effective_response.get("facts"), dict):
            outcome_facts_payment_satisfied = effective_response["facts"].get(
                "payment_satisfied"
            )

        # CRITICAL FIX: Session facts are authoritative for reconciliation
        # When session_state exists, use session_state["facts"] as the primary source
        # This ensures capability completion facts (payment_satisfied) from previous turns are respected
        if session_state and isinstance(session_state.get("facts"), dict):
            session_payment_satisfied = session_state["facts"].get(
                "payment_satisfied")
            if session_payment_satisfied is not None:
                payment_satisfied = session_payment_satisfied
                logger.info(
                    f"[CAPABILITY_GATING] Using session_state.facts.payment_satisfied={payment_satisfied} "
                    f"(session facts are authoritative for reconciliation)"
                )
        elif not payment_satisfied:
            # Fall back to decision.facts if session_state doesn't have it
            if decision_facts_payment_satisfied is not None:
                payment_satisfied = decision_facts_payment_satisfied
            # Fall back to effective_response.facts if neither session nor decision have it
            elif outcome_facts_payment_satisfied is not None:
                payment_satisfied = outcome_facts_payment_satisfied

        # INSTRUMENTATION: Log all three sources for debugging
        logger.debug(
            f"[CAPABILITY_GATING_INSTRUMENTATION] payment_satisfied sources: "
            f"decision.facts={decision_facts_payment_satisfied}, "
            f"session_state.facts={session_facts_payment_satisfied}, "
            f"effective_response.facts={outcome_facts_payment_satisfied}, "
            f"FINAL={payment_satisfied}"
        )

        # Debug logging
        logger.debug(
            f"Capability gating check: org_data={org_data is not None}, "
            f"payment_required={payment_required}, payment_satisfied={payment_satisfied}, "
            f"plan_status={plan_status}"
        )

        # Override plan status if payment is required but not satisfied
        # This override applies even if planner returns READY
        if payment_required and not payment_satisfied:
            logger.info(
                f"Capability gating: payment_required=True, payment_satisfied=False. "
                f"Overriding plan.status from '{plan_status}' to 'AWAITING_CAPABILITY'"
            )
            plan_status = "AWAITING_CAPABILITY"
            populated_plan["status"] = "AWAITING_CAPABILITY"
            populated_plan["awaiting"] = "PAYMENT"
            # Lowercase adapter key
            populated_plan["active_capability"] = "payment"

            # Update decision.plan so that build_outcome_from_decision() uses the correct values
            if decision and isinstance(decision.get("plan"), dict):
                decision["plan"]["status"] = "AWAITING_CAPABILITY"
                decision["plan"]["awaiting"] = "PAYMENT"
                # Lowercase adapter key
                decision["plan"]["active_capability"] = "payment"
        elif payment_required and payment_satisfied:
            # CRITICAL: When payment is satisfied, clear active_capability to prevent re-entering payment capability
            # This ensures "paid" sessions do not re-enter AWAITING_CAPABILITY state
            logger.info(
                f"Capability gating: payment_required=True, payment_satisfied=True. "
                f"Clearing active_capability to prevent re-entry into payment capability"
            )
            # Clear active_capability from plan
            populated_plan["active_capability"] = None
            if "active_capability" in populated_plan:
                del populated_plan["active_capability"]

            # Update decision.plan to clear active_capability
            if decision and isinstance(decision.get("plan"), dict):
                decision["plan"]["active_capability"] = None
                if "active_capability" in decision["plan"]:
                    del decision["plan"]["active_capability"]

        # CRITICAL: Ensure outcome always uses raw service_id (not canonical)
        # Dialog output, outcome.slots, outcome.facts.slots MUST use raw tenant value
        from core.planning.temporal_proposal import (
            has_bound_booking_datetime,
            strip_unconfirmed_temporal_slots,
        )

        outcome_slots = strip_unconfirmed_temporal_slots(
            slots.copy() if isinstance(slots, dict) else {},
            intent_name,
            session_state,
            confirmed=has_bound_booking_datetime(
                slots, session_state, effective_response
            ),
        )
        slots = outcome_slots
        populated_plan["slots"] = outcome_slots

        intentionally_dropped_slots = set()
        if effective_response and isinstance(effective_response, dict):
            intentionally_dropped_slots = (
                effective_response.get("_intentionally_dropped_slots") or set()
            )
        service_candidates = None
        if session_state and isinstance(session_state, dict):
            service_candidates = session_state.get("service_candidates")

        skip_session_service_reinject = bool(
            "service_id" in intentionally_dropped_slots
            or (service_candidates and len(service_candidates) > 0)
        )

        # Get raw service_id from session or effective_response
        # Priority: 1) session.slots["service_id"], 2) effective_response slots, 3) current slots
        raw_service_id_for_outcome = None
        if not skip_session_service_reinject and session_state and isinstance(
            session_state, dict
        ):
            session_slots = session_state.get("slots", {})
            if isinstance(session_slots, dict) and "service_id" in session_slots:
                raw_service_id_for_outcome = session_slots["service_id"]

        if not raw_service_id_for_outcome and effective_response:
            effective_slots = effective_response.get("slots", {})
            if isinstance(effective_slots, dict) and "service_id" in effective_slots:
                raw_service_id_for_outcome = effective_slots["service_id"]

        if not raw_service_id_for_outcome and "service_id" in outcome_slots:
            raw_service_id_for_outcome = outcome_slots["service_id"]

        # Always use raw service_id in outcome
        # Priority: session raw > effective_response raw > current slots
        if raw_service_id_for_outcome:
            outcome_slots["service_id"] = raw_service_id_for_outcome
            logger.debug(
                f"Using raw service_id in outcome: {raw_service_id_for_outcome}"
            )
        elif "service_id" in outcome_slots:
            # Keep existing service_id if no raw found (shouldn't happen but fail-safe)
            logger.debug(
                f"Using existing service_id in outcome: {outcome_slots.get('service_id')}"
            )

        # Remove canonical from outcome slots (never expose canonical to tests/dialog)
        if "_canonical_service_id" in outcome_slots:
            del outcome_slots["_canonical_service_id"]
            logger.debug(
                f"Removed _canonical_service_id from outcome, using raw service_id: {outcome_slots.get('service_id')}"
            )

        # Construct flattened planning response with both structures:
        # 1. Flattened fields at outcome level (for test compatibility)
        # 2. Complete plan object (for observability and debugging)
        # 3. Facts object for backward compatibility (snapshot builder reads outcome.facts.*)
        # CRITICAL: Start from decision.facts to preserve capability facts (e.g., payment_satisfied)
        # Do NOT create a new facts dict - this would discard capability completion markers
        decision_facts = decision.get("facts", {}) if decision else {}
        if not isinstance(decision_facts, dict):
            decision_facts = {}

        # Build outcome facts: preserve all decision facts, overlay missing_slots and slots
        outcome_facts = {
            # Preserve capability facts (payment_satisfied, payment_reference, org, etc.)
            **decision_facts,
            "missing_slots": missing_slots,  # Overlay with computed missing_slots
            # Overlay with computed slots (use raw service_id only)
            "slots": outcome_slots,
        }

        result = {
            "success": True,
            "outcome": {
                "intent_name": intent_name,
                "stage": stage,
                "action": action,
                "missing_slots": missing_slots,
                "slots": outcome_slots,  # Use raw service_id only
                "status": plan_status,
                "plan": populated_plan,  # Always include complete plan object
                # Preserve decision facts (includes capability completion markers)
                "facts": outcome_facts,
            },
        }

        # Add active_capability if present in populated_plan (from capability gating)
        if populated_plan.get("active_capability"):
            result["outcome"]["active_capability"] = populated_plan["active_capability"]

        # Store effective Luma response for session building (for test snapshots)
        if effective_response:
            result["_merged_luma_response"] = effective_response

        # Store decision for plan_message / ConversationEngine early-return paths
        result["_decision"] = decision

        # DEBUG LOG: Finalization point (after all overrides, before PLAN_FINAL)
        # Track fingerprint-based availability resolution for debugging
        stored_fp = (
            session_state.get(
                "availability_fingerprint") if session_state else None
        )
        last_exec_result = (
            session_state.get(
                "last_execution_result") if session_state else None
        )
        from core.workflows.availability.fingerprint import (
            build_availability_fingerprint_slots,
            compute_availability_fingerprint,
        )

        fp_slots = (
            build_availability_fingerprint_slots(
                slots or {},
                intent_name=intent_name,
                organization_id=organization_id,
                luma_response=effective_response,
                session_state=session_state,
            )
            if slots
            else {}
        )
        current_fp = (
            compute_availability_fingerprint(fp_slots, intent_name=intent_name)
            if fp_slots
            else None
        )
        logger.debug(
            f"[FINALIZATION_DECISION] BEFORE PLAN_FINAL (after all overrides): "
            f"intent={intent_name}, "
            f"plan.status={populated_plan.get('status')}, plan.stage={populated_plan.get('stage')}, plan.action={populated_plan.get('action')}, "
            f"session.intent_name={session_state.get('intent_name') if session_state else None}, "
            f"session.status={session_state.get('status') if session_state else None}, "
            f"session.stage={session_state.get('stage') if session_state else None}, "
            f"session.action={session_state.get('action') if session_state else None}, "
            f"availability_fingerprint_stored={stored_fp}, "
            f"availability_fingerprint_current={current_fp}, "
            f"last_execution_result={last_exec_result}, "
            f"slots service_id={slots.get('service_id') if isinstance(slots, dict) else None}, "
            f"date={slots.get('date') if isinstance(slots, dict) else None}, "
            f"time={slots.get('time') if isinstance(slots, dict) else None}, "
            f"org_id={fp_slots.get('organization_id') if isinstance(fp_slots, dict) else None}"
        )

        # GUARD LOG: Final plan values before return
        logger.debug(
            "[PLAN_FINAL] stage=%s action=%s missing=%s slots=%s",
            populated_plan["stage"],
            populated_plan["action"],
            populated_plan["missing_slots"],
            populated_plan["slots"],
        )

        # Capability runner invocation lives in core.api.capability_boundary
        # (message.py API boundary). Planning only emits AWAITING_CAPABILITY.

        # ASSERTION: plan.intent_name must never be empty after successful planning
        if (
            not populated_plan.get("intent_name")
            or populated_plan.get("intent_name") == ""
        ):
            logger.error(
                "[PLANNING_ASSERTION] CRITICAL: plan.intent_name is empty after planning! "
                "intent_name=%r, plan=%s, outcome.intent_name=%s",
                intent_name,
                json.dumps(populated_plan, default=str, ensure_ascii=True),
                result.get("outcome", {}).get("intent_name"),
            )
            # Fail-safe: Use intent_name from variable if plan doesn't have it
            if intent_name and intent_name not in ("", "UNKNOWN"):
                populated_plan["intent_name"] = intent_name
                result["outcome"]["intent_name"] = intent_name
                result["outcome"]["plan"]["intent_name"] = intent_name
                logger.error(
                    "[PLANNING_ASSERTION] Recovered: Set plan.intent_name=%s from resolved intent_name",
                    intent_name,
                )
            else:
                logger.error(
                    "[PLANNING_ASSERTION] FAILED: Cannot recover - intent_name is empty/invalid: %r",
                    intent_name,
                )
        else:
            # Log success to confirm intent_name is present
            logger.debug(
                "[PLANNING_ASSERTION] SUCCESS: plan.intent_name=%s is present after planning",
                populated_plan.get("intent_name"),
            )

        # PLANNING INVARIANT: Ephemeral intents must NOT leak into planning
        plan_intent_name = populated_plan.get("intent_name")
        if plan_intent_name and plan_intent_name not in ("", "UNKNOWN"):
            if not is_durable_intent(plan_intent_name):
                raise AssertionError(
                    f"Ephemeral intent '{plan_intent_name}' leaked into planning. "
                    f"Only durable intents may be persisted. "
                    f"Add '{plan_intent_name}' to DURABLE_INTENTS if it should be persistent."
                )

        # OUTCOME: Log final outcome structure
        outcome = result.get("outcome", {})
        outcome_slots = outcome.get("slots", {})
        outcome_missing_slots = outcome.get("missing_slots", [])
        logger.info(
            "[OUTCOME] user_id=%s intent=%s stage=%s action=%s missing_slots=%s slots=%s",
            user_id,
            intent_name,
            outcome.get("stage"),
            outcome.get("action"),
            outcome_missing_slots,
            json.dumps(outcome_slots, default=str, ensure_ascii=True),
        )

        # Inject system rendering text (greeting/welcome) BEFORE clarification injection
        # Only if NOT NEEDS_CLARIFICATION, NOT AWAITING_CAPABILITY, NOT EXECUTED
        if plan_status not in (
            "NEEDS_CLARIFICATION",
            "AWAITING_CAPABILITY",
            "EXECUTED",
        ):
            _inject_system_text(result, decision)

        # Inject rendering text for clarification states (ONLY for NEEDS_CLARIFICATION)
        if plan_status == "NEEDS_CLARIFICATION":
            _inject_rendering_text(result, decision, session_state)

        # Do NOT return early for AWAITING_* statuses - let the handler below process them
        if plan_status not in ("AWAITING_CONFIRMATION", "AWAITING_CAPABILITY"):
            return result

    # Handle AWAITING_* statuses (AWAITING_CONFIRMATION, AWAITING_CAPABILITY, etc.)
    # Generic handler that mirrors plan status and awaiting without special-casing
    if plan_status in ("AWAITING_CONFIRMATION", "AWAITING_CAPABILITY"):
        # Build outcome from decision using unified helper
        outcome_dict = build_outcome_from_decision(decision)

        # Override status and awaiting to mirror plan (AWAITING_* statuses are special)
        outcome_dict["status"] = plan_status
        outcome_dict["awaiting"] = awaiting

        # Include _raw_luma_response in facts for test snapshots (preserved from effective_response)
        if effective_response and "_raw_luma_response" in effective_response:
            facts = outcome_dict.get("facts", {})
            if not isinstance(facts, dict):
                facts = {}
            facts["_raw_luma_response"] = effective_response["_raw_luma_response"]
            outcome_dict["facts"] = facts

        # Add booking for AWAITING_CONFIRMATION (backward compatibility)
        facts = outcome_dict.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}

        confirmation_text = None
        if plan_status == "AWAITING_CONFIRMATION":
            booking = decision.get("booking", {})
            outcome_dict["booking"] = booking
            from core.rendering.booking_confirmation_renderer import (
                prefix_with_revision_acknowledgement,
                render_booking_confirmation_prompt,
            )

            confirm_slots = outcome_dict.get("slots") or facts.get("slots", {})
            if isinstance(confirm_slots, dict):
                confirmation_text = render_booking_confirmation_prompt(
                    confirm_slots)
                revision_summary = None
                if isinstance(effective_response, dict):
                    revision_summary = effective_response.get(
                        "_revision_summary")
                confirmation_text = prefix_with_revision_acknowledgement(
                    confirmation_text, revision_summary
                )

        # Add active_capability for AWAITING_CAPABILITY
        # STRICTLY resolve from decision.plan.active_capability (authoritative source from capability gating)
        # Capability gating sets decision.plan.active_capability when entering AWAITING_CAPABILITY
        # Do NOT depend on session_state (may be None on first turn) or facts
        active_capability = None
        if plan_status == "AWAITING_CAPABILITY":
            # Capability gating ALWAYS injects decision.plan.active_capability when setting AWAITING_CAPABILITY
            # This is the single source of truth for active capability
            plan = decision.get("plan", {})
            if isinstance(plan, dict):
                active_capability = plan.get("active_capability")
            if active_capability:
                outcome_dict["active_capability"] = active_capability

        result = {"success": True, "outcome": outcome_dict}
        if confirmation_text:
            result["text"] = confirmation_text
        # Store effective Luma response for session building
        result["_merged_luma_response"] = effective_response

        # Store decision for plan_message / ConversationEngine
        result["_decision"] = decision

        # AWAITING_* statuses must never render clarification text at top-level
        # Capability/awaiting UI owns the text for these statuses
        return result

    # This should never be reached - all statuses handled above
    logger.error(f"Unexpected plan_status: {plan_status} for user {user_id}")
    return {
        "success": False,
        "error": "internal_error",
        "message": f"Unexpected plan status: {plan_status}",
    }
