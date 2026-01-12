"""
Service for resolving conversational input and resolving intent/state.

This module contains the core resolution logic extracted from api.py.

INVARIANT: This file must NEVER:
- Infer intent (domain, classifier output, or any other source must not be used to guess intent)
- Inject services (do not add services by scanning raw text or using heuristics)
- Invent dates/times (do not create temporal values that weren't produced by pipeline stages)
- Reinterpret raw text (do not parse or analyze user input directly)

This file's responsibilities are LIMITED to:
- Orchestrating pipeline stages (extraction, intent, structure, grouping, semantic, decision, binder)
- Processing each request independently (Luma is stateless)
- Enforcing decision/binder guardrails (validating completeness, temporal shapes, etc.)

If logic would violate this invariant, replace the behavior with:
- Logging (diagnostic information for debugging)
- Clarification (let the decision layer handle missing information)

This ensures semantic integrity and prevents cascading hacks that corrupt the resolution pipeline.
"""
from luma.response.builder import ResponseBuilder, format_service_for_response, build_issues
from luma.config.core import STATUS_READY, STATUS_NEEDS_CLARIFICATION, STATUS_RESOLVED, STATUS_PARTIAL
from luma.config import config
from luma.trace import log_field_removal
from luma.trace.stage_snapshot import capture_stage_snapshot
from luma.trace import validate_stable_fields
from luma.config.intent_meta import get_intent_registry
from luma.config.temporal import APPOINTMENT_TEMPORAL_TYPE, RESERVATION_TEMPORAL_TYPE, DateMode, TimeMode
from luma.perf import StageTimer
from luma.resolution.semantic_resolver import SemanticResolutionResult, _is_weekday_only_range
from luma.utils.missing_slots import compute_temporal_shape_missing_slots, compute_missing_slots_for_intent
from luma.decision import decide_booking_status
from luma.clarification.reasons import ClarificationReason
from luma.pipeline import LumaPipeline
from luma.calendar.calendar_binder import bind_calendar, bind_times, combine_datetime_range, get_timezone, get_booking_policy, CalendarBindingResult, _bind_single_date, _localize_datetime
import time
import logging
import re
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone as dt_timezone

from flask import jsonify
from luma.config.conversation_signals import (
    get_confirmation_terms,
    get_confirmation_phrases,
    is_confirmation_enabled
)


def is_confirmation(text: str) -> bool:
    """
    Check if text is a confirmation response.

    Uses configuration from conversation_signals.yaml to determine
    if the input text matches confirmation terms or phrases.

    Args:
        text: User input text

    Returns:
        True if text is a confirmation, False otherwise
    """
    if not is_confirmation_enabled():
        return False

    t = text.lower().strip()

    # Check exact matches
    confirmation_terms = get_confirmation_terms()
    if t in confirmation_terms:
        return True

    # Check if text starts with "confirm" (legacy behavior)
    if t.startswith("confirm"):
        return True

    # Check phrase matches (substring)
    confirmation_phrases = get_confirmation_phrases()
    for phrase in confirmation_phrases:
        if phrase in t:
            return True

    return False


logger = logging.getLogger(__name__)


# CONTEXTUAL_UPDATE constant removed - no longer used in state-first model


def _normalize_service_canonical_to_display(canonical: str) -> str:
    """
    Convert canonical service ID to display name.

    Examples:
    - "beauty_and_wellness.beard_grooming" → "beard grooming"
    - "hospitality.suite" → "suite"
    - "hospitality.room" → "room"

    Args:
        canonical: Service canonical ID in format "category.service_name"

    Returns:
        Display name with underscores replaced by spaces and category prefix removed
    """
    if not canonical or "." not in canonical:
        return canonical

    # Split category.service_name
    parts = canonical.split(".", 1)
    if len(parts) == 2:
        service_name = parts[1]
        # Replace underscores with spaces
        display_name = service_name.replace("_", " ")
        return display_name

    return canonical


# QUARANTINED: _convert_time_ref_to_24h removed - violates invariant (parses raw text)
# This function was used to derive time_constraint from time_refs, which violates
# the invariant against inventing dates/times. Time constraint derivation is now
# handled by the decision layer through clarification.


def is_booking_intent(intent: str) -> bool:
    """
    Check if intent is a booking intent (CREATE_APPOINTMENT or CREATE_RESERVATION).

    Args:
        intent: Intent string to check

    Returns:
        True if intent is CREATE_APPOINTMENT or CREATE_RESERVATION, False otherwise
    """
    return intent in {"CREATE_APPOINTMENT", "CREATE_RESERVATION"}


def build_datetime_range_for_api(
    slots: Dict[str, Any],
    semantic_booking: Dict[str, Any],
    domain: str,
    request_id: Optional[str] = None,
    user_id: Optional[str] = None
) -> None:
    """
    Build datetime_range structure for MODIFY_BOOKING API response compatibility.

    This is a compatibility shim that enforces API response shape requirements:
    - If has_datetime=True, datetime_range must exist in the response
    - For time-only or date-time changes in appointment modifications, constructs
      a minimal datetime_range structure with identical start/end values

    CRITICAL: This function does NOT perform temporal resolution or inference.
    It only shapes the API response structure based on what semantic_booking already
    contains. It MUST never invent new time values or resolve temporal ambiguity.

    Args:
        slots: Response slots dict (modified in place)
        semantic_booking: Semantic resolver output containing date_mode, time_mode, time_refs, etc.
        domain: Domain context ("service" for appointments, "reservation" for reservations)
        request_id: Optional request ID for logging
        user_id: Optional user ID for logging

    Side Effects:
        - Sets slots["has_datetime"] = True if time or date is present
        - Sets slots["datetime_range"] with minimal structure if has_datetime=True but datetime_range missing
    """
    # Only apply to appointment modifications (service domain)
    semantic_booking_mode = semantic_booking.get("booking_mode", domain)
    is_appointment_modify = (semantic_booking_mode == "service" or domain == "service")
    
    if not is_appointment_modify:
        return
    
    # Skip if datetime_range already exists
    if slots.get("datetime_range"):
        return
    
    # Check if date is present (date_mode != "none" and date_refs exist)
    has_date = (semantic_booking.get("date_mode") is not None and
               semantic_booking.get("date_mode") != "none" and
               semantic_booking.get("date_refs"))
    
    # Check if time is present (time_mode != "none" and time_refs exist, or time_constraint exists)
    has_time = ((semantic_booking.get("time_mode") is not None and
                semantic_booking.get("time_mode") != "none" and
                semantic_booking.get("time_refs")) or
               semantic_booking.get("time_constraint"))
    
    # Set has_datetime if already set, or if time/date is present
    if slots.get("has_datetime") or has_time or has_date:
        # Set has_datetime if not already set
        if not slots.get("has_datetime"):
            slots["has_datetime"] = True
            logger.info(
                f"[slots] MODIFY_BOOKING appointment: set has_datetime=True (time or date present, "
                f"has_time={has_time}, has_date={has_date}, "
                f"booking_mode={semantic_booking_mode}, "
                f"date_mode={semantic_booking.get('date_mode')}, time_mode={semantic_booking.get('time_mode')})",
                extra={'request_id': request_id, 'user_id': user_id}
            )
        
        # Build minimal datetime_range when has_datetime is True but datetime_range is missing
        # This is required by the API contract: if has_datetime=True, datetime_range must exist
        time_refs = semantic_booking.get("time_refs", [])
        if time_refs:
            # Create a minimal datetime_range with time reference (date will be resolved later if needed)
            # Start and end are identical - this is a compatibility structure, not temporal resolution
            slots["datetime_range"] = {
                "start": time_refs[0] if time_refs else None,
                "end": time_refs[0] if time_refs else None
            }
        else:
            # Fallback: create empty datetime_range structure (will be populated if date/time are bound)
            slots["datetime_range"] = {
                "start": None,
                "end": None
            }
        logger.info(
            f"[slots] MODIFY_BOOKING appointment: built minimal datetime_range for has_datetime=True "
            f"(time_refs={time_refs})",
            extra={'request_id': request_id, 'user_id': user_id}
        )


def resolve_message(
    # Flask request globals
    g,
    request,

    # Module globals
    intent_resolver,
    logger,

    # Constants
    APPOINTMENT_TEMPORAL_TYPE_CONST,

    # Helper functions
    _localize_datetime,
    find_normalization_dir,
    _get_business_categories,
    _count_mutable_slots_modified,
    _has_booking_verb,
    validate_required_slots,
    plan_clarification,
    _log_stage,
):
    """
    Process conversational input and resolve intent/state.

    This is the extracted body of the /resolve handler from api.py.
    All dependencies are passed as parameters.
    """
    request_id = g.request_id if hasattr(g, 'request_id') else 'unknown'
    booking_payload: Optional[Dict[str, Any]] = None
    calendar_booking: Dict[str, Any] = {}

    if intent_resolver is None:
        logger.error("Pipeline not initialized",
                     extra={'request_id': request_id})
        return jsonify({
            "success": False,
            "error": "Pipeline not initialized"
        }), 503

    # Parse request
    try:
        data = request.get_json()
        if not data:
            logger.warning("Missing request body", extra={
                           'request_id': request_id})
            return jsonify({
                "success": False,
                "error": "Missing request body"
            }), 400

        # Require user_id
        if "user_id" not in data:
            logger.warning("Missing 'user_id' parameter",
                           extra={'request_id': request_id})
            return jsonify({
                "success": False,
                "error": "Missing 'user_id' parameter in request body"
            }), 400

        user_id = data["user_id"]
        if not user_id or not isinstance(user_id, str):
            logger.warning("Invalid user_id parameter",
                           extra={'request_id': request_id})
            return jsonify({
                "success": False,
                "error": "'user_id' must be a non-empty string"
            }), 400

        if "text" not in data:
            logger.warning("Missing 'text' parameter",
                           extra={'request_id': request_id})
            return jsonify({
                "success": False,
                "error": "Missing 'text' parameter in request body"
            }), 400

        text = data["text"]
        domain = data.get("domain", "service")
        timezone = data.get("timezone", "UTC")
        # Optional tenant context with aliases
        tenant_context = data.get("tenant_context")

        # Log tenant_context for debugging
        if tenant_context:
            aliases_count = len(tenant_context.get("aliases", {})) if isinstance(
                tenant_context.get("aliases"), dict) else 0
            aliases = tenant_context.get("aliases", {}) if isinstance(
                tenant_context, dict) else {}
            booking_mode = tenant_context.get("booking_mode") if isinstance(
                tenant_context, dict) else None
            logger.info(
                f"Received tenant_context with {aliases_count} aliases",
                extra={
                    'request_id': request_id,
                    'aliases_count': aliases_count,
                    'aliases': aliases,
                    'booking_mode': booking_mode
                }
            )

        if not text or not isinstance(text, str):
            logger.warning("Invalid text parameter", extra={
                           'request_id': request_id})
            return jsonify({
                "success": False,
                "error": "'text' must be a non-empty string"
            }), 400

        # Luma is now stateless - no memory storage or recall
        memory_state = None
        # Initialize execution_trace
        execution_trace = {"timings": {}}

    except Exception as e:  # noqa: BLE001
        logger.error(
            f"Invalid request format: {str(e)}",
            extra={'request_id': request_id},
            exc_info=True
        )
        return jsonify({
            "success": False,
            "error": f"Invalid request format: {str(e)}"
        }), 400

    # Process conversational input
    try:
        start_time = time.perf_counter()

        # Find normalization directory
        normalization_dir = find_normalization_dir()
        if not normalization_dir:
            return jsonify({
                "success": False,
                "error": "Normalization directory not found"
            }), 500

        entity_file = str(normalization_dir / "101.v1.json")

        # Initialize now datetime
        now = datetime.now()
        now = _localize_datetime(now, timezone)

        results = {
            "input": {
                "sentence": text,
                "domain": domain,
                "timezone": timezone,
                "now": now.isoformat()
            },
            "stages": {}
        }

        # Execute pipeline to get execution_trace
        try:
            pipeline = LumaPipeline(
                domain=domain, entity_file=entity_file, intent_resolver=intent_resolver)
            booking_mode_for_pipeline = "service"
            if tenant_context and isinstance(tenant_context, dict):
                booking_mode_for_pipeline = tenant_context.get(
                    "booking_mode", "service") or "service"

            # Determine debug mode for pipeline contract validation
            debug_flag = str(request.args.get("debug", "0")).lower()
            pipeline_debug_mode = debug_flag in {"1", "true", "yes"}

            # Initialize execution_trace with timings dict for stage-level timing
            execution_trace = {"timings": {}}

            pipeline_results = pipeline.run(
                text=text,
                now=now,
                timezone=timezone,
                tenant_context=tenant_context,
                booking_mode=booking_mode_for_pipeline,
                request_id=request_id,
                debug_mode=pipeline_debug_mode
            )

            # Extract stage results and execution_trace from pipeline
            extraction_result = pipeline_results["stages"]["extraction"]
            intent_resp = pipeline_results["stages"]["intent"]
            structure_dict = pipeline_results["stages"]["structure"]
            grouped_result = pipeline_results["stages"]["grouping"]
            semantic_result = pipeline_results["stages"]["semantic"]
            # Merge pipeline's execution_trace into our trace (preserves timings)
            pipeline_trace = pipeline_results["execution_trace"]
            execution_trace.update(pipeline_trace)

            # Capture stage snapshots from pipeline results
            # Initialize stage_snapshots list if not present
            if "stage_snapshots" not in execution_trace:
                execution_trace["stage_snapshots"] = []

            # Capture extraction snapshot (input: text, output: extraction_result)
            extraction_snapshot = capture_stage_snapshot(
                stage_name="extraction",
                input_data={"text": text, "domain": domain},
                output_data=extraction_result
            )
            execution_trace["stage_snapshots"].append(extraction_snapshot)

            # Capture grouping snapshot (input: extraction_result + structure, output: grouped_result)
            grouping_input = {
                "extraction_result": extraction_result,
                "structure": structure_dict
            }
            grouping_snapshot = capture_stage_snapshot(
                stage_name="grouping",
                input_data=grouping_input,
                output_data=grouped_result
            )
            execution_trace["stage_snapshots"].append(grouping_snapshot)

            # Capture semantic snapshot (input: grouped_result, output: semantic_result.resolved_booking)
            semantic_snapshot = capture_stage_snapshot(
                stage_name="semantic",
                input_data={"grouped_result": grouped_result,
                            "extraction_result": extraction_result},
                output_data=semantic_result.resolved_booking if semantic_result else {}
            )
            execution_trace["stage_snapshots"].append(semantic_snapshot)

            # Store stage results
            results["stages"]["extraction"] = extraction_result
            results["stages"]["intent"] = intent_resp
            results["stages"]["structure"] = structure_dict
            results["stages"]["grouping"] = grouped_result
            results["stages"]["semantic"] = semantic_result.to_dict()

            # Expose backward-compatible fields
            classifier_intent = intent_resp["intent"]
            confidence = intent_resp["confidence"]

            # Luma is stateless - all requests are independent (no follow-ups)
            # Use classifier intent directly
            intent = classifier_intent

            # Store intent in results (real intent, no normalization)
            # Intent resolver returns CREATE_APPOINTMENT or CREATE_RESERVATION directly
            results["stages"]["intent"]["external_intent"] = intent if is_booking_intent(
                intent) else None

            # Luma is stateless - all requests are independent
            results["stages"]["intent"]["stateless"] = {
                "classifier_intent": classifier_intent,
                "effective_intent": intent
            }

        except Exception as e:
            # Fallback to individual stage execution on pipeline error
            logger.error(f"Pipeline execution failed: {e}", extra={
                         'request_id': request_id}, exc_info=True)
            results["stages"]["extraction"] = {"error": str(e)}
            return jsonify({"success": False, "data": results}), 500

        # INVARIANT: Do not parse or reinterpret raw text
        # If services are missing, let the decision layer handle clarification
        # This prevents silent corruption of semantic results
        # Note: We no longer scan text for reservation nouns - that violates the invariant
        service_missing = False
        if (semantic_result and
            (not semantic_result.resolved_booking.get("services") or
             len(semantic_result.resolved_booking.get("services", [])) == 0)):
            service_missing = True
            logger.debug(
                f"Service missing in semantic result for user {user_id}",
                extra={
                    'request_id': request_id,
                    'domain': domain,
                    'note': 'Decision layer will handle clarification'
                }
            )

        # Store trace flag for diagnostic purposes
        if "semantic" not in execution_trace:
            execution_trace["semantic"] = {}
        execution_trace["semantic"]["service_missing"] = service_missing

        # Luma is stateless - use semantic result directly (no memory merging)
        merged_semantic_result = semantic_result

        # Extract intent_name early to check for UNKNOWN
        intent_name_early = intent if isinstance(intent, str) else (intent.get("name") if isinstance(intent, dict) else None)
        is_unknown_intent = (intent_name_early == "UNKNOWN")

        # Decision / Policy Layer - ACTIVE
        # Decision layer determines if clarification is needed BEFORE calendar binding
        # Policy operates ONLY on semantic roles, never on raw text or regex
        # Luma is stateless - decision sees only the current semantic result (no merging)
        # EXCEPTION: UNKNOWN intents skip decision layer (pure extraction, no validation)
        decision_result = None
        # Initialize semantic_for_decision before try block to ensure it's always defined
        semantic_for_decision = merged_semantic_result.resolved_booking if merged_semantic_result else {}

        # Skip decision layer for UNKNOWN intents (pure extraction, no validation)
        if not is_unknown_intent:
            try:
                # Load booking policy from config
                booking_policy = get_booking_policy()

                # Attach booking_mode for decision policy (service vs reservation)
                if isinstance(semantic_for_decision, dict):
                    semantic_for_decision["booking_mode"] = domain

                # Get intent_name for temporal shape validation
                # Use intent directly (already CREATE_APPOINTMENT or CREATE_RESERVATION)
                intent_name_for_decision = intent

                # Time decision re-run (with merged semantic result)
                # Decision layer now handles tenant-authoritative service resolution internally
                with StageTimer(execution_trace, "decision", request_id=request_id):
                    decision_result, decision_trace = decide_booking_status(
                        semantic_for_decision,
                        entities=extraction_result,
                        policy=booking_policy,
                        intent_name=intent_name_for_decision,
                        tenant_context=tenant_context
                    )

                # Extract resolved tenant_service_id from decision trace
                # (Service resolution is now handled within decision layer)
                service_resolution_info = decision_trace.get(
                    "decision", {}).get("service_resolution", {})
                resolved_tenant_service_id = service_resolution_info.get(
                    "resolved_tenant_service_id")
                service_resolution_reason = service_resolution_info.get(
                    "clarification_reason")
                service_resolution_metadata = service_resolution_info.get(
                    "metadata", {})

                if resolved_tenant_service_id:
                    logger.info(
                        f"[decision] Service resolved to tenant_service_id: '{resolved_tenant_service_id}'"
                    )
                elif service_resolution_reason:
                    logger.info(
                        f"[decision] Service resolution failed: {service_resolution_reason}"
                    )

                # Store decision result in results
                results["stages"]["decision"] = {
                    "status": decision_result.status,
                    "reason": decision_result.reason,
                    "effective_time": decision_result.effective_time,
                    "resolved_tenant_service_id": resolved_tenant_service_id
                }
                # Update execution_trace with decision trace (overwrites pipeline's trace with merged semantic result)
                execution_trace.update(decision_trace)

                # Capture decision snapshot
                decision_input = {
                    "semantic_booking": semantic_for_decision,
                    "intent_name": intent_name_for_decision
                }
                decision_output = {
                    "status": decision_result.status,
                    "reason": decision_result.reason,
                    "effective_time": decision_result.effective_time
                }
                decision_snapshot = capture_stage_snapshot(
                    stage_name="decision",
                    input_data=decision_input,
                    output_data=decision_output,
                    decision_flags={
                        "temporal_shape_satisfied": decision_trace.get("decision", {}).get("temporal_shape_satisfied"),
                        "missing_slots": decision_trace.get("decision", {}).get("missing_slots", [])
                    }
                )
                if "stage_snapshots" not in execution_trace:
                    execution_trace["stage_snapshots"] = []
                execution_trace["stage_snapshots"].append(decision_snapshot)

                # Fail fast guardrail: If temporal_shape == datetime_range and missing slots, ensure binder is skipped
                expected_shape = decision_trace.get(
                    "decision", {}).get("expected_temporal_shape")
                if expected_shape == APPOINTMENT_TEMPORAL_TYPE_CONST:
                    missing = decision_trace.get(
                        "decision", {}).get("missing_slots", [])
                    if missing and decision_result.status == "RESOLVED":
                        # This is an invariant violation - should not happen
                        # Force NEEDS_CLARIFICATION
                        decision_result.status = "NEEDS_CLARIFICATION"
                        decision_result.reason = "temporal_shape_not_satisfied"
                        execution_trace["decision"]["state"] = "NEEDS_CLARIFICATION"
                        execution_trace["decision"]["reason"] = "temporal_shape_not_satisfied"
                        execution_trace["decision"]["temporal_shape_satisfied"] = False
                        execution_trace["decision"]["rule_enforced"] = "temporal_shape_guardrail"
                        execution_trace["decision"]["missing_slots"] = missing

                # Decision is RESOLVED - proceed to calendar binding unconditionally
                # Calendar binding will assume inputs are already approved

            except Exception as e:  # noqa: BLE001
                # Decision layer failure should not block - log and continue
                logger.error(
                    f"[DECISION] Decision layer failed: {e}",
                    extra={'request_id': request_id},
                    exc_info=True
                )
                results["stages"]["decision"] = {"error": str(e)}
                # Continue to calendar binding on error (fallback behavior)
        else:
            # UNKNOWN intent: Skip decision layer, force RESOLVED status
            # Store empty decision result for UNKNOWN
            results["stages"]["decision"] = {
                "status": "RESOLVED",
                "reason": None,
                "effective_time": None,
                "resolved_tenant_service_id": None
            }
            execution_trace["decision"] = {
                "state": "RESOLVED",
                "reason": None,
                "temporal_shape_satisfied": None,
                "missing_slots": []
            }

        # Luma is stateless - effective intent is just the intent we determined
        effective_intent = intent

        # Stage 6: Required slots validation (before calendar binding)
        # UNKNOWN intents skip required slots validation (pure extraction, no validation)
        intent_name_for_slots_raw = intent or effective_intent
        intent_name_for_slots = intent_name_for_slots_raw.get("name") if isinstance(
            intent_name_for_slots_raw, dict) else intent_name_for_slots_raw
        missing_required = []
        if intent_name_for_slots and not is_unknown_intent:
            resolved_booking_for_validation = merged_semantic_result.resolved_booking if merged_semantic_result else {}
            missing_required = validate_required_slots(
                intent_name_for_slots,
                resolved_booking_for_validation,
                extraction_result or {}
            )
        skip_prebind = (
            intent_name_for_slots == "CREATE_APPOINTMENT"
            and missing_required == ["time"]
        )
        # Extract calendar_result from pipeline_results (pipeline already called bind_calendar)
        # This is the authoritative source - resolve_service should use it instead of calling bind_calendar again
        # UNKNOWN intents: Skip calendar binding (intentionally skipped per design)
        # Slots will be built directly from semantic output using normalization functions
        if is_unknown_intent:
            # Create empty calendar_result for UNKNOWN (calendar binding is intentionally skipped)
            calendar_result = CalendarBindingResult(
                calendar_booking={},
                needs_clarification=False,
                clarification=None,
                _binding_success=False,
                _binding_error="skipped_for_unknown_intent"
            )
            results["stages"]["calendar"] = calendar_result.to_dict()
        else:
            pipeline_calendar_result = pipeline_results.get(
                "stages", {}).get("calendar")
            # Use pipeline's calendar_result as the base (it already has the binder output)
            calendar_result = pipeline_calendar_result if pipeline_calendar_result else None
        if not is_unknown_intent and missing_required and not skip_prebind:
            results["stages"]["intent"]["status"] = STATUS_NEEDS_CLARIFICATION
            results["stages"]["intent"]["missing_slots"] = missing_required
            # Only create empty calendar_result if pipeline didn't provide one
            if not calendar_result:
                calendar_result = CalendarBindingResult(
                    calendar_booking={},
                    needs_clarification=False,
                    clarification=None,
                    _binding_success=False,
                    _binding_error="skipped_due_to_missing_required_slots"
                )
            results["stages"]["calendar"] = calendar_result.to_dict()
        else:
            # Stage 6: Calendar Binding
            # MANDATORY: Calendar binding only runs when decision_state == RESOLVED
            # EXCEPTION: Also allow binding when date is present but time is missing
            # (to provide bound date in clarification context)
            # The decision layer enforces temporal shape completeness, so RESOLVED
            # guarantees that temporal shape requirements are satisfied
            has_date = (merged_semantic_result and
                        merged_semantic_result.resolved_booking.get("date_refs"))
            # Get missing slots from decision trace if available (added at line 1384)
            decision_missing_slots = execution_trace.get("decision", {}).get(
                "missing_slots", []) if execution_trace else []
            missing_only_time = (decision_result and
                                 decision_result.status == "NEEDS_CLARIFICATION" and
                                 decision_result.reason == "temporal_shape_not_satisfied" and
                                 len(decision_missing_slots) == 1 and
                                 decision_missing_slots == ["time"] and
                                 has_date)

            # UNKNOWN intents skip calendar binding (pure extraction, no temporal binding)
            if not is_unknown_intent and decision_result and (decision_result.status == "RESOLVED" or missing_only_time):
                # Proceed with calendar binding
                # Use effective_intent for calendar binding
                # Luma is stateless - use current semantic result directly
                binding_intent = effective_intent
                # Get external_intent for reservation handling (CREATE_RESERVATION vs CREATE_APPOINTMENT)
                external_intent = results["stages"]["intent"].get(
                    "external_intent")
                try:
                    # BINDER layer: Structured DEBUG log (before binding)
                    # Legacy log - downgraded to DEBUG as part of logging refactor
                    reason_str = 'decision=RESOLVED' if decision_result.status == "RESOLVED" else 'decision=NEEDS_CLARIFICATION (date-only binding)'
                    logger.debug(
                        "BINDER_GATE",
                        extra={
                            'request_id': request_id,
                            'run': True,
                            'reason': reason_str
                        }
                    )
                    # Time calendar binding re-run (with merged semantic result)
                    with StageTimer(execution_trace, "binder", request_id=request_id):
                        calendar_result, binder_trace = bind_calendar(
                            merged_semantic_result,
                            now,
                            timezone,
                            intent=binding_intent,
                            entities=extraction_result,
                            external_intent=external_intent
                        )
                        results["stages"]["calendar"] = calendar_result.to_dict()
                    # Update execution_trace with binder trace (overwrites pipeline's trace with merged semantic result)
                    execution_trace.update(binder_trace)

                    # Guardrail: READY requires binder output
                    # If decision is READY but binder didn't produce required bound field, downgrade to NEEDS_CLARIFICATION
                    if decision_result and decision_result.status == "RESOLVED":
                        calendar_booking = calendar_result.calendar_booking if calendar_result else {}
                        required_bound_field_present = False

                        if external_intent == "CREATE_APPOINTMENT":
                            # Early escape: If semantic result has date + time, don't require calendar binding
                            # Handle date-time combination
                            semantic_booking = merged_semantic_result.resolved_booking if merged_semantic_result else {}
                            date_refs = semantic_booking.get("date_refs", [])
                            time_mode = semantic_booking.get("time_mode", "none")
                            time_refs = semantic_booking.get("time_refs", [])
                            time_constraint = semantic_booking.get("time_constraint")
                            
                            # Check if date is present
                            has_date = len(date_refs) > 0
                            
                            # Check if time is present (time_mode with refs, or time_constraint)
                            has_time = False
                            if time_constraint is not None:
                                tc_mode = time_constraint.get("mode")
                                if tc_mode in {TimeMode.EXACT.value, TimeMode.WINDOW.value, TimeMode.FUZZY.value}:
                                    has_time = True
                            elif time_mode in {TimeMode.EXACT.value, TimeMode.RANGE.value, TimeMode.WINDOW.value}:
                                if len(time_refs) > 0:
                                    has_time = True
                            
                            # If both date and time are present semantically, don't require calendar binding
                            # The calendar binding might fail due to weekday-only ranges or other issues,
                            # but the semantic slots are sufficient for appointment creation
                            if has_date and has_time:
                                required_bound_field_present = True
                            else:
                                # Appointments require datetime_range from calendar binding
                                required_bound_field_present = bool(
                                    calendar_booking.get("datetime_range"))
                        elif external_intent == "CREATE_RESERVATION":
                            # Reservations require date_range OR (start_date AND end_date)
                            required_bound_field_present = bool(
                                calendar_booking.get("date_range") or
                                (calendar_booking.get("start_date")
                                 and calendar_booking.get("end_date"))
                            )
                        else:
                            # For other intents, assume binding is optional
                            required_bound_field_present = True

                        if not required_bound_field_present:
                            # Downgrade to NEEDS_CLARIFICATION
                            decision_result.status = "NEEDS_CLARIFICATION"
                            decision_result.reason = ClarificationReason.INCOMPLETE_BINDING.value
                            # Update execution trace to reflect downgrade
                            if "decision" in execution_trace:
                                execution_trace["decision"]["state"] = "NEEDS_CLARIFICATION"
                                execution_trace["decision"]["reason"] = ClarificationReason.INCOMPLETE_BINDING.value
                            logger.warning(
                                f"Guardrail: Downgraded READY to NEEDS_CLARIFICATION due to missing binder output",
                                extra={
                                    'request_id': request_id,
                                    'external_intent': external_intent,
                                    'clarification_reason': ClarificationReason.INCOMPLETE_BINDING.value
                                }
                            )

                    # Capture binder snapshot
                    binder_input = {
                        "semantic_result": merged_semantic_result.resolved_booking if merged_semantic_result else {},
                        "intent": binding_intent,
                        "external_intent": external_intent,
                        "timezone": timezone
                    }
                    binder_output = calendar_result.to_dict() if calendar_result else {}
                    binder_snapshot = capture_stage_snapshot(
                        stage_name="binder",
                        input_data=binder_input,
                        output_data=binder_output,
                        decision_flags={
                            "called": True,
                            "needs_clarification": calendar_result.needs_clarification if calendar_result else False
                        }
                    )
                    if "stage_snapshots" not in execution_trace:
                        execution_trace["stage_snapshots"] = []
                    execution_trace["stage_snapshots"].append(binder_snapshot)
                except Exception as e:
                    results["stages"]["calendar"] = {"error": str(e)}
                    # Build binder input for error trace
                    semantic_for_binder = merged_semantic_result.resolved_booking if merged_semantic_result else semantic_result.resolved_booking
                    # Get temporal shape from IntentRegistry (sole policy source)
                    registry = get_intent_registry()
                    intent_meta = registry.get(
                        external_intent) if external_intent else None
                    temporal_shape_for_trace = intent_meta.temporal_shape if intent_meta else None
                    execution_trace["binder"] = {
                        "called": False,
                        "input": {
                            "intent": binding_intent,
                            "external_intent": external_intent,
                            "temporal_shape": temporal_shape_for_trace,
                            "date_mode": semantic_for_binder.get("date_mode", "none"),
                            "date_refs": semantic_for_binder.get("date_refs", []),
                            "time_mode": semantic_for_binder.get("time_mode", "none"),
                            "time_refs": semantic_for_binder.get("time_refs", []),
                            "time_constraint": semantic_for_binder.get("time_constraint"),
                            "timezone": timezone
                        },
                        "output": {},
                        "decision_reason": f"exception: {str(e)}"
                    }
                    # Create empty calendar_result for consistency (even though we return early)
                    calendar_result = CalendarBindingResult(
                        calendar_booking={},
                        needs_clarification=False,
                        clarification=None,
                        _binding_success=False,
                        _binding_error=f"exception: {str(e)}"
                    )
                    return jsonify({"success": False, "data": results}), 500
            else:
                # decision_state != RESOLVED - skip calendar binding
                # Temporal shape incomplete or other clarification needed
                reason = f"decision={decision_result.status if decision_result else 'NONE'}"
                calendar_result = CalendarBindingResult(
                    calendar_booking={},
                    needs_clarification=False,
                    clarification=None,
                    _binding_success=False,
                    _binding_error=f"skipped_due_to_decision_state: {reason}"
                )
                results["stages"]["calendar"] = calendar_result.to_dict()
                # Binder was skipped - add trace with input even though not called
                semantic_for_binder = merged_semantic_result.resolved_booking if merged_semantic_result else semantic_result.resolved_booking
                external_intent_for_trace = results["stages"]["intent"].get(
                    "external_intent") or intent
                # Get temporal shape from IntentRegistry (sole policy source)
                registry = get_intent_registry()
                intent_meta = registry.get(
                    external_intent_for_trace) if external_intent_for_trace else None
                temporal_shape_for_trace = intent_meta.temporal_shape if intent_meta else None
                execution_trace["binder"] = {
                    "called": False,
                    "input": {
                        "intent": intent,
                        "external_intent": external_intent_for_trace,
                        "temporal_shape": temporal_shape_for_trace,
                        "date_mode": semantic_for_binder.get("date_mode", "none"),
                        "date_refs": semantic_for_binder.get("date_refs", []),
                        "time_mode": semantic_for_binder.get("time_mode", "none"),
                        "time_refs": semantic_for_binder.get("time_refs", []),
                        "time_constraint": semantic_for_binder.get("time_constraint"),
                        "timezone": timezone
                    },
                    "output": {},
                    "decision_reason": reason
                }

        # Determine debug mode (query param debug=1|true|yes)
        debug_flag = str(request.args.get("debug", "0")).lower()
        debug_mode = debug_flag in {"1", "true", "yes"}

        # Extract current booking state from calendar result
        calendar_dict = calendar_result.to_dict()
        calendar_booking = calendar_dict.get(
            "calendar_booking", {}) if calendar_dict else {}
        cal_clar_dict = calendar_dict.get(
            "clarification") if calendar_dict else None
        cal_needs_clarification = bool(calendar_dict.get(
            "needs_clarification")) if calendar_dict else False

        # Clarification planning (YAML-driven + semantic/calendar)
        intent_resp = results["stages"]["intent"]
        intent_name = intent_resp.get("name") if isinstance(
            intent_resp, dict) else intent_resp or intent
        
        # Determine if this is MODIFY_BOOKING (for special handling of semantic clarifications)
        is_modify_booking = intent_name == "MODIFY_BOOKING"

        # Extract decision trace for plan_clarification (contains missing_slots from temporal shape validation)
        decision_trace_for_plan = execution_trace.get(
            "decision", {}) if execution_trace else {}

        # UNKNOWN intents skip clarification planning (pure extraction, no validation)
        if is_unknown_intent:
            # Force status = READY and needs_clarification = False for UNKNOWN
            clar = {
                "status": STATUS_READY,
                "missing_slots": [],
                "clarification_reason": None
            }
            needs_clarification = False
            missing_slots = []
            clarification_reason = None
        else:
            clar = plan_clarification(
                intent_resp, extraction_result, merged_semantic_result, decision_result, decision_trace_for_plan)

            # If calendar needs clarification and none set yet, use calendar clarification
            if cal_needs_clarification and clar.get("status") != STATUS_NEEDS_CLARIFICATION:
                clar["status"] = STATUS_NEEDS_CLARIFICATION
                # Extract reason from calendar clarification
                cal_reason = cal_clar_dict.get("reason") if isinstance(
                    cal_clar_dict, dict) else None
                if cal_reason:
                    if isinstance(cal_reason, str):
                        clar["clarification_reason"] = cal_reason
                    elif hasattr(cal_reason, "value"):
                        clar["clarification_reason"] = cal_reason.value

            needs_clarification = clar.get("status") == STATUS_NEEDS_CLARIFICATION
            missing_slots = clar.get("missing_slots", [])
            clarification_reason = clar.get("clarification_reason")

        # For MODIFY_BOOKING, preserve semantic clarification as authoritative
        semantic_clarification_present = False
        semantic_missing_slots = []
        semantic_clarification_reason = None
        
        if is_modify_booking and merged_semantic_result:
            # Check if semantic resolver set a clarification (e.g., MISSING_DATE when time present but no date)
            if merged_semantic_result.needs_clarification and merged_semantic_result.clarification:
                semantic_clarification_present = True
                semantic_clarification_obj = merged_semantic_result.clarification
                # Extract missing_slots from semantic clarification data
                if hasattr(semantic_clarification_obj, 'data') and isinstance(semantic_clarification_obj.data, dict):
                    semantic_missing_slots = semantic_clarification_obj.data.get("missing_slots", [])
                # Extract clarification_reason from semantic clarification
                if hasattr(semantic_clarification_obj, 'reason'):
                    reason = semantic_clarification_obj.reason
                    if isinstance(reason, str):
                        semantic_clarification_reason = reason
                    elif hasattr(reason, 'value'):
                        semantic_clarification_reason = reason.value

        # Check if semantic resolver already detected MISSING_DATE_RANGE (weekday-only range)
        # If so, preserve it and ensure both dates are marked missing
        normalized_weekday_range = False
        if clarification_reason == ClarificationReason.MISSING_DATE_RANGE.value:
            # Semantic resolver already detected weekday-only range
            # Ensure both dates are marked missing (should already be set, but verify)
            if "start_date" not in missing_slots:
                missing_slots.append("start_date")
            if "end_date" not in missing_slots:
                missing_slots.append("end_date")
            # Remove duplicates and sort for consistency
            missing_slots = sorted(list(set(missing_slots)))
            normalized_weekday_range = True
            
            # Lock normalized missing slots in decision_result to prevent downstream overrides
            if decision_result:
                decision_result.missing_slots = ["start_date", "end_date"]
                decision_result.reason = ClarificationReason.MISSING_DATE_RANGE.value
                decision_result._normalized = True
            
            # Update execution trace to reflect normalized missing slots
            if execution_trace and "decision" in execution_trace:
                execution_trace["decision"]["missing_slots"] = ["start_date", "end_date"]
                execution_trace["decision"]["reason"] = ClarificationReason.MISSING_DATE_RANGE.value
                execution_trace["decision"]["_normalized"] = True

        # Guardrail: If decision was downgraded to INCOMPLETE_BINDING, check if it's a weekday-only range
        # Only set INCOMPLETE_BINDING if we haven't already detected MISSING_DATE_RANGE
        if (not normalized_weekday_range and decision_result and 
            decision_result.reason == ClarificationReason.INCOMPLETE_BINDING.value):
            # Check if this is actually a weekday-only range that should be MISSING_DATE_RANGE
            if intent_name == "CREATE_RESERVATION":
                # Try to get dates from extraction_result first, then fallback to semantic result
                date_texts = []
                if extraction_result:
                    dates = extraction_result.get("dates", [])
                    if len(dates) >= 2:
                        date_texts = [d.get("text", "").strip().lower() for d in dates[:2] if d.get("text")]
                
                # Fallback: try to get dates from semantic result's date_refs if available
                if not date_texts and merged_semantic_result and merged_semantic_result.resolved_booking:
                    date_refs = merged_semantic_result.resolved_booking.get("date_refs", [])
                    if len(date_refs) >= 2:
                        date_texts = [str(ref).strip().lower() for ref in date_refs[:2] if ref]
                
                if len(date_texts) == 2:
                    # Simple check: if both dates are common weekday names, treat as weekday-only range
                    common_weekdays = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                                       "mon", "tue", "tues", "wed", "thu", "thurs", "fri", "sat", "sun"}
                    is_simple_weekday_pair = all(text in common_weekdays for text in date_texts)
                    
                    # Check if this is an unanchored weekday-only range (more robust check)
                    # Use extraction_result if available, otherwise pass empty dict
                    entities_for_check = extraction_result if extraction_result else {}
                    is_weekday_range = (is_simple_weekday_pair or 
                                       _is_weekday_only_range(date_texts, DateMode.RANGE.value, entities_for_check, None))
                    
                    if is_weekday_range:
                        # Normalize: treat as fully unresolved, mark both dates as missing
                        missing_slots = ["start_date", "end_date"]
                        clarification_reason = ClarificationReason.MISSING_DATE_RANGE.value
                        normalized_weekday_range = True
                        needs_clarification = True
                        
                        # Lock normalized missing slots in decision_result to prevent downstream overrides
                        if decision_result:
                            decision_result.missing_slots = ["start_date", "end_date"]
                            decision_result.reason = ClarificationReason.MISSING_DATE_RANGE.value
                            decision_result._normalized = True
                        
                        # Update execution trace to reflect normalized missing slots
                        if execution_trace and "decision" in execution_trace:
                            execution_trace["decision"]["missing_slots"] = ["start_date", "end_date"]
                            execution_trace["decision"]["reason"] = ClarificationReason.MISSING_DATE_RANGE.value
                            execution_trace["decision"]["_normalized"] = True
                    else:
                        # Not a weekday-only range, so INCOMPLETE_BINDING is appropriate
                        clarification_reason = ClarificationReason.INCOMPLETE_BINDING.value
                        needs_clarification = True
                else:
                    # Not enough dates to check, use INCOMPLETE_BINDING as-is
                    clarification_reason = ClarificationReason.INCOMPLETE_BINDING.value
                    needs_clarification = True
            else:
                # Not a reservation, use INCOMPLETE_BINDING as-is
                clarification_reason = ClarificationReason.INCOMPLETE_BINDING.value
                needs_clarification = True

        # If missing_slots not set in clarification, use decision trace as fallback
        # Guard: Do not override normalized missing slots for unanchored weekday ranges
        # EXCEPTION: For MODIFY_BOOKING, decision/semantic layers set missing_slots via plan_clarification
        # Do not read from execution_trace procedurally - plan_clarification is authoritative
        if not missing_slots and execution_trace and not normalized_weekday_range and not is_modify_booking:
            decision_trace = execution_trace.get("decision", {})
            if decision_trace and isinstance(decision_trace, dict):
                decision_missing = decision_trace.get("missing_slots", [])
                if decision_missing:
                    missing_slots = decision_missing

        # Override clarification based on decision_result for time constraints
        # Accept exact/window time constraints for appointments; fuzzy requires clarification
        tc = semantic_for_decision.get(
            "time_constraint") if semantic_for_decision else None
        tc_mode = tc.get("mode") if isinstance(tc, dict) else None

        # Enforce required slots from intent metadata (authoritative)
        resolved_snapshot: Dict[str, Any] = {}
        # Prefer booking_payload for ready/partial responses
        if booking_payload:
            resolved_snapshot.update(booking_payload)
        # Overlay calendar booking fields if present
        if calendar_booking:
            resolved_snapshot.update(calendar_booking)
        # Overlay semantic resolved booking as fallback
        if merged_semantic_result and merged_semantic_result.resolved_booking:
            resolved_snapshot.setdefault(
                "date_refs", merged_semantic_result.resolved_booking.get("date_refs"))
            resolved_snapshot.setdefault(
                "time_refs", merged_semantic_result.resolved_booking.get("time_refs"))
            resolved_snapshot.setdefault(
                "services", merged_semantic_result.resolved_booking.get("services"))

        if intent_name:
            # POLICY: If decision.state == RESOLVED, skip slot validation entirely
            # EXCEPTION: MODIFY_BOOKING requires "at least one delta" check even if decision is RESOLVED
            # This is because MODIFY_BOOKING uses delta semantics (booking_id + at least one delta)
            should_skip_validation = (
                decision_result and 
                decision_result.status == "RESOLVED" and
                intent_name != "MODIFY_BOOKING"
            )
            
            if should_skip_validation:
                # Decision already resolved - skip slot validation (for non-MODIFY_BOOKING intents)
                # Don't re-run validation, don't add issues, don't downgrade status
                pass
            else:
                # Decision is not RESOLVED OR it's MODIFY_BOOKING - run slot validation
                # INVARIANT: For MODIFY_BOOKING, if missing_slots is already populated before compute_missing_slots_for_intent,
                # it must NEVER be recomputed or overridden. Decision + semantic layers (via plan_clarification) are authoritative.
                # Case 84: "modify booking FGH890" with booking_mode="service" → decision layer returns ["date", "time"]
                # Case 85: "reschedule reservation IJK123" with booking_mode="reservation" → decision layer returns ["start_date", "end_date"]
                # Case 99: "reschedule my booking ABC123" → decision layer returns ["change"], should use that instead of ["date", "time"]
                
                # Guard: Skip compute_missing_slots_for_intent if MODIFY_BOOKING already has missing_slots
                if is_modify_booking and missing_slots:
                    # Decision/semantic layers already set missing_slots via plan_clarification - do not override
                        enforced_missing = None
                else:
                    # Use centralized missing slot computation with special-case filtering
                    enforced_missing = compute_missing_slots_for_intent(
                        intent_name=intent_name,
                        resolved_slots=resolved_snapshot,
                        entities=extraction_result or {},
                        extraction_result=extraction_result,
                        merged_semantic_result=merged_semantic_result
                    )

                    if enforced_missing:
                        # Guard: Do not override normalized missing slots for unanchored weekday ranges
                        if not normalized_weekday_range:
                            needs_clarification = True
                            
                            # For MODIFY_BOOKING: merge semantic missing_slots with enforced_missing, never drop semantic ones
                            if is_modify_booking and semantic_clarification_present and semantic_missing_slots:
                                # Merge: keep all semantic missing_slots, add any additional from enforced_missing
                                missing_slots = list(set(semantic_missing_slots + enforced_missing))
                            else:
                                # For other intents or when no semantic clarification: use enforced_missing
                                missing_slots = enforced_missing
                            
                            # Update clarification_reason based on missing slots (only when enforced_missing was used)
                            # For MODIFY_BOOKING: preserve semantic clarification_reason if present, otherwise infer
                            # INVARIANT: For CREATE_APPOINTMENT, never set MISSING_SERVICE if services were extracted
                            if not clarification_reason:
                                if is_modify_booking and semantic_clarification_reason:
                                    # Preserve semantic clarification_reason for MODIFY_BOOKING
                                    clarification_reason = semantic_clarification_reason
                                elif "time" in missing_slots:
                                    clarification_reason = ClarificationReason.MISSING_TIME.value
                                elif "date" in missing_slots:
                                    clarification_reason = ClarificationReason.MISSING_DATE.value
                                elif "service_id" in missing_slots or "service" in missing_slots:
                                    # Only set MISSING_SERVICE if it's not CREATE_APPOINTMENT with extracted services
                                    if not (intent_name == "CREATE_APPOINTMENT" and extracted_services):
                                        clarification_reason = ClarificationReason.MISSING_SERVICE.value
                booking_payload = None
        # Temporal shape enforcement (authoritative, post-binding)
        # POLICY: If decision.state == RESOLVED, skip temporal enforcement to avoid downgrading status
        # Get temporal shape from IntentRegistry (sole policy source)
        registry = get_intent_registry()
        intent_meta = registry.get(intent_name) if intent_name else None
        has_temporal_shape = intent_meta and intent_meta.temporal_shape is not None

        if (intent_name and has_temporal_shape and
                not (decision_result and decision_result.status == "RESOLVED")):
            shape = intent_meta.temporal_shape
            # Use centralized temporal shape missing slot computation
            temporal_missing = compute_temporal_shape_missing_slots(
                intent_name=intent_name,
                calendar_booking=calendar_booking or {},
                merged_semantic_result=merged_semantic_result,
                temporal_shape=shape
            )
            if temporal_missing:
                        # Guard: Do not override normalized missing slots for unanchored weekday ranges
                        # Guard: Do not override MODIFY_BOOKING missing_slots if already set (decision/semantic layers are authoritative)
                        if not normalized_weekday_range and not (is_modify_booking and missing_slots):
                            needs_clarification = True
                            missing_slots = temporal_missing
                            # Update clarification_reason based on missing slots
                            if not clarification_reason:
                                if "time" in temporal_missing:
                                    clarification_reason = ClarificationReason.MISSING_TIME.value
                                elif "date" in temporal_missing:
                                    clarification_reason = ClarificationReason.MISSING_DATE.value
                                elif len(temporal_missing) >= 2:
                                    # Default to time if both missing
                                    clarification_reason = ClarificationReason.MISSING_TIME.value
                        booking_payload = None
                        response_body_status = STATUS_NEEDS_CLARIFICATION

        # FINAL OVERRIDE: Enforce decision layer as authoritative
        # If decision_result.status == "RESOLVED", override any validation that may have set needs_clarification
        # EXCEPTION: MODIFY_BOOKING requires "at least one delta" validation - don't override if validation found missing deltas
        # This must run AFTER all validation (plan_clarification, slot validation, temporal enforcement)
        # but BEFORE building the final response body
        # Guard: Do not override normalized missing slots for unanchored weekday ranges
        # Guard: Don't override if MODIFY_BOOKING validation found missing deltas ("delta" in missing_slots)
        should_override_decision = (
            decision_result and 
            decision_result.status == "RESOLVED" and 
            not normalized_weekday_range and
            intent_name != "MODIFY_BOOKING"  # MODIFY_BOOKING delta validation is authoritative
        )
        
        if should_override_decision:
            needs_clarification = False
            missing_slots = []
            clarification_reason = None
            # Clear any issues that might have been set by validation
            # Decision layer is authoritative - if it says RESOLVED, the request is ready

        # Extract current clarification
        # Priority: semantic clarification > calendar binding clarification > decision layer
        # CRITICAL: Semantic clarifications (e.g., MULTIPLE_MATCHES) must be preserved
        # even if decision layer says RESOLVED (due to invariant override)
        current_clarification = None

        # First priority: Check semantic resolution clarification (e.g., MULTIPLE_MATCHES ambiguity)
        # NOTE: For MODIFY_BOOKING, decision layer is authoritative for readiness (not semantic clarifications)
        # Semantic resolver no longer sets clarifications for time-only or date-only MODIFY_BOOKING
        if merged_semantic_result and merged_semantic_result.needs_clarification and merged_semantic_result.clarification:
            # Semantic resolution detected clarification (e.g., service variant ambiguity)
            semantic_clar = merged_semantic_result.clarification.to_dict()
            semantic_clar_reason = semantic_clar.get("reason")
            
            # For MODIFY_BOOKING: decision layer is authoritative - only preserve if decision also says NEEDS_CLARIFICATION
            # This ensures decision layer readiness rules are not overridden by semantic clarifications
            if is_modify_booking:
                # MODIFY_BOOKING: Only preserve semantic clarification if decision layer also says NEEDS_CLARIFICATION
                # Decision layer is authoritative for MODIFY_BOOKING readiness (reservation date_range, time-only, etc.)
                if decision_result and decision_result.status == "NEEDS_CLARIFICATION":
                    # Both decision and semantic say NEEDS_CLARIFICATION - preserve semantic clarification
                    current_clarification = semantic_clar
                    needs_clarification = True
                    semantic_clar_data = semantic_clar.get("data", {})
                    semantic_missing_slots = semantic_clar_data.get("missing_slots", [])
                    if semantic_missing_slots:
                        # Merge semantic missing_slots with decision missing_slots (if any)
                        decision_missing_slots = decision_result.missing_slots if decision_result and decision_result.missing_slots else []
                        missing_slots = list(set(semantic_missing_slots + decision_missing_slots))
                        clarification_reason = semantic_clar_reason
                    logger.info(
                        f"Preserving semantic clarification for MODIFY_BOOKING (decision also says NEEDS_CLARIFICATION): {semantic_clar_reason}",
                        extra={'request_id': request_id,
                               'clarification_reason': semantic_clar_reason,
                               'intent': intent_name,
                               'missing_slots': semantic_missing_slots}
                    )
                # If decision says RESOLVED, ignore semantic clarification (decision layer is authoritative)
                elif decision_result and decision_result.status == "RESOLVED":
                    logger.info(
                        f"Ignoring semantic clarification for MODIFY_BOOKING (decision says RESOLVED): {semantic_clar_reason}",
                        extra={'request_id': request_id,
                               'clarification_reason': semantic_clar_reason,
                               'intent': intent_name}
                    )
            elif decision_result and decision_result.status != "RESOLVED":
                # For other intents: only preserve if decision is not RESOLVED
                # This maintains existing behavior for CREATE intents
                current_clarification = semantic_clar
                logger.info(
                    f"Preserving semantic clarification: {semantic_clar_reason}",
                    extra={'request_id': request_id,
                           'clarification_reason': semantic_clar_reason}
                )
        elif (decision_result and decision_result.status == "NEEDS_CLARIFICATION" and
              decision_result.reason == ClarificationReason.MULTIPLE_MATCHES.value and
              service_resolution_metadata):
            # Decision layer returned MULTIPLE_MATCHES (e.g., from service resolution)
            # Create clarification with options from metadata
            options = service_resolution_metadata.get(
                "options") or service_resolution_metadata.get("family_tenant_services") or []
            if options:
                current_clarification = {
                    "reason": ClarificationReason.MULTIPLE_MATCHES.value,
                    "data": {
                        "options": options
                    }
                }
                logger.info(
                    f"Creating decision-layer clarification: MULTIPLE_MATCHES with {len(options)} options",
                    extra={'request_id': request_id,
                           'clarification_reason': ClarificationReason.MULTIPLE_MATCHES.value}
                )
        elif decision_result and decision_result.status == "RESOLVED":
            # Decision is RESOLVED - no clarification needed
            # EXCEPTION: For MODIFY_BOOKING, semantic clarifications are authoritative and were already handled above
            # Clear any existing PARTIAL clarification for non-MODIFY_BOOKING intents
            # (MODIFY_BOOKING semantic clarifications were preserved in the first if block)
            if not is_modify_booking:
                current_clarification = None
        elif calendar_result.needs_clarification and calendar_result.clarification:
            # Only validation errors from calendar binding (range conflicts, etc.)
            current_clarification = calendar_result.clarification.to_dict()

        # For MODIFY_BOOKING: Decision layer is authoritative for readiness
        # If decision says RESOLVED, use decision status (even if semantic set clarification)
        # If decision says NEEDS_CLARIFICATION, preserve semantic clarification if present
        if is_modify_booking:
            if decision_result and decision_result.status == "RESOLVED":
                # Decision layer says RESOLVED - override any semantic clarifications
                needs_clarification = False
                current_clarification = None
                missing_slots = []
                clarification_reason = None
                logger.info(
                    f"[MODIFY_BOOKING] Decision layer says RESOLVED - overriding semantic clarifications",
                    extra={'request_id': request_id, 'intent': intent_name}
                )
            elif decision_result and decision_result.status == "NEEDS_CLARIFICATION" and current_clarification:
                # Decision says NEEDS_CLARIFICATION - preserve semantic clarification if present
                clar_missing_slots = current_clarification.get("data", {}).get("missing_slots", [])
                clar_reason = current_clarification.get("reason")
                if clar_missing_slots:
                    # Merge semantic missing_slots with decision missing_slots
                    decision_missing_slots = decision_result.missing_slots if decision_result and decision_result.missing_slots else []
                    missing_slots = list(set(missing_slots + clar_missing_slots + decision_missing_slots))
                    logger.info(
                        f"[MODIFY_BOOKING] Preserving semantic missing_slots: {clar_missing_slots}, merged: {missing_slots}",
                        extra={'request_id': request_id, 'intent': intent_name}
                    )
                if clar_reason and not clarification_reason:
                    clarification_reason = clar_reason
                    logger.info(
                        f"[MODIFY_BOOKING] Preserving semantic clarification_reason: {clar_reason}",
                        extra={'request_id': request_id, 'intent': intent_name}
                    )

        # Prepare current booking state (only canonical fields)
        # Include date_range and time_range for merge logic to handle time-only updates
        # Format services to preserve resolved_alias if present
        calendar_services = calendar_booking.get("services", [])
        formatted_services = [
            format_service_for_response(service)
            for service in calendar_services
            if isinstance(service, dict)
        ] if calendar_services else []

        current_booking = {
            "services": formatted_services,
            "datetime_range": calendar_booking.get("datetime_range"),
            "date_range": calendar_booking.get("date_range"),
            "time_range": calendar_booking.get("time_range"),
            "duration": calendar_booking.get("duration")
        }

        # Luma is stateless - no memory persistence or follow-up merging
        # All requests are independent

        # Post-semantic validation guard: Check for orphan slot updates
        # If extracted slots exist but cannot be applied (no booking_id, no draft, no booking),
        # return clarification instead of "successful" empty response
        # Build production response
        # Always expose real intent in API responses (CREATE_APPOINTMENT or CREATE_RESERVATION)
        api_intent = effective_intent
        # Get intent from results (already real intent: CREATE_APPOINTMENT or CREATE_RESERVATION)
        external_intent_for_response = results["stages"]["intent"].get(
            "external_intent")

        # Use real intent directly (no normalization needed)
        intent_payload_name = external_intent_for_response if external_intent_for_response in {
            "CREATE_APPOINTMENT", "CREATE_RESERVATION"} else api_intent
        intent_payload = {"name": intent_payload_name,
                          "confidence": confidence}

        # Clarification fields from plan_clarification / calendar
        # Return booking state for booking intents or MODIFY_BOOKING
        # CRITICAL: For booking intents, booking must NEVER be null, even when clarification is needed
        booking_payload = None
        context_payload = None

        # Determine if this is a CREATE booking intent (MODIFY_BOOKING and CANCEL_BOOKING do NOT produce booking_payload)
        # UNKNOWN intents never produce booking_payload (pure extraction, no booking logic)
        is_booking_intent_flag = is_booking_intent(effective_intent)
        # HARD INVARIANT: MODIFY_BOOKING and CANCEL_BOOKING never produce booking_payload
        # UNKNOWN intents never produce booking_payload
        is_creates_booking = (is_booking_intent_flag and 
                             effective_intent not in {"MODIFY_BOOKING", "CANCEL_BOOKING", "UNKNOWN"})

        if not is_unknown_intent and is_creates_booking and not needs_clarification:
            # Use current_booking directly (stateless - no memory merging)
            booking_payload = current_booking.copy() if current_booking else {}
            # Add booking_state = "RESOLVED" for resolved bookings
            if booking_payload:
                booking_payload["booking_state"] = "RESOLVED"
                # Format services to preserve resolved_alias if present in current semantic result
                if merged_semantic_result:
                    current_services = merged_semantic_result.resolved_booking.get(
                        "services", [])
                    if current_services:
                        # Use services from current semantic result (which may have resolved_alias)
                        booking_payload["services"] = [
                            format_service_for_response(service)
                            for service in current_services
                            if isinstance(service, dict)
                        ]
            # INVARIANT: Do not resurrect missing booking state by rebuilding it silently
            # If booking_payload is missing or incomplete, log and let decision layer handle clarification
            if not booking_payload or (not booking_payload.get("services") and not booking_payload.get("datetime_range") and
                                       not booking_payload.get("start_date") and not booking_payload.get("end_date")):
                logger.warning(
                    f"Booking payload missing or incomplete for user {user_id} (invariant violation - not rebuilding)",
                    extra={
                        'request_id': request_id,
                        'intent': api_intent,
                        'has_calendar_booking': bool(calendar_booking),
                        'note': 'Decision layer should have handled this - forcing clarification'
                    }
                )
                # Force clarification instead of silently rebuilding
                needs_clarification = True
                booking_payload = None
        elif is_creates_booking and needs_clarification:
            # For booking intents that need clarification, return lightweight context only
            resolved_booking = merged_semantic_result.resolved_booking
            services = resolved_booking.get("services", [])
            if not services:
                service_families = _get_business_categories(extraction_result)
                services = [
                    format_service_for_response(service)
                    for service in service_families
                    if isinstance(service, dict) and service.get("text")
                ]
            else:
                services = [
                    format_service_for_response(service)
                    for service in services
                    if isinstance(service, dict)
                ]
            date_refs = resolved_booking.get("date_refs") or []
            time_refs = resolved_booking.get("time_refs", [])
            date_roles = resolved_booking.get("date_roles", [])

            # Debug: Log what we're getting from resolved_booking
            logger.info(
                f"[context] Building context: date_refs={date_refs}, date_roles={date_roles}, "
                f"resolved_booking_keys={list(resolved_booking.keys())}",
                extra={'request_id': request_id, 'user_id': user_id}
            )

            context_payload = {}
            if services:
                context_payload["services"] = services
            if date_refs:
                # Semantic reference (what user said)
                context_payload["start_date_ref"] = date_refs[0]
                if len(date_refs) >= 2:
                    context_payload["end_date_ref"] = date_refs[1]

                # Include date_roles if available (for debugging and client logic)
                # Always include date_roles array (even if empty) for consistency
                context_payload["date_roles"] = date_roles

                # Bound/processed date (ISO format) from calendar binding
                # Extract from calendar_booking if available
                bound_start_date = None
                bound_end_date = None
                if calendar_booking:
                    # Try date_range first (for reservations or date-only)
                    if calendar_booking.get("date_range"):
                        bound_start_date = calendar_booking["date_range"].get(
                            "start_date")
                        bound_end_date = calendar_booking["date_range"].get(
                            "end_date")
                    # Fallback to datetime_range (extract date part)
                    elif calendar_booking.get("datetime_range"):
                        dt_start = calendar_booking["datetime_range"].get(
                            "start", "")
                        dt_end = calendar_booking["datetime_range"].get(
                            "end", "")
                        if dt_start:
                            bound_start_date = dt_start.split("T")[0]
                        if dt_end:
                            bound_end_date = dt_end.split("T")[0]
                    # Also check direct start_date/end_date fields (for reservations)
                    if not bound_start_date and calendar_booking.get("start_date"):
                        bound_start_date = calendar_booking.get("start_date")
                    if not bound_end_date and calendar_booking.get("end_date"):
                        bound_end_date = calendar_booking.get("end_date")

                # FIX: For date-only turns (CREATE_APPOINTMENT with date but no time),
                # always resolve the date immediately and persist it in context
                # This ensures context never carries only symbolic temporal values
                # Date-only ≠ fully resolved appointment, but resolved dates must always be concrete
                # Detect date-only turn: CREATE_APPOINTMENT with date_refs but no time_refs
                is_date_only_turn = (
                    intent_payload_name == "CREATE_APPOINTMENT" and
                    date_refs and
                    not time_refs and
                    needs_clarification
                )

                # For date-only turns, always resolve date to ensure context has concrete values
                # This ensures context never carries only symbolic temporal values
                # For other cases, resolve if not already bound
                if (is_date_only_turn or (not bound_start_date and date_refs)):
                    # Resolve date directly using binder's date resolution
                    # This treats date-only as a valid partial state, not an error
                    from luma.calendar.calendar_binder import _bind_dates
                    # get_timezone is already imported at module level (line 37)

                    # Get timezone and now from function scope (set earlier in resolve_message)
                    tz = get_timezone(timezone)
                    # Use now from pipeline context (injected, not system time)

                    # Get date_mode from resolved_booking
                    date_mode = resolved_booking.get("date_mode", "single_day")

                    # Resolve dates immediately - treat as partially resolved appointment
                    # For date-only turns, always resolve even if calendar_booking has it
                    # This ensures context always contains concrete resolved dates
                    resolved_date_range = _bind_dates(
                        date_refs, date_mode, now, tz)
                    if resolved_date_range:
                        # For date-only turns, always use resolved date (even if calendar_booking had it)
                        # This ensures context never carries only symbolic temporal values
                        if is_date_only_turn:
                            bound_start_date = resolved_date_range.get(
                                "start_date")
                            bound_end_date = resolved_date_range.get(
                                "end_date")
                        elif not bound_start_date:
                            # For non-date-only turns, only use resolved date if not already bound
                            bound_start_date = resolved_date_range.get(
                                "start_date")
                            bound_end_date = resolved_date_range.get(
                                "end_date")
                        logger.info(
                            f"[context] Resolved date for {'date-only turn' if is_date_only_turn else 'follow-up'}: {date_refs[0]} -> {bound_start_date}",
                            extra={'request_id': request_id, 'date_mode': date_mode,
                                   'is_date_only_turn': is_date_only_turn}
                        )

                # CRITICAL: For date-only turns, always persist resolved date in context
                # Context must never carry only symbolic temporal values
                # Use bound date if available, otherwise fallback to semantic reference
                # For date-only turns, bound_start_date should always be set after resolution above
                context_payload["start_date"] = bound_start_date if bound_start_date else date_refs[0]
                if len(date_refs) >= 2:
                    context_payload["end_date"] = bound_end_date if bound_end_date else date_refs[1]

            # Add time information to context (similar to date handling)
            time_refs = resolved_booking.get("time_refs", [])
            time_mode = resolved_booking.get("time_mode", "none")
            time_constraint = resolved_booking.get("time_constraint")

            if time_refs or time_mode != "none" or time_constraint:
                # Semantic reference (what user said)
                if time_refs:
                    # For exact time, use first time_ref
                    if time_mode == "exact" and time_refs:
                        context_payload["time_ref"] = time_refs[0]
                    # For window/fuzzy, use first ref or all refs
                    elif time_refs:
                        context_payload["time_ref"] = time_refs[0] if len(
                            time_refs) == 1 else time_refs

                # Include time_mode for client logic
                if time_mode != "none":
                    context_payload["time_mode"] = time_mode

                # Include time_constraint if present (e.g., "after 10am", "by 6pm")
                if time_constraint:
                    context_payload["time_constraint"] = time_constraint

                # Resolved time from calendar binding (if available)
                # Similar to how dates are bound, extract from calendar_booking
                if calendar_booking and calendar_booking.get("datetime_range"):
                    dt_start = calendar_booking["datetime_range"].get(
                        "start", "")
                    if dt_start:
                        # Extract time portion and convert to 12-hour format
                        # dt_start format: "2026-01-01T15:00:00+00:00"
                        try:
                            # Handle both ISO format with timezone and without
                            dt_str = dt_start.replace('Z', '+00:00')
                            if '+' in dt_str or dt_str.endswith('Z'):
                                dt = datetime.fromisoformat(
                                    dt_str.replace('Z', '+00:00'))
                            else:
                                dt = datetime.fromisoformat(dt_str)
                            # Format as 12-hour time (e.g., "3:00 PM")
                            time_str = dt.strftime("%I:%M %p")
                            # Remove leading zero from hour (e.g., "03:00 PM" -> "3:00 PM")
                            if time_str.startswith('0'):
                                time_str = time_str[1:]
                            context_payload["time"] = time_str
                        except (ValueError, AttributeError):
                            # Fallback: extract time portion directly and convert
                            if "T" in dt_start:
                                time_part = dt_start.split(
                                    "T")[1].split("+")[0].split("-")[0]
                                # Convert 24h to 12h format
                                try:
                                    hour, minute = time_part.split(":")[:2]
                                    hour_int = int(hour)
                                    minute_str = minute[:2]  # Get MM part
                                    if hour_int == 0:
                                        context_payload["time"] = f"12:{minute_str} AM"
                                    elif hour_int < 12:
                                        context_payload["time"] = f"{hour_int}:{minute_str} AM"
                                    elif hour_int == 12:
                                        context_payload["time"] = f"12:{minute_str} PM"
                                    else:
                                        context_payload["time"] = f"{hour_int - 12}:{minute_str} PM"
                                except (ValueError, IndexError):
                                    # If parsing fails, just use the time part as-is
                                    pass

        # Extract entities for non-booking intents (DISCOVERY, QUOTE, DETAILS, etc.)
        # Booking intents and MODIFY_BOOKING should not include entities field
        entities_payload = None
        is_modify_booking = intent == "MODIFY_BOOKING"
        if not is_booking_intent_flag and not is_modify_booking:
            # Extract services from extraction result
            service_families = _get_business_categories(extraction_result)
            # Always include entities field for non-booking intents
            entities_payload = {}
            if service_families:
                # Format services with text and canonical (same format as booking.services)
                # Preserve resolved_alias if present
                entities_payload["services"] = [
                    format_service_for_response(service)
                    for service in service_families
                    if isinstance(service, dict) and service.get("text")
                ]

        processing_time = round((time.perf_counter() - start_time) * 1000, 2)

        # Add entity trace
        execution_trace["entity"] = {
            "service_ids": [s.get("text", "") if isinstance(s, dict) else str(s) for s in _get_business_categories(extraction_result)],
            "dates": [d.get("text", "") if isinstance(d, dict) else str(d) for d in (extraction_result.get("dates", []) + extraction_result.get("dates_absolute", []))],
            "times": [t.get("text", "") if isinstance(t, dict) else str(t) for t in extraction_result.get("times", [])]
        }

        # Add response trace (build issues for trace too)
        issues_for_trace: Dict[str, Any] = {}
        if needs_clarification:
            time_issues_for_trace = None
            if merged_semantic_result:
                time_issues_for_trace = merged_semantic_result.resolved_booking.get(
                    "time_issues", [])
            elif semantic_result:
                time_issues_for_trace = semantic_result.resolved_booking.get(
                    "time_issues", [])
            issues_for_trace = build_issues(
                missing_slots, time_issues_for_trace)

        execution_trace["response"] = {
            "status": STATUS_NEEDS_CLARIFICATION if needs_clarification else STATUS_READY,
            "intent": api_intent,
            "issues": issues_for_trace if issues_for_trace else {},
            "has_booking": booking_payload is not None,
            "has_clarification": needs_clarification
        }

        # Build final response summary (with issues)
        final_response_issues: Dict[str, Any] = {}
        if needs_clarification:
            time_issues_for_final = None
            if merged_semantic_result:
                time_issues_for_final = merged_semantic_result.resolved_booking.get(
                    "time_issues", [])
            elif semantic_result:
                time_issues_for_final = semantic_result.resolved_booking.get(
                    "time_issues", [])
            final_response_issues = build_issues(
                missing_slots, time_issues_for_final)

        final_response = {
            "status": STATUS_NEEDS_CLARIFICATION if needs_clarification else STATUS_READY,
            "intent": api_intent,
            "issues": final_response_issues if final_response_issues else {}
        }

        # Validate trace completeness (fail fast in debug mode)
        debug_flag = str(request.args.get("debug", "0")).lower()
        debug_mode = debug_flag in {"1", "true", "yes"}
        if debug_mode:
            required_trace_keys = ["entity", "semantic",
                                   "decision", "binder", "response"]
            missing_keys = [
                key for key in required_trace_keys if key not in execution_trace]
            if missing_keys:
                raise ValueError(
                    f"EXECUTION_TRACE incomplete: missing keys {missing_keys}")

        # Ensure trace and final_response are always included (even if empty)
        # This ensures the log structure is consistent
        if not execution_trace:
            execution_trace = {}
        if not final_response:
            final_response = {}

        # Build sentence trace - capture sentence evolution through pipeline
        # Capture the actual values flowing through the pipeline (do not recompute)
        normalized_text = extraction_result.get(
            "osentence", text) if extraction_result else text
        parameterized_text = extraction_result.get(
            "psentence", "") if extraction_result else ""

        # Intent resolver is called with raw text, but should use osentence (normalized)
        # Capture what is actually passed to resolve_intent (currently raw text)
        # Note: The intent resolver normalizes internally, but we capture what was passed
        intent_input_text = text  # Currently passed as raw text to resolve_intent

        sentence_trace = {
            "raw_text": text,
            "normalized_text": normalized_text,
            "parameterized_text": parameterized_text,
            "intent_input_text": intent_input_text
        }

        # Build complete input payload - capture all initial request data
        input_payload = {
            'user_id': user_id,
            'raw_text': text,
            'domain': domain,
            'timezone': timezone
        }

        # Include tenant_context if present (with aliases and booking_mode)
        if tenant_context:
            tenant_context_for_trace = {}
            if isinstance(tenant_context, dict):
                # Include aliases if present
                if "aliases" in tenant_context:
                    tenant_context_for_trace["aliases"] = tenant_context["aliases"]
                # Include booking_mode if present
                if "booking_mode" in tenant_context:
                    tenant_context_for_trace["booking_mode"] = tenant_context["booking_mode"]
            # Only add tenant_context to input if it has content
            if tenant_context_for_trace:
                input_payload['tenant_context'] = tenant_context_for_trace

        # Validate stable fields in debug mode only (non-breaking enforcement)
        # This ensures stable fields are present and have expected types
        # Debug fields are not validated and may change freely
        if debug_mode:
            trace_data = {
                'request_id': request_id,
                'input': input_payload,
                'trace': execution_trace,
                'final_response': final_response
            }
            validate_stable_fields(trace_data, debug_mode=True)

        # Emit single consolidated execution trace log
        # Field classification: See luma/trace_contract.py
        # - STABLE fields (request_id, input, final_response, trace.response.*, trace.semantic.*, trace.decision.state/reason/missing_slots)
        #   require versioning to change and are relied upon by downstream systems.
        # - DEBUG fields (sentence_trace, processing_time_ms, trace.entity.*, trace.binder.*, trace.*.rule_enforced, etc.)
        #   are internal diagnostics and may change without notice.
        # Trace version: v{TRACE_VERSION} (see luma/trace_contract.py)
        logger.info(
            "EXECUTION_TRACE",
            extra={
                'request_id': request_id,
                'input': input_payload,
                'sentence_trace': sentence_trace,
                'trace': execution_trace,
                'final_response': final_response,
                'processing_time_ms': processing_time
            }
        )

        # INVARIANT: Only require booking_payload for intents that produce it
        # MODIFY_BOOKING and CANCEL_BOOKING do NOT produce booking_payload (intent-specific semantics)
        registry = get_intent_registry()
        intent_meta = registry.get(api_intent) if api_intent else None
        produces_booking = intent_meta.produces_booking_payload if intent_meta else False
        
        # Only enforce booking_payload requirement for intents that produce it
        if produces_booking and booking_payload is None and not needs_clarification:
            logger.error(
                f"INVARIANT VIOLATION: Intent {api_intent} requires booking_payload but it is None for user {user_id}",
                extra={
                    'request_id': request_id,
                    'intent': api_intent,
                    'decision_status': decision_result.status if decision_result else None,
                    'has_calendar_booking': bool(calendar_booking),
                    'produces_booking_payload': produces_booking,
                    'note': 'This should not happen - decision/binder should have produced booking_payload'
                }
            )
            # Force clarification instead of silently rebuilding
            needs_clarification = True
            clarification_reason = "INCOMPLETE_BOOKING_STATE"

        # booking_payload is already set above for both RESOLVED and PARTIAL cases
        # For non-booking intents, booking_payload may be None (which is fine)

        # Project minimal booking output shape using ResponseBuilder
        response_builder = ResponseBuilder()
        if booking_payload is not None:
            booking_payload = response_builder.format_booking_payload(
                booking_payload,
                intent_payload_name,
                calendar_booking,
                request_id=request_id
            )

        # Build issues object from missing_slots and time_issues
        issues: Dict[str, Any] = {}
        # POLICY: If decision.state == RESOLVED, skip issue construction entirely
        # Decision layer is authoritative - no issues should be reported for RESOLVED requests
        # UNKNOWN intents skip issues building (pure extraction, no validation)
        if not is_unknown_intent and needs_clarification and not (decision_result and decision_result.status == "RESOLVED"):
            # Get time_issues from resolved_booking if available
            time_issues_for_issues = None
            if merged_semantic_result:
                time_issues_for_issues = merged_semantic_result.resolved_booking.get(
                    "time_issues", [])
            elif semantic_result:
                time_issues_for_issues = semantic_result.resolved_booking.get(
                    "time_issues", [])

            # Fix 4: Filter service_id from missing_slots for reservations when services exist
            # For CREATE_RESERVATION, remove service_id from issues if services exist
            filtered_missing_slots = missing_slots
            # Check for services in semantic result (available even when booking_payload is None)
            semantic_services = None
            if merged_semantic_result and merged_semantic_result.resolved_booking:
                semantic_services = merged_semantic_result.resolved_booking.get(
                    "services")
            elif semantic_result and semantic_result.resolved_booking:
                semantic_services = semantic_result.resolved_booking.get(
                    "services")

            # Filter missing slots (but don't override decision status)
            # Response must mirror decision exactly - decision layer already handled service resolution
            filtered_missing_slots = missing_slots

            # Guard: Preserve normalized missing slots for unanchored weekday ranges
            # Do not filter out start_date or end_date for normalized weekday ranges
            if normalized_weekday_range:
                # Lock: Ensure both start_date and end_date are preserved
                filtered_missing_slots = ["start_date", "end_date"]
            else:
                # Filter out "service" from missing_slots for MULTIPLE_MATCHES cases
                # These should use "ambiguous" not "missing", so remove from missing_slots before build_issues
                if (clarification_reason == ClarificationReason.MULTIPLE_MATCHES.value or
                        service_resolution_reason == ClarificationReason.MULTIPLE_MATCHES.value):
                    # MULTIPLE_MATCHES: service family resolved, variant ambiguous
                    # Can come from semantic resolver (clarification_reason) or decision layer (service_resolution_reason)
                    filtered_missing_slots = [
                        s for s in filtered_missing_slots if s not in ("service", "service_id")]

            # Build issues from filtered missing_slots (excludes ambiguous service slots)
            issues = build_issues(
                filtered_missing_slots, time_issues_for_issues)

            # Add service ambiguity to issues (using "ambiguous" not "missing")
            # Use "service" key for consistency (both appointments and reservations)
            if (clarification_reason == ClarificationReason.MULTIPLE_MATCHES.value or
                    service_resolution_reason == ClarificationReason.MULTIPLE_MATCHES.value):
                issues["service"] = "ambiguous"

            # Add service unresolved state (canonical matched but tenant resolution failed)
            # This applies to all requests - all require tenant resolution
            if (clarification_reason == ClarificationReason.UNSUPPORTED_SERVICE.value or
                    service_resolution_reason == ClarificationReason.UNSUPPORTED_SERVICE.value):
                # Check if services were extracted (canonical matched)
                if semantic_services:
                    # Service matched at canonical level but tenant resolution failed
                    issues["service"] = "unresolved"
                # If no services extracted, it remains "missing" (from build_issues)

        # Build slots first (single source of truth for temporal data)
        # Slots MUST be present whenever any resolved data exists (service, date, datetime)
        # Slots are built for both ready and clarification cases if data is resolved
        slots: Dict[str, Any] = {}

        # UNKNOWN intent: Build slots directly from semantic output (pure extraction, no booking logic)
        # Calendar binder is intentionally skipped for UNKNOWN, so we normalize date/time refs directly
        # Reuse existing normalization functions (_bind_single_date, bind_times) - do NOT reparse text
        # UNKNOWN intents should always have needs_clarification=False (forced at line 938)
        if is_unknown_intent:
            # Get semantic_booking from merged_semantic_result or semantic_result (fallback)
            # merged_semantic_result = semantic_result for stateless Luma (line 510)
            semantic_booking = {}
            if merged_semantic_result and merged_semantic_result.resolved_booking:
                semantic_booking = merged_semantic_result.resolved_booking
            elif semantic_result and semantic_result.resolved_booking:
                # Fallback to semantic_result if merged_semantic_result is not available
                semantic_booking = semantic_result.resolved_booking
            
            # Date handling: Normalize date_refs using existing date normalizer
            date_mode = semantic_booking.get("date_mode")
            date_refs = semantic_booking.get("date_refs", [])
            
            if date_mode == "single_day" and len(date_refs) >= 1:
                # Single date: normalize using _bind_single_date (reuse existing normalizer)
                try:
                    tz = get_timezone(timezone)
                    # Ensure now is timezone-aware for _bind_single_date
                    # now should already be timezone-aware from line 371, but ensure it is
                    if now.tzinfo is None:
                        now_tz_aware = _localize_datetime(now, tz)
                    else:
                        now_tz_aware = now
                    bound_date = _bind_single_date(date_refs[0], now_tz_aware, tz)
                    if bound_date:
                        slots["date"] = bound_date.strftime("%Y-%m-%d")
                    else:
                        logger.warning(
                            f"[UNKNOWN] _bind_single_date returned None for date_ref: {date_refs[0]}",
                            extra={'request_id': request_id, 'date_ref': date_refs[0], 'date_mode': date_mode}
                        )
                except Exception as e:
                    # If normalization fails, skip date (extraction-only, no fallback)
                    logger.warning(
                        f"[UNKNOWN] Date normalization failed: {str(e)}",
                        extra={'request_id': request_id, 'date_ref': date_refs[0] if date_refs else None, 'error': str(e)}
                    )
                    pass
            
            elif date_mode == "range" and len(date_refs) >= 2:
                # Date range: normalize both dates using _bind_single_date
                try:
                    tz = get_timezone(timezone)
                    # Ensure now is timezone-aware for _bind_single_date
                    if now.tzinfo is None:
                        now_tz_aware = _localize_datetime(now, tz)
                    else:
                        now_tz_aware = now
                    start_date_dt = _bind_single_date(date_refs[0], now_tz_aware, tz)
                    end_date_dt = _bind_single_date(date_refs[1], now_tz_aware, tz)
                    if start_date_dt and end_date_dt:
                        # Fix year drift if needed (same logic as _bind_dates)
                        if start_date_dt > end_date_dt:
                            from datetime import datetime as dt
                            start_date_dt = _localize_datetime(
                                dt(end_date_dt.year, start_date_dt.month, start_date_dt.day), tz
                            )
                        slots["date_range"] = {
                            "start": start_date_dt.strftime("%Y-%m-%d"),
                            "end": end_date_dt.strftime("%Y-%m-%d")
                        }
                except Exception as e:
                    # If normalization fails, skip date_range (extraction-only)
                    logger.debug(f"[UNKNOWN] Date range normalization failed: {str(e)}", extra={'request_id': request_id})
                    pass
            
            # Time handling: Normalize time_refs using existing time normalizer (bind_times)
            time_refs = semantic_booking.get("time_refs", [])
            time_mode = semantic_booking.get("time_mode", "none")
            if len(time_refs) >= 1:
                try:
                    tz = get_timezone(timezone)
                    # Ensure now is timezone-aware for bind_times
                    if now.tzinfo is None:
                        now_tz_aware = _localize_datetime(now, tz)
                    else:
                        now_tz_aware = now
                    time_windows = extraction_result.get("time_windows", []) if extraction_result else []
                    time_result = bind_times(
                        time_refs,
                        time_mode,
                        now_tz_aware,
                        tz,
                        time_windows=time_windows
                    )
                    if time_result:
                        start_time = time_result.get("start_time")
                        if start_time:
                            slots["time"] = start_time
                except Exception as e:
                    # If normalization fails, skip time (extraction-only, no fallback)
                    logger.debug(f"[UNKNOWN] Time normalization failed: {str(e)}", extra={'request_id': request_id})
                    pass
            
            # Service handling: Extract from semantic_booking (tenant alias normalization)
            # CRITICAL: service_id must be a TENANT alias key, never a canonical ID
            services = semantic_booking.get("services", [])
            if len(services) == 1 and isinstance(services[0], dict):
                service = services[0]
                
                # Priority 1: Use resolved_alias if present (this is the tenant alias key)
                tenant_alias_key = service.get("resolved_alias")
                
                # Priority 2: If no resolved_alias, map canonical -> tenant alias key using tenant_context.aliases
                if not tenant_alias_key:
                    canonical = service.get("canonical")
                    if canonical and tenant_context and isinstance(tenant_context, dict):
                        aliases = tenant_context.get("aliases", {})
                        if isinstance(aliases, dict):
                            # Inverse lookup: find tenant alias key for this canonical
                            # aliases dict is {alias_key: canonical}, so we need to find key by value
                            for alias_key, alias_canonical in aliases.items():
                                if alias_canonical == canonical:
                                    tenant_alias_key = alias_key
                                    break
                
                # Priority 3: If still no mapping, use text (raw matched text, not canonical)
                # This ensures we NEVER return a canonical ID in service_id
                if not tenant_alias_key:
                    tenant_alias_key = service.get("text")
                    # Safety check: if text is a canonical ID (contains "."), don't use it
                    # Instead, use the first alias key from aliases if available
                    if tenant_alias_key and "." in tenant_alias_key:
                        # This looks like a canonical ID, try to find an alias
                        if tenant_context and isinstance(tenant_context, dict):
                            aliases = tenant_context.get("aliases", {})
                            if isinstance(aliases, dict) and aliases:
                                # Use the first alias key as fallback (better than canonical)
                                tenant_alias_key = list(aliases.keys())[0]
                
                if tenant_alias_key:
                    slots["service_id"] = tenant_alias_key
            
            # date + time present → keep them SEPARATE (do NOT collapse to datetime)
            # This is already handled above - date and time are set separately
            
            # Skip all output-shaping cleanup for UNKNOWN (no removal of date, time, date_range, etc.)
            # Slots are built directly from semantic output using normalization functions

        # Get resolved data sources (prioritize booking_payload for ready, semantic for clarification)
        resolved_services = None
        resolved_date_range = None
        resolved_datetime_range = None

        if booking_payload is not None:
            # Ready case: use booking_payload (most authoritative)
            resolved_services = booking_payload.get("services") or []
            resolved_date_range = booking_payload.get("date_range")
            # For CREATE_APPOINTMENT ready responses, ensure datetime_range is available
            # Priority: booking_payload.datetime_range > calendar_booking.datetime_range
            resolved_datetime_range = booking_payload.get("datetime_range")
            if not resolved_datetime_range and calendar_booking:
                datetime_range_from_calendar = calendar_booking.get(
                    "datetime_range")
                if datetime_range_from_calendar:  # Only use if truthy
                    resolved_datetime_range = datetime_range_from_calendar
        elif needs_clarification:
            # Clarification case: try to get resolved data from semantic result or decision trace
            # Service resolution: check decision trace for resolved tenant_service_id
            resolved_tenant_service_id = results.get("stages", {}).get(
                "decision", {}).get("resolved_tenant_service_id")
            if resolved_tenant_service_id:
                # Service was resolved (even if other fields are missing)
                resolved_services = [
                    {"tenant_service_id": resolved_tenant_service_id}]

            # Temporal data: check calendar_booking if available (may have partial binding)
            if calendar_booking:
                resolved_date_range = calendar_booking.get("date_range")
                resolved_datetime_range = calendar_booking.get(
                    "datetime_range")

        # For CREATE_APPOINTMENT, ensure datetime_range is set from calendar_booking if not already set
        # This mirrors reservation behavior which always checks calendar_booking for date_range
        if intent_payload_name == "CREATE_APPOINTMENT" and not resolved_datetime_range:
            # Try calendar_booking first
            if calendar_booking:
                datetime_range_from_calendar = calendar_booking.get(
                    "datetime_range")
                # Only set if truthy (not None, not empty dict)
                if datetime_range_from_calendar:
                    resolved_datetime_range = datetime_range_from_calendar
            else:
                # Fallback: get from results["stages"]["calendar"] directly (binder output)
                calendar_stage = results.get("stages", {}).get("calendar", {})
                calendar_booking_from_stage = calendar_stage.get(
                    "calendar_booking", {}) if calendar_stage else {}
                datetime_range_from_stage = calendar_booking_from_stage.get(
                    "datetime_range") if calendar_booking_from_stage else None
                # Also try direct access in case datetime_range is at top level of calendar_stage
                if not datetime_range_from_stage:
                    datetime_range_from_stage = calendar_stage.get(
                        "datetime_range")
                if datetime_range_from_stage:
                    resolved_datetime_range = datetime_range_from_stage
        
        # For MODIFY_BOOKING, check calendar_booking and semantic result for date_range/datetime_range
        # (booking_payload is None for MODIFY_BOOKING, so we need to check calendar_booking/semantic result directly)
        # Note: Run this check regardless of needs_clarification to populate resolved_date_range/resolved_datetime_range
        if intent_payload_name == "MODIFY_BOOKING":
            # Check calendar_booking first (if available)
            if calendar_booking:
                if not resolved_date_range:
                    resolved_date_range = calendar_booking.get("date_range")
                if not resolved_datetime_range:
                    resolved_datetime_range = calendar_booking.get("datetime_range")
            # Fallback: check semantic result for date_range and datetime_range (if calendar binding was skipped)
            # This is especially important when calendar binding is skipped for MODIFY_BOOKING
            if merged_semantic_result and merged_semantic_result.resolved_booking:
                semantic_booking = merged_semantic_result.resolved_booking
                # Check if semantic result has date_range or if we need to construct it from date_refs
                if not resolved_date_range and semantic_booking.get("date_range"):
                    resolved_date_range = semantic_booking.get("date_range")
                # Check if semantic result has datetime_range (from delta normalization)
                if not resolved_datetime_range and semantic_booking.get("datetime_range"):
                    resolved_datetime_range = semantic_booking.get("datetime_range")

        # SYNTHESIS: For CREATE_APPOINTMENT with RESOLVED status, construct datetime_range if missing
        # Decision layer is authoritative - if decision says RESOLVED, slots must include datetime_range
        # Binder output is optional; do not gate on calendar_booking.datetime_range
        if (intent_payload_name == "CREATE_APPOINTMENT" and
            decision_result and decision_result.status == "RESOLVED" and
                not resolved_datetime_range):

            # Get date_range from resolved_date_range or calendar_booking
            date_range_for_synthesis = resolved_date_range
            if not date_range_for_synthesis and calendar_booking:
                date_range_for_synthesis = calendar_booking.get("date_range")

            # Get time_mode and time_refs from semantic result
            semantic_booking = merged_semantic_result.resolved_booking if merged_semantic_result else {}
            time_mode = semantic_booking.get("time_mode")
            time_refs = semantic_booking.get("time_refs", [])
            time_constraint = semantic_booking.get("time_constraint")

            # Check if we have date_range and valid time_mode
            if (date_range_for_synthesis and
                    time_mode in {"window", "fuzzy", "exact"}):

                # Extract start_date from date_range
                start_date = date_range_for_synthesis.get(
                    "start_date") or date_range_for_synthesis.get("start")
                if not start_date:
                    # Try end_date as fallback (for single-day appointments)
                    start_date = date_range_for_synthesis.get(
                        "end_date") or date_range_for_synthesis.get("end")

                if start_date:
                    # Determine time window based on time_mode and time_refs
                    window_start = "00:00"
                    window_end = "23:59"

                    if time_mode == "exact" and time_refs:
                        # Exact time: use first time_ref as both start and end
                        exact_time = time_refs[0] if time_refs else None
                        if exact_time:
                            window_start = exact_time
                            window_end = exact_time
                    elif time_mode in {"window", "fuzzy"}:
                        # Window or fuzzy: resolve using FUZZY_TIME_WINDOWS
                        from luma.config.temporal import FUZZY_TIME_WINDOWS

                        # Check time_refs for fuzzy time keywords
                        fuzzy_keyword = None
                        for time_ref in time_refs:
                            if isinstance(time_ref, str):
                                time_ref_lower = time_ref.lower()
                                if time_ref_lower in FUZZY_TIME_WINDOWS:
                                    fuzzy_keyword = time_ref_lower
                                    break

                        # Also check time_constraint for fuzzy keywords
                        if not fuzzy_keyword and time_constraint:
                            constraint_label = time_constraint.get("label")
                            if constraint_label and isinstance(constraint_label, str):
                                constraint_label_lower = constraint_label.lower()
                                if constraint_label_lower in FUZZY_TIME_WINDOWS:
                                    fuzzy_keyword = constraint_label_lower

                        # If fuzzy keyword found, use FUZZY_TIME_WINDOWS
                        if fuzzy_keyword:
                            window_start, window_end = FUZZY_TIME_WINDOWS[fuzzy_keyword]
                        elif time_constraint and time_mode == "window":
                            # Window mode: use time_constraint start/end if available
                            constraint_start = time_constraint.get("start")
                            constraint_end = time_constraint.get("end")
                            if constraint_start:
                                window_start = constraint_start
                            if constraint_end:
                                window_end = constraint_end

                    # Construct datetime_range ISO-8601 strings
                    # Format: YYYY-MM-DDTHH:MMZ (match binder format exactly)
                    try:
                        # Parse start_date (could be YYYY-MM-DD or ISO format)
                        if isinstance(start_date, str):
                            if "T" in start_date:
                                # Already ISO format, extract date part
                                date_part = start_date.split("T")[0]
                            else:
                                date_part = start_date

                            # Construct ISO-8601 datetime strings - match binder format
                            # Binder uses format: "YYYY-MM-DDTHH:MMZ" (no seconds)
                            resolved_datetime_range = {
                                "start": f"{date_part}T{window_start}Z",
                                "end": f"{date_part}T{window_end}Z"
                            }
                        else:
                            pass
                    except Exception as e:
                        pass

        # Build service_id slot (if service is resolved)
        if resolved_services:
            # Use tenant_service_id from service object (presentation layer)
            # Contract: tenant_service_id is the public API, canonical is internal-only
            primary = resolved_services[-1] if isinstance(resolved_services[-1], dict) else (
                resolved_services[0] if isinstance(resolved_services[0], dict) else {})

            # Priority 1: Check for resolved_alias (explicit tenant alias match from semantic resolution)
            # This preserves the explicitly mentioned tenant alias when multiple aliases map to the same canonical
            resolved_alias = primary.get(
                "resolved_alias") if isinstance(primary, dict) else None
            if resolved_alias:
                slots["service_id"] = resolved_alias
                logger.debug(
                    f"[slots] Using resolved_alias (explicit match) from service: '{resolved_alias}'"
                )
            else:
                # Priority 2: Check for tenant_service_id from service object (set during annotation)
                tenant_service_id = primary.get(
                    "tenant_service_id") if isinstance(primary, dict) else None
                if tenant_service_id:
                    slots["service_id"] = tenant_service_id
                    logger.debug(
                        f"[slots] Using tenant_service_id from service: '{tenant_service_id}'"
                    )
                else:
                    # Priority 3: Fallback to resolved_tenant_service_id from decision layer
                    resolved_tenant_service_id = results.get("stages", {}).get(
                        "decision", {}).get("resolved_tenant_service_id")
                    if resolved_tenant_service_id:
                        slots["service_id"] = resolved_tenant_service_id

            # FINAL NORMALIZATION: Ensure service_id is always a tenant alias key, never a canonical
            # INVARIANT: API responses must NEVER expose canonical service IDs
            if slots.get("service_id") and tenant_context and isinstance(tenant_context, dict):
                aliases = tenant_context.get("aliases", {})
                if isinstance(aliases, dict) and aliases:
                    service_id_value = slots["service_id"]

                    # Check if service_id is already a tenant alias key (direct match)
                    if service_id_value in aliases:
                        # Already a tenant alias key - no normalization needed
                        pass
                    else:
                        # Check if service_id is a canonical value - reverse lookup to find tenant alias key
                        # aliases structure: {tenant_alias_key: canonical_family}
                        # Example: {"suite": "room", "delux": "room"} means "room" is canonical
                        # If service_id is "room", we need to find a tenant alias key that maps to it
                        # Priority: Use resolved_alias if present (explicit match), otherwise pick first match
                        tenant_alias_key = None
                        resolved_alias_from_service = primary.get(
                            "resolved_alias") if isinstance(primary, dict) else None

                        # If resolved_alias exists and maps to this canonical, use it (preserves explicit match)
                        if resolved_alias_from_service and resolved_alias_from_service in aliases:
                            canonical_for_resolved = aliases.get(
                                resolved_alias_from_service)
                            if canonical_for_resolved and (
                                service_id_value == canonical_for_resolved or
                                ("." in service_id_value and service_id_value.endswith(
                                    f".{canonical_for_resolved}"))
                            ):
                                tenant_alias_key = resolved_alias_from_service
                                logger.debug(
                                    f"[slots] Using resolved_alias from normalization: '{tenant_alias_key}'"
                                )

                        # If no resolved_alias match, pick first alias that maps to this canonical
                        if not tenant_alias_key:
                            for alias_key, canonical_family in aliases.items():
                                # Check if service_id matches the canonical family
                                # Handle both full canonical IDs (e.g., "hospitality.room") and family names (e.g., "room")
                                if canonical_family:
                                    # Exact match with canonical family
                                    if service_id_value == canonical_family:
                                        tenant_alias_key = alias_key
                                        break
                                    # Match with full canonical ID (e.g., "hospitality.room" contains "room")
                                    elif "." in service_id_value and service_id_value.endswith(f".{canonical_family}"):
                                        tenant_alias_key = alias_key
                                        break

                        if tenant_alias_key:
                            # Replace canonical with tenant alias key
                            slots["service_id"] = tenant_alias_key
                        else:
                            # service_id is not in aliases and doesn't match any canonical
                            # This violates the invariant - canonical IDs must not appear in responses
                            # Log error and remove service_id to prevent canonical exposure
                            logger.error(
                                f"[slots] INVARIANT VIOLATION: service_id '{service_id_value}' is not a tenant alias key and doesn't map to any canonical. Removing from response.",
                                extra={'request_id': request_id,
                                       'service_id': service_id_value}
                            )
                            slots.pop("service_id", None)

        # Build temporal slots based on intent-specific temporal shapes
        # CREATE_RESERVATION → slots.date_range
        # CREATE_APPOINTMENT → slots.datetime_range
        # UNKNOWN intents skip this (slots already built directly from semantic output above)
        if not is_unknown_intent and intent_payload_name == "CREATE_RESERVATION":
            # Reservations use date_range {start, end}
            if resolved_date_range:
                # Convert date_range format from start_date/end_date to start/end for response
                if isinstance(resolved_date_range, dict):
                    start_date = resolved_date_range.get("start_date") or resolved_date_range.get("start")
                    end_date = resolved_date_range.get("end_date") or resolved_date_range.get("end")
                    # Store date_range with start/end format (response contract)
                    slots["date_range"] = {
                        "start": start_date,
                        "end": end_date
                    } if start_date and end_date else resolved_date_range
                else:
                    slots["date_range"] = resolved_date_range
        elif not is_unknown_intent and intent_payload_name == "CREATE_APPOINTMENT":
            # Appointments use datetime_range {start, end}
            # Source directly from binder output in results["stages"]["calendar"]["calendar_booking"]["datetime_range"]
            # This ensures we get the value even if calendar_result variable is empty
            calendar_stage = results.get("stages", {}).get("calendar", {})
            calendar_booking_from_binder = calendar_stage.get(
                "calendar_booking", {}) if calendar_stage else {}
            datetime_range_from_binder = calendar_booking_from_binder.get(
                "datetime_range") if calendar_booking_from_binder else None

            # Use binder output if available (truthy check), otherwise fall back to resolved_datetime_range
            datetime_range_for_slots = datetime_range_from_binder if datetime_range_from_binder else resolved_datetime_range

            if datetime_range_for_slots:
                source_used = 'binder' if datetime_range_from_binder else 'resolved'
                slots["datetime_range"] = datetime_range_for_slots
                # Set has_datetime flag when datetime_range exists (response contract)
                slots["has_datetime"] = True
        elif not is_unknown_intent and intent_payload_name in {"MODIFY_BOOKING", "CANCEL_BOOKING"}:
            # MODIFY_BOOKING and CANCEL_BOOKING use booking_id slot
            # Extract booking_id from entities
            if extraction_result and extraction_result.get("booking_id"):
                slots["booking_id"] = extraction_result["booking_id"]

        # Extract clarification_data from current_clarification if present
        clarification_data_for_response = None
        if current_clarification and isinstance(current_clarification, dict):
            clarification_data_for_response = current_clarification.get("data")

        # Normalize INCOMPLETE_BINDING to a public clarification reason
        # INCOMPLETE_BINDING is internal/debug-only and must never be exposed in responses
        public_clarification_reason = clarification_reason
        if clarification_reason == ClarificationReason.INCOMPLETE_BINDING.value:
            # Normalize based on intent and missing slots
            if intent_name == "CREATE_RESERVATION":
                # For reservations, INCOMPLETE_BINDING typically means missing date range
                public_clarification_reason = ClarificationReason.MISSING_DATE_RANGE.value
            elif missing_slots:
                # Use missing slots to determine appropriate reason
                if "start_date" in missing_slots and "end_date" in missing_slots:
                    public_clarification_reason = ClarificationReason.MISSING_DATE_RANGE.value
                elif "date" in missing_slots:
                    public_clarification_reason = ClarificationReason.MISSING_DATE.value
                elif "time" in missing_slots:
                    public_clarification_reason = ClarificationReason.MISSING_TIME.value
                else:
                    # Default to MISSING_DATE for temporal binding issues
                    public_clarification_reason = ClarificationReason.MISSING_DATE.value
            else:
                # Default to MISSING_DATE if no specific missing slots identified
                public_clarification_reason = ClarificationReason.MISSING_DATE.value

        # ============================================================
        # CENTRALIZED MODIFY_BOOKING OUTPUT SHAPING (critical)
        # ============================================================
        # This runs AFTER decision == RESOLVED and BEFORE response return.
        # Output shaping rules for MODIFY_BOOKING (delta-only output):
        # - Always include booking_id
        # - If any time OR date change detected → set has_datetime = true
        # - If reservation date range detected → include date_range
        # - For appointments: datetime_range must exist when has_datetime=True
        # - For reservations: preserve legacy start_date/end_date fields
        # This logic exists in ONE place only (final response shaping),
        # not in semantic or decision layers.
        # UNKNOWN intents skip all output-shaping cleanup (pure extraction)
        if not is_unknown_intent and intent_payload_name == "MODIFY_BOOKING" and decision_result and decision_result.status == "RESOLVED":
            # Ensure booking_id is always included (should already be set earlier, but verify)
            if extraction_result and extraction_result.get("booking_id") and not slots.get("booking_id"):
                slots["booking_id"] = extraction_result["booking_id"]
            # Determine booking mode
            semantic_booking = merged_semantic_result.resolved_booking if merged_semantic_result else {}
            booking_mode = semantic_booking.get("booking_mode", domain)
            is_reservation = (booking_mode == "reservation" or domain == "reservation")
            
            # Check for date_range (reservations) - may come from calendar binding or semantic normalization
            has_date_range = False
            if is_reservation:
                # Check resolved_date_range (from calendar binding or semantic result)
                if resolved_date_range and isinstance(resolved_date_range, dict):
                    start_date = resolved_date_range.get("start_date") or resolved_date_range.get("start")
                    end_date = resolved_date_range.get("end_date") or resolved_date_range.get("end")
                    # Only include date_range if we have both start and end (allow start == end for destination-only moves)
                    if start_date and end_date:
                        # Handle "from X to Y" pattern: collapse to destination only
                        # Detect pattern in original text: "from <date> to <date>"
                        text_lower = text.lower() if text else ""
                        # Match "from <something> to <something>" pattern
                        from_to_pattern = r'\bfrom\s+.*?\s+to\s+.*'
                        if re.search(from_to_pattern, text_lower):
                            # Collapse to destination date only
                            slots["date_range"] = {
                                "start": end_date,
                                "end": end_date
                            }
                        else:
                            slots["date_range"] = {
                                "start": start_date,
                                "end": end_date
                            }
                        # Preserve legacy delta fields for reservations
                        slots["start_date"] = start_date
                        slots["end_date"] = end_date
                        has_date_range = True
                # Also check semantic result for date_range (may have been set by semantic resolver)
                elif semantic_booking.get("date_range") and isinstance(semantic_booking.get("date_range"), dict):
                    date_range_from_semantic = semantic_booking.get("date_range")
                    start_date = date_range_from_semantic.get("start_date") or date_range_from_semantic.get("start")
                    end_date = date_range_from_semantic.get("end_date") or date_range_from_semantic.get("end")
                    if start_date and end_date:
                        # Handle "from X to Y" pattern: collapse to destination only
                        text_lower = text.lower() if text else ""
                        from_to_pattern = r'\bfrom\s+.*?\s+to\s+.*'
                        if re.search(from_to_pattern, text_lower):
                            slots["date_range"] = {
                                "start": end_date,
                                "end": end_date
                            }
                        else:
                            slots["date_range"] = {
                                "start": start_date,
                                "end": end_date
                            }
                        # Preserve legacy delta fields for reservations
                        slots["start_date"] = start_date
                        slots["end_date"] = end_date
                        has_date_range = True
            
            # Check for time-related changes (appointments or time-only modifications)
            # Priority: semantic normalization > calendar binding
            has_time_change = False
            has_date_change = False
            
            if merged_semantic_result and merged_semantic_result.resolved_booking:
                # Check semantic result for time-related changes
                semantic_has_datetime = semantic_booking.get("has_datetime")
                semantic_datetime_range = semantic_booking.get("datetime_range")
                time_refs = semantic_booking.get("time_refs", [])
                time_constraint = semantic_booking.get("time_constraint")
                time_mode = semantic_booking.get("time_mode")
                date_refs = semantic_booking.get("date_refs", [])
                date_mode = semantic_booking.get("date_mode")
                
                # Check if time-related change exists
                has_time_change = (
                    semantic_has_datetime or
                    bool(semantic_datetime_range) or
                    len(time_refs) > 0 or
                    time_constraint is not None or
                    (time_mode and time_mode != "none")
                )
                
                # Check if date-related change exists (for appointments, date-only changes should set has_datetime)
                has_date_change = (
                    len(date_refs) > 0 or
                    (date_mode and date_mode != "none" and date_mode != "flexible") or
                    bool(semantic_booking.get("date_range"))
                )
            
            # For appointments (service mode), any date OR time change → set has_datetime = true
            # For reservations, only date_range is output (no has_datetime)
            if not is_reservation:
                # Appointment mode: any date OR time change → has_datetime = true
                if has_time_change or has_date_change:
                    slots["has_datetime"] = True
                
                # For appointments: ensure datetime_range exists when has_datetime=True
                # This is required by API contract
                if slots.get("has_datetime"):
                    # First, try to get datetime_range from resolved sources
                    if not slots.get("datetime_range"):
                        if resolved_datetime_range:
                            slots["datetime_range"] = resolved_datetime_range
                        elif calendar_booking and calendar_booking.get("datetime_range"):
                            slots["datetime_range"] = calendar_booking.get("datetime_range")
                        else:
                            # Call build_datetime_range_for_api to construct minimal structure
                            build_datetime_range_for_api(
                                slots,
                                semantic_booking,
                                domain,
                                request_id=request_id,
                                user_id=user_id
                            )
            # For reservations, date_range is already set above if present
            
            # CRITICAL: Remove fields that shouldn't be in delta output
            # BUT preserve fields required by API contract or tests:
            # - datetime_range: NEVER remove if has_datetime=True
            # - start_date/end_date: NEVER remove for MODIFY_BOOKING reservations
            # - time_range: can be removed (redundant with datetime_range)
            if not is_reservation:
                # For appointments: only remove datetime_range if has_datetime is False
                if not slots.get("has_datetime"):
                    slots.pop("datetime_range", None)
            # For reservations: start_date and end_date are preserved above
            slots.pop("time_range", None)
            # booking_payload is already None for MODIFY_BOOKING (handled above)

        # CENTRAL NORMALIZATION: Ensure service_id is always a tenant alias key, never a canonical
        # INVARIANT: API responses must NEVER expose canonical service IDs
        # This runs for ALL intents in ONE central place before final response assembly
        if slots and tenant_context and isinstance(tenant_context, dict):
            aliases = tenant_context.get("aliases", {})
            if isinstance(aliases, dict) and aliases and slots.get("service_id"):
                service_id_value = slots.get("service_id")
                
                # Check if service_id is already a tenant alias key (direct match)
                if service_id_value not in aliases:
                    # service_id is not a tenant alias key - check if it's a canonical value
                    # Reverse lookup: find tenant alias key that maps to this canonical
                    # aliases structure: {tenant_alias_key: canonical_family}
                    # Example: {"suite": "room", "delux": "room"} means "room" is canonical
                    tenant_alias_key = None
                    
                    # Priority 1: Check for resolved_alias from semantic result (preserves explicit match)
                    # This is important for CREATE_RESERVATION cases where semantic resolver may have set resolved_alias
                    if merged_semantic_result and merged_semantic_result.resolved_booking:
                        resolved_services = merged_semantic_result.resolved_booking.get("services", [])
                        if resolved_services:
                            # Get resolved_alias from first service (explicit match)
                            primary_service = resolved_services[0] if isinstance(resolved_services[0], dict) else {}
                            resolved_alias = primary_service.get("resolved_alias")
                            if resolved_alias and resolved_alias in aliases:
                                # resolved_alias is a valid tenant alias key - use it
                                tenant_alias_key = resolved_alias
                                logger.info(
                                    f"[response] Using resolved_alias from semantic result: '{tenant_alias_key}'",
                                    extra={'request_id': request_id}
                                )
                    
                    # Priority 2: If no resolved_alias found, search for alias key that maps to this canonical
                    if not tenant_alias_key:
                        for alias_key, canonical_family in aliases.items():
                            if canonical_family:
                                # Exact match with canonical family (e.g., "room")
                                if service_id_value == canonical_family:
                                    tenant_alias_key = alias_key
                                    break
                                # Match with full canonical ID (e.g., "hospitality.room" contains "room")
                                elif "." in service_id_value and service_id_value.endswith(f".{canonical_family}"):
                                    tenant_alias_key = alias_key
                                    break
                    
                    if tenant_alias_key:
                        # Replace canonical with tenant alias key
                        slots["service_id"] = tenant_alias_key
                        logger.info(
                            f"[response] Normalized service_id from canonical '{service_id_value}' to tenant alias '{tenant_alias_key}'",
                            extra={'request_id': request_id}
                        )
                    else:
                        # service_id is not in aliases and doesn't match any canonical
                        # This violates the invariant - canonical IDs must not appear in responses
                        # Log error and remove service_id to prevent canonical exposure
                        logger.error(
                            f"[response] INVARIANT VIOLATION: service_id '{service_id_value}' is not a tenant alias key and doesn't map to any canonical. Removing from response.",
                            extra={'request_id': request_id, 'service_id': service_id_value}
                        )
                        slots.pop("service_id", None)

        # Build response body using ResponseBuilder (slots now built above)
        # Response must mirror decision: if decision says ambiguous service → needs_clarification
        response_builder = ResponseBuilder()
        response_body = response_builder.build_response_body(
            intent_payload=intent_payload,
            needs_clarification=needs_clarification,
            clarification_reason=public_clarification_reason,
            issues=issues if issues else {},
            booking_payload=booking_payload,
            entities_payload=entities_payload,
            slots=slots if slots else None,
            context_payload=context_payload,
            clarification_data=clarification_data_for_response,
            debug_data=results if debug_mode else None,
            request_id=request_id
        )

        # Removed per-stage logging - consolidated trace emitted at end

        return jsonify(response_body)

    except Exception as e:  # noqa: BLE001
        logger.error(
            f"Processing failed: {str(e)}",
            extra={
                'request_id': request_id,
                'error_type': type(e).__name__,
                'text_length': len(text) if 'text' in locals() else 0
            },
            exc_info=True
        )
        return jsonify({
            "success": False,
            "error": f"Processing failed: {str(e)}"
        }), 500
