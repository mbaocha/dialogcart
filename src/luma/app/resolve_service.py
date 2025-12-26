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
- Merging already-produced semantics (combining memory state with current semantic results)
- Enforcing decision/binder guardrails (validating completeness, temporal shapes, etc.)

If logic would violate this invariant, replace the behavior with:
- Logging (diagnostic information for debugging)
- Clarification (let the decision layer handle missing information)

This ensures semantic integrity and prevents cascading hacks that corrupt the resolution pipeline.
"""
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone as dt_timezone

from flask import jsonify

from luma.calendar.calendar_binder import bind_calendar, bind_times, combine_datetime_range, get_timezone, get_booking_policy, CalendarBindingResult
from luma.pipeline import LumaPipeline
from luma.decision import decide_booking_status
from luma.memory.merger import merge_booking_state, extract_memory_state_for_response
from luma.memory.policy import (
    # Legacy functions (deprecated, kept for backward compatibility but not used)
    is_active_booking,
    is_partial_booking,
    maybe_persist_draft,
    should_clear_memory,
    should_persist_memory,
    prepare_memory_for_persistence,
    get_final_memory_state,
    # New state-first model functions
    state_exists,
    is_new_task,
    get_state_intent,
    merge_slots_for_followup
)
from luma.resolution.semantic_resolver import SemanticResolutionResult
from luma.perf import StageTimer
from luma.config.temporal import APPOINTMENT_TEMPORAL_TYPE, INTENT_TEMPORAL_SHAPE
from luma.trace_contract import validate_stable_fields
from luma.trace.stage_snapshot import capture_stage_snapshot


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


def resolve_message(
    # Flask request globals
    g,
    request,

    # Module globals
    intent_resolver,
    memory_store,
    logger,

    # Constants
    APPOINTMENT_TEMPORAL_TYPE_CONST,
    INTENT_TEMPORAL_SHAPE_CONST,
    MEMORY_TTL,

    # Helper functions
    _merge_semantic_results,
    _localize_datetime,
    find_normalization_dir,
    _get_business_categories,
    _count_mutable_slots_modified,
    _has_booking_verb,
    validate_required_slots,
    _build_issues,
    _format_service_for_response,
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
            logger.info(
                f"Received tenant_context with {aliases_count} aliases",
                extra={'request_id': request_id,
                       'aliases_count': aliases_count}
            )

        if not text or not isinstance(text, str):
            logger.warning("Invalid text parameter", extra={
                           'request_id': request_id})
            return jsonify({
                "success": False,
                "error": "'text' must be a non-empty string"
            }), 400

        # Load memory state
        memory_state = None
        # Initialize execution_trace early for memory timing
        execution_trace = {"timings": {}}

        if memory_store:
            try:
                # Time memory read operation
                memory_read_input = {"user_id": user_id, "domain": domain}
                with StageTimer(execution_trace, "memory", request_id=request_id):
                    memory_state = memory_store.get(user_id, domain)
                memory_read_output = {
                    "found": memory_state is not None,
                    "has_intent": bool(memory_state.get("intent")) if memory_state else False,
                    "has_booking_state": bool(memory_state.get("booking_state")) if memory_state else False
                }

                # Capture memory read snapshot (if stage_snapshots exists)
                # Note: We capture this early, so we'll initialize it if needed
                if "stage_snapshots" not in execution_trace:
                    execution_trace["stage_snapshots"] = []
                memory_read_snapshot = capture_stage_snapshot(
                    stage_name="memory_read",
                    input_data=memory_read_input,
                    output_data=memory_read_output
                )
                execution_trace["stage_snapshots"].append(memory_read_snapshot)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to load memory: {e}", extra={
                               'request_id': request_id})

        # Removed per-stage logging - consolidated trace emitted at end

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

            # NEW STATE-FIRST MODEL: Determine if this is a new task or follow-up
            state_exists_flag = state_exists(memory_state)
            is_new_task_flag = is_new_task(
                input_text=text,
                extraction_result=extraction_result,
                intent_result={"intent": classifier_intent,
                               "confidence": confidence},
                state_exists_flag=state_exists_flag
            )

            # CRITICAL INVARIANT: UNKNOWN intent must NEVER start a new task when memory_state exists
            # This safeguard ensures memory is preserved for follow-ups even if is_new_task returns True
            if state_exists_flag and classifier_intent == "UNKNOWN":
                is_new_task_flag = False
                logger.debug(
                    f"Override: UNKNOWN intent with existing state treated as follow-up for user {user_id}",
                    extra={'request_id': request_id,
                           'classifier_intent': classifier_intent}
                )

            # Apply state-first behavior
            if is_new_task_flag:
                # New task: discard previous state, use classifier intent
                memory_state = None
                state_exists_flag = False
                intent = classifier_intent
                logger.debug(
                    f"New task detected for user {user_id}",
                    extra={'request_id': request_id,
                           'classifier_intent': classifier_intent}
                )
            else:
                # Follow-up: keep previous intent from state, classifier intent is advisory only
                state_intent = get_state_intent(memory_state)
                if state_intent:
                    intent = state_intent
                    logger.debug(
                        f"Follow-up detected for user {user_id}, using state intent: {intent}",
                        extra={'request_id': request_id,
                               'classifier_intent': classifier_intent, 'state_intent': intent}
                    )
                else:
                    # State exists but intent field is missing (e.g., legacy memory)
                    # INVARIANT: Do not infer intent from domain - use classifier intent (already produced by pipeline)
                    # If classifier intent is also unavailable, let decision layer handle clarification
                    if classifier_intent and classifier_intent != "UNKNOWN":
                        intent = classifier_intent
                        logger.debug(
                            f"Follow-up detected but state intent is None for user {user_id}, using classifier intent: {intent}",
                            extra={'request_id': request_id, 'classifier_intent': classifier_intent,
                                   'note': 'Using pipeline-produced intent, not inferring from domain'}
                        )
                    else:
                        # No valid intent available - log and let decision layer handle clarification
                        intent = classifier_intent  # May be UNKNOWN or None
                        logger.warning(
                            f"Follow-up detected but no valid intent available for user {user_id}",
                            extra={'request_id': request_id, 'classifier_intent': classifier_intent,
                                   'state_intent': None, 'note': 'Decision layer will handle clarification'}
                        )

            # Determine external_intent for decision layer
            # external_intent is the ONLY source of truth for intent type (CREATE_APPOINTMENT/CREATE_RESERVATION)
            # domain must NEVER imply intent
            external_intent = None
            if intent in {"CREATE_APPOINTMENT", "CREATE_RESERVATION"}:
                # Intent resolver returned external intent directly - use it as source of truth
                external_intent = intent
                intent = "CREATE_BOOKING"  # Normalize for internal use
            # If intent is CREATE_BOOKING, do NOT infer external_intent from domain
            # This ensures domain never implies intent - clarification is the correct behavior if needed

            # Store external_intent in results for decision layer and response
            # Only store it if it's one of the external intents, otherwise store None explicitly
            if external_intent in {"CREATE_APPOINTMENT", "CREATE_RESERVATION"}:
                results["stages"]["intent"]["external_intent"] = external_intent
            else:
                # Clear it if not valid (explicit None when external_intent is not available)
                results["stages"]["intent"]["external_intent"] = None

            # Store state-first model decision in results for debugging
            results["stages"]["intent"]["state_first"] = {
                "is_new_task": is_new_task_flag,
                "state_exists": state_exists_flag,
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

        # NEW STATE-FIRST MODEL: Semantic result handling
        # For follow-ups, merge semantic slots BEFORE decision layer
        # This ensures decision/completeness checking sees the merged state
        if not is_new_task_flag and memory_state and semantic_result:
            # Merge slots from memory into current semantic result BEFORE decision
            current_resolved_booking = semantic_result.resolved_booking or {}

            # Check for semantic fields in memory (preferred source)
            memory_semantic_booking = memory_state.get(
                "resolved_booking_semantics", {})
            memory_booking_state = memory_state.get("booking_state", {})

            # Merge at semantic level (services, date_refs, time_refs, duration)
            merged_resolved_booking = current_resolved_booking.copy()

            # SERVICES: Replace if mentioned in current, else keep from memory
            current_services = current_resolved_booking.get("services", [])
            if not current_services:
                # Prefer semantic booking, fallback to booking_state
                memory_services = memory_semantic_booking.get(
                    "services") or memory_booking_state.get("services", [])
                if memory_services:
                    merged_resolved_booking["services"] = memory_services

            # DATE_REFS: Replace if current has date_refs, else keep from memory
            current_date_refs = current_resolved_booking.get("date_refs", [])
            memory_date_refs = memory_semantic_booking.get("date_refs", [])
            memory_date_mode = memory_semantic_booking.get("date_mode")

            # INVARIANT: Do not parse or reinterpret raw text
            # Date merge logic: Use semantic resolver output as authoritative
            # If semantic resolver produced date_refs, use them as-is (no text parsing to infer structure)
            if current_date_refs:
                # Current has date_refs: replace (semantic resolver is authoritative)
                merged_resolved_booking["date_refs"] = current_date_refs
                # Preserve date_mode from current semantic result if available
                current_date_mode = current_resolved_booking.get("date_mode")
                if current_date_mode:
                    merged_resolved_booking["date_mode"] = current_date_mode
            elif not current_date_refs:
                # No current date_refs: keep from memory
                if memory_date_refs:
                    merged_resolved_booking["date_refs"] = memory_date_refs
                    if memory_date_mode:
                        merged_resolved_booking["date_mode"] = memory_date_mode
            else:
                # Current has date_refs: replace (default behavior)
                merged_resolved_booking["date_refs"] = current_date_refs
                # date_mode should already be preserved from current_resolved_booking.copy() above
                # but explicitly ensure it's set if present
                current_date_mode = current_resolved_booking.get("date_mode")
                if current_date_mode:
                    merged_resolved_booking["date_mode"] = current_date_mode

            # TIME_REFS: Replace if current has time_refs, else keep from memory
            current_time_refs = current_resolved_booking.get("time_refs", [])
            if not current_time_refs:
                memory_time_refs = memory_semantic_booking.get("time_refs", [])
                if memory_time_refs:
                    merged_resolved_booking["time_refs"] = memory_time_refs
                    # Also preserve time_mode if available
                    memory_time_mode = memory_semantic_booking.get("time_mode")
                    if memory_time_mode:
                        merged_resolved_booking["time_mode"] = memory_time_mode

            # Bug B: TIME_CONSTRAINT: Merge from memory if current doesn't have it
            # The binder needs time_constraint to build time_range/datetime_range
            current_time_constraint = current_resolved_booking.get(
                "time_constraint")
            if not current_time_constraint:
                memory_time_constraint = memory_semantic_booking.get(
                    "time_constraint")
                if memory_time_constraint:
                    merged_resolved_booking["time_constraint"] = memory_time_constraint

            # INVARIANT: Do not invent dates/times - if time_constraint is missing, let decision layer handle it
            # If time_refs exist but time_constraint is missing, log diagnostic info but do not derive it
            time_constraint_missing_but_derivable = False
            if (not merged_resolved_booking.get("time_constraint") and
                merged_resolved_booking.get("time_refs") and
                    merged_resolved_booking.get("time_mode") == "exact"):
                time_constraint_missing_but_derivable = True
                time_ref = merged_resolved_booking["time_refs"][0]
                logger.debug(
                    f"Time constraint missing but derivable from time_refs for user {user_id} (quarantined - not deriving)",
                    extra={
                        'request_id': request_id,
                        'time_ref': str(time_ref),
                        'time_mode': merged_resolved_booking.get("time_mode"),
                        'note': 'Decision layer will handle clarification if binder requires time_constraint'
                    }
                )

            # Store trace flag for diagnostic purposes
            if "semantic" not in execution_trace:
                execution_trace["semantic"] = {}
            execution_trace["semantic"]["time_constraint_missing_but_derivable"] = time_constraint_missing_but_derivable

            # DURATION: Replace if mentioned, else keep from memory
            current_duration = current_resolved_booking.get("duration")
            if current_duration is None:
                memory_duration = memory_semantic_booking.get(
                    "duration") or memory_booking_state.get("duration")
                if memory_duration is not None:
                    merged_resolved_booking["duration"] = memory_duration

            # Create merged semantic result
            merged_semantic_result = SemanticResolutionResult(
                resolved_booking=merged_resolved_booking,
                needs_clarification=semantic_result.needs_clarification,
                clarification=semantic_result.clarification
            )

            logger.debug(
                f"Merged semantic slots for follow-up (before decision) for user {user_id}",
                extra={
                    'request_id': request_id,
                    'merged_keys': list(merged_resolved_booking.keys()),
                    'has_services': bool(merged_resolved_booking.get("services")),
                    'has_date_refs': bool(merged_resolved_booking.get("date_refs")),
                    'has_time_refs': bool(merged_resolved_booking.get("time_refs"))
                }
            )
        else:
            # New task: use current semantic result as-is
            merged_semantic_result = semantic_result

        # OLD CONTINUATION LOGIC REMOVED - replaced by state-first model
        # All contextual update detection logic removed (handled by slot merge before decision)

        # Decision / Policy Layer - ACTIVE
        # Decision layer determines if clarification is needed BEFORE calendar binding
        # Policy operates ONLY on semantic roles, never on raw text or regex
        decision_result = None
        # Initialize semantic_for_decision before try block to ensure it's always defined
        semantic_for_decision = merged_semantic_result.resolved_booking if merged_semantic_result else {}

        try:
            # Load booking policy from config
            booking_policy = get_booking_policy()

            # OLD CONTINUATION LOGIC REMOVED - replaced by state-first model
            # The old contextual update detection logic has been removed.
            # Slot merging now happens after calendar binding via merge_slots_for_followup()

            # CRITICAL INVARIANT: decision must see fully merged semantics
            # If there is an active booking, the semantic object passed to decision
            # MUST be the merged semantic booking, not the current fragment
            # Attach booking_mode for decision policy (service vs reservation)
            if isinstance(semantic_for_decision, dict):
                semantic_for_decision["booking_mode"] = domain

            # Get intent_name for temporal shape validation
            # Use external_intent if available (CREATE_APPOINTMENT/CREATE_RESERVATION),
            # otherwise use normalized intent
            intent_name_for_decision = results["stages"]["intent"].get(
                "external_intent"
            ) or intent

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

        # NEW STATE-FIRST MODEL: Effective intent is just the intent we determined
        # OLD CONTEXTUAL_UPDATE logic bypassed (kept for reference but not used in state-first model)
        effective_intent = intent

        # OLD LOGIC (bypassed):
        # effective_intent = detect_contextual_update(...)

        # Stage 6: Required slots validation (before calendar binding)
        intent_name_for_slots_raw = intent or effective_intent
        intent_name_for_slots = intent_name_for_slots_raw.get("name") if isinstance(
            intent_name_for_slots_raw, dict) else intent_name_for_slots_raw
        missing_required = []
        if intent_name_for_slots:
            missing_required = validate_required_slots(
                intent_name_for_slots,
                merged_semantic_result.resolved_booking if merged_semantic_result else {},
                extraction_result or {}
            )
        skip_prebind = (
            intent_name_for_slots == "CREATE_APPOINTMENT"
            and missing_required == ["time"]
        )
        if missing_required and not skip_prebind:
            results["stages"]["intent"]["status"] = "needs_clarification"
            results["stages"]["intent"]["missing_slots"] = missing_required
            calendar_result = CalendarBindingResult(
                calendar_booking={},
                needs_clarification=False,
                clarification=None
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

            if decision_result and (decision_result.status == "RESOLVED" or missing_only_time):
                # Proceed with calendar binding
                # Use effective_intent for calendar binding (CONTEXTUAL_UPDATE treated as CREATE_BOOKING)
                # Use merged semantic result if this was a PARTIAL continuation
                # CONTEXTUAL_UPDATE logic removed (dead code)
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
                            # Appointments require datetime_range
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
                            decision_result.reason = "INCOMPLETE_BINDING"
                            # Update execution trace to reflect downgrade
                            if "decision" in execution_trace:
                                execution_trace["decision"]["state"] = "NEEDS_CLARIFICATION"
                                execution_trace["decision"]["reason"] = "INCOMPLETE_BINDING"
                            logger.warning(
                                f"Guardrail: Downgraded READY to NEEDS_CLARIFICATION due to missing binder output",
                                extra={
                                    'request_id': request_id,
                                    'external_intent': external_intent,
                                    'clarification_reason': 'INCOMPLETE_BINDING'
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
                    temporal_shape_for_trace = INTENT_TEMPORAL_SHAPE_CONST.get(
                        external_intent) if external_intent else None
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
                        clarification=None
                    )
                    return jsonify({"success": False, "data": results}), 500
            else:
                # decision_state != RESOLVED - skip calendar binding
                # Temporal shape incomplete or other clarification needed
                reason = f"decision={decision_result.status if decision_result else 'NONE'}"
                calendar_result = CalendarBindingResult(
                    calendar_booking={},
                    needs_clarification=False,
                    clarification=None
                )
                results["stages"]["calendar"] = calendar_result.to_dict()
                # Binder was skipped - add trace with input even though not called
                semantic_for_binder = merged_semantic_result.resolved_booking if merged_semantic_result else semantic_result.resolved_booking
                external_intent_for_trace = results["stages"]["intent"].get(
                    "external_intent") or intent
                temporal_shape_for_trace = INTENT_TEMPORAL_SHAPE_CONST.get(
                    external_intent_for_trace) if external_intent_for_trace else None
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
        clar = plan_clarification(
            intent_resp, extraction_result, merged_semantic_result, decision_result)

        # If calendar needs clarification and none set yet, use calendar clarification
        if cal_needs_clarification and clar.get("status") != "needs_clarification":
            clar["status"] = "needs_clarification"
            # Extract reason from calendar clarification
            cal_reason = cal_clar_dict.get("reason") if isinstance(
                cal_clar_dict, dict) else None
            if cal_reason:
                if isinstance(cal_reason, str):
                    clar["clarification_reason"] = cal_reason
                elif hasattr(cal_reason, "value"):
                    clar["clarification_reason"] = cal_reason.value

        needs_clarification = clar.get("status") == "needs_clarification"
        missing_slots = clar.get("missing_slots", [])
        clarification_reason = clar.get("clarification_reason")

        # Guardrail: If decision was downgraded to INCOMPLETE_BINDING, ensure clarification_reason is set
        if decision_result and decision_result.reason == "INCOMPLETE_BINDING":
            clarification_reason = "INCOMPLETE_BINDING"
            needs_clarification = True

        # If missing_slots not set in clarification, use decision trace as fallback
        if not missing_slots and execution_trace:
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
            enforced_missing = validate_required_slots(
                intent_name, resolved_snapshot, extraction_result or {})

            if enforced_missing:
                needs_clarification = True
                missing_slots = enforced_missing
                # Update clarification_reason based on missing slots
                if not clarification_reason:
                    if "time" in enforced_missing:
                        clarification_reason = "MISSING_TIME"
                    elif "date" in enforced_missing:
                        clarification_reason = "MISSING_DATE"
                    elif "service_id" in enforced_missing or "service" in enforced_missing:
                        clarification_reason = "MISSING_SERVICE"
                booking_payload = None
        # Temporal shape enforcement (authoritative, post-binding)
        if intent_name and intent_name in INTENT_TEMPORAL_SHAPE_CONST:
            shape = INTENT_TEMPORAL_SHAPE_CONST.get(intent_name)
            if shape == APPOINTMENT_TEMPORAL_TYPE_CONST:
                has_dtr = bool((calendar_booking or {}).get("datetime_range"))
                if not has_dtr:
                    temporal_missing: List[str] = []
                    date_refs = merged_semantic_result.resolved_booking.get(
                        "date_refs") if merged_semantic_result else []
                    if date_refs:
                        temporal_missing = ["time"]
                    else:
                        temporal_missing = ["date", "time"]
                    if temporal_missing:
                        needs_clarification = True
                        missing_slots = temporal_missing
                        # Update clarification_reason based on missing slots
                        if not clarification_reason:
                            if "time" in temporal_missing:
                                clarification_reason = "MISSING_TIME"
                            elif "date" in temporal_missing:
                                clarification_reason = "MISSING_DATE"
                            elif len(temporal_missing) >= 2:
                                clarification_reason = "MISSING_TIME"  # Default to time if both missing
                        booking_payload = None
                        response_body_status = "needs_clarification"

        # Extract current clarification
        # Priority: semantic clarification > calendar binding clarification > decision layer
        # CRITICAL: Semantic clarifications (e.g., SERVICE_VARIANT) must be preserved
        # even if decision layer says RESOLVED (due to invariant override)
        current_clarification = None

        # First priority: Check semantic resolution clarification (e.g., SERVICE_VARIANT ambiguity)
        if merged_semantic_result and merged_semantic_result.needs_clarification and merged_semantic_result.clarification:
            # Semantic resolution detected ambiguity (e.g., service variant ambiguity)
            # This must be preserved even if decision layer says RESOLVED
            current_clarification = merged_semantic_result.clarification.to_dict()
            logger.info(
                f"Preserving semantic clarification: {current_clarification.get('reason')}",
                extra={'request_id': request_id,
                       'clarification_reason': current_clarification.get('reason')}
            )
        elif decision_result and decision_result.status == "RESOLVED":
            # Decision is RESOLVED - no clarification needed
            # This clears any existing PARTIAL clarification from memory
            current_clarification = None
        elif calendar_result.needs_clarification and calendar_result.clarification:
            # Only validation errors from calendar binding (range conflicts, etc.)
            current_clarification = calendar_result.clarification.to_dict()

        # Prepare current booking state (only canonical fields)
        # Include date_range and time_range for merge logic to handle time-only updates
        # Format services to preserve resolved_alias if present
        calendar_services = calendar_booking.get("services", [])
        formatted_services = [
            _format_service_for_response(service)
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

        # NEW STATE-FIRST MODEL: Merge slots for follow-ups (after calendar binding)
        # For follow-ups, merge current_booking with memory booking state
        # New slot value replaces old, missing slot keeps old
        if not is_new_task_flag and memory_state and memory_state.get("booking_state"):
            memory_booking_state = memory_state.get("booking_state", {})
            if isinstance(memory_booking_state, dict):
                merged_booking = merge_slots_for_followup(
                    memory_booking=memory_booking_state,
                    current_booking=current_booking
                )
                current_booking = merged_booking
                logger.debug(
                    f"Merged slots for follow-up for user {user_id}",
                    extra={'request_id': request_id,
                           'merged_booking_keys': list(merged_booking.keys())}
                )

        # OLD CONTEXTUAL_UPDATE logic removed - this is handled by merge_slots_for_followup in the new state-first model
        # This entire block has been removed as part of the cleanup of obsolete continuation logic

        # Memory clearing and persistence decisions delegated to memory policy
        should_clear = should_clear_memory(effective_intent)
        if should_clear and memory_store:
            try:
                memory_store.clear(user_id, domain)
                logger.info(f"Cleared memory for {user_id} due to intent: {effective_intent}", extra={
                            'request_id': request_id})
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to clear memory: {e}", extra={
                               'request_id': request_id})

        # Determine if and how memory should be persisted
        should_persist, is_booking_intent, is_modify_with_booking_id, persist_intent = should_persist_memory(
            effective_intent=effective_intent,
            data=data,
            should_clear=should_clear
        )

        merged_memory = None
        if should_persist and is_booking_intent:
            # Prepare memory for persistence (handles RESOLVED vs PARTIAL logic)
            merged_memory = prepare_memory_for_persistence(
                memory_state=memory_state,
                decision_result=decision_result,
                persist_intent=persist_intent,
                current_booking=current_booking,
                current_clarification=current_clarification,
                merged_semantic_result=merged_semantic_result,
                logger=logger,
                user_id=user_id,
                request_id=request_id
            )

            # Persist merged state with CREATE_BOOKING intent
            # Only persist if we have a valid booking state (RESOLVED or PARTIAL)
            if memory_store:
                try:
                    # Time memory write operation
                    memory_input = {
                        "user_id": user_id,
                        "domain": domain,
                        "state_intent": merged_memory.get("intent") if merged_memory else None,
                        "state_has_booking": bool(merged_memory.get("booking_state")) if merged_memory else False
                    }
                    with StageTimer(execution_trace, "memory", request_id=request_id):
                        memory_store.set(
                            user_id=user_id,
                            domain=domain,
                            state=merged_memory,
                            ttl=MEMORY_TTL
                        )
                    memory_output = {
                        "stored": True,
                        "intent": merged_memory.get("intent") if merged_memory else None,
                        "has_booking_state": bool(merged_memory.get("booking_state")) if merged_memory else False
                    }

                    # Capture memory snapshot
                    memory_snapshot = capture_stage_snapshot(
                        stage_name="memory_write",
                        input_data=memory_input,
                        output_data=memory_output,
                        decision_flags={
                            "should_persist": True,
                            "is_booking_intent": is_booking_intent
                        }
                    )
                    if "stage_snapshots" not in execution_trace:
                        execution_trace["stage_snapshots"] = []
                    execution_trace["stage_snapshots"].append(memory_snapshot)

                    # Stage 8: STATE PERSIST - Log what was stored
                    storage_backend = "redis" if hasattr(
                        memory_store, 'redis') else "memory"
                    _log_stage(
                        logger, request_id, "state_persist",
                        input_data={"user_id": user_id, "domain": domain},
                        output_data={
                            "stored": {
                                "intent": merged_memory.get("intent"),
                                "booking_state": merged_memory.get("booking_state", {}).get("booking_state") if isinstance(merged_memory.get("booking_state"), dict) else None,
                                "has_services": bool(merged_memory.get("booking_state", {}).get("services") if isinstance(merged_memory.get("booking_state"), dict) else False),
                                "has_datetime_range": bool(merged_memory.get("booking_state", {}).get("datetime_range") if isinstance(merged_memory.get("booking_state"), dict) else False)
                            },
                            "storage_backend": storage_backend
                        }
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Failed to persist memory: {e}", extra={
                                   'request_id': request_id})
        elif should_persist and is_modify_with_booking_id:
            # MODIFY_BOOKING with booking_id: persist as MODIFY_BOOKING
            merged_memory = merge_booking_state(
                memory_state=None,  # Don't merge with CREATE_BOOKING draft
                current_intent="MODIFY_BOOKING",
                current_booking=current_booking,
                current_clarification=current_clarification
            )

            # Persist MODIFY_BOOKING state
            if memory_store:
                try:
                    # Time memory write operation
                    memory_input = {
                        "user_id": user_id,
                        "domain": domain,
                        "intent": "MODIFY_BOOKING",
                        "has_booking_state": bool(merged_memory.get("booking_state")) if merged_memory else False
                    }
                    with StageTimer(execution_trace, "memory", request_id=request_id):
                        memory_store.set(
                            user_id=user_id,
                            domain=domain,
                            state=merged_memory,
                            ttl=MEMORY_TTL
                        )
                    memory_output = {
                        "stored": True,
                        "intent": "MODIFY_BOOKING",
                        "has_booking_state": bool(merged_memory.get("booking_state")) if merged_memory else False
                    }

                    # Capture memory snapshot
                    memory_snapshot = capture_stage_snapshot(
                        stage_name="memory",
                        input_data=memory_input,
                        output_data=memory_output,
                        decision_flags={
                            "should_persist": True,
                            "is_modify_with_booking_id": True
                        }
                    )
                    if "stage_snapshots" not in execution_trace:
                        execution_trace["stage_snapshots"] = []
                    execution_trace["stage_snapshots"].append(memory_snapshot)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Failed to persist memory: {e}", extra={
                                   'request_id': request_id})

        # Get final memory state based on all decisions
        merged_memory = get_final_memory_state(
            should_clear=should_clear,
            should_persist=should_persist,
            is_booking_intent=is_booking_intent,
            is_modify_with_booking_id=is_modify_with_booking_id,
            effective_intent=effective_intent,
            memory_state=memory_state,
            current_booking=current_booking,
            current_clarification=current_clarification,
            merged_memory=merged_memory,
            logger=logger,
            request_id=request_id
        )

        # Post-semantic validation guard: Check for orphan slot updates
        # If extracted slots exist but cannot be applied (no booking_id, no draft, no booking),
        # return clarification instead of "successful" empty response
        # Build production response
        # Map CONTEXTUAL_UPDATE to CREATE_BOOKING in API response
        # CONTEXTUAL_UPDATE logic removed (dead code)
        api_intent = effective_intent
        # Get external_intent from results (set earlier for decision layer)
        external_intent_for_response = results["stages"]["intent"].get(
            "external_intent")
        intent_payload_name = external_intent_for_response if external_intent_for_response in {
            "CREATE_APPOINTMENT", "CREATE_RESERVATION"} else api_intent
        intent_payload = {"name": intent_payload_name,
                          "confidence": confidence}

        # Clarification fields from plan_clarification / calendar
        # Return booking state for CREATE_BOOKING or MODIFY_BOOKING
        # CRITICAL: For CREATE_BOOKING, booking must NEVER be null, even when clarification is needed
        booking_payload = None
        context_payload = None

        if (is_booking_intent or is_modify_with_booking_id) and not needs_clarification:
            booking_payload = extract_memory_state_for_response(merged_memory)
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
                            _format_service_for_response(service)
                            for service in current_services
                            if isinstance(service, dict)
                        ]
                    else:
                        # Format existing services from memory
                        booking_payload["services"] = [
                            _format_service_for_response(service)
                            for service in booking_payload.get("services", [])
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
                        'has_merged_memory': bool(merged_memory),
                        'note': 'Decision layer should have handled this - forcing clarification'
                    }
                )
                # Force clarification instead of silently rebuilding
                needs_clarification = True
                booking_payload = None
        elif memory_state and memory_state.get("intent") == "CREATE_BOOKING" and not needs_clarification:
            # Return existing booking state from memory for follow-ups
            booking_payload = extract_memory_state_for_response(memory_state)
            if booking_payload:
                booking_payload["booking_state"] = "RESOLVED"
                # Format services to preserve resolved_alias if present in current semantic result
                if merged_semantic_result:
                    current_services = merged_semantic_result.resolved_booking.get(
                        "services", [])
                    if current_services:
                        # Use services from current semantic result (which may have resolved_alias)
                        booking_payload["services"] = [
                            _format_service_for_response(service)
                            for service in current_services
                            if isinstance(service, dict)
                        ]
                    else:
                        # Format existing services from memory
                        booking_payload["services"] = [
                            _format_service_for_response(service)
                            for service in booking_payload.get("services", [])
                            if isinstance(service, dict)
                        ]
        elif is_booking_intent and needs_clarification:
            # For booking intents that need clarification, return lightweight context only
            resolved_booking = merged_semantic_result.resolved_booking
            services = resolved_booking.get("services", [])
            if not services:
                service_families = _get_business_categories(extraction_result)
                services = [
                    _format_service_for_response(service)
                    for service in service_families
                    if isinstance(service, dict) and service.get("text")
                ]
            else:
                services = [
                    _format_service_for_response(service)
                    for service in services
                    if isinstance(service, dict)
                ]
            date_refs = resolved_booking.get("date_refs") or []
            context_payload = {}
            if services:
                context_payload["services"] = services
            if date_refs:
                # Semantic reference (what user said)
                context_payload["start_date_ref"] = date_refs[0]
                if len(date_refs) >= 2:
                    context_payload["end_date_ref"] = date_refs[1]

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

                # Use bound date if available, otherwise fallback to semantic reference
                context_payload["start_date"] = bound_start_date if bound_start_date else date_refs[0]
                if len(date_refs) >= 2:
                    context_payload["end_date"] = bound_end_date if bound_end_date else date_refs[1]

            # Note: time_hint removed - now part of issues structure

        # Extract entities for non-booking intents (DISCOVERY, QUOTE, DETAILS, etc.)
        # CREATE_BOOKING and MODIFY_BOOKING should not include entities field
        entities_payload = None
        is_modify_booking = intent == "MODIFY_BOOKING"
        if not is_booking_intent and not is_modify_booking:
            # Extract services from extraction result
            service_families = _get_business_categories(extraction_result)
            # Always include entities field for non-booking intents
            entities_payload = {}
            if service_families:
                # Format services with text and canonical (same format as booking.services)
                # Preserve resolved_alias if present
                entities_payload["services"] = [
                    _format_service_for_response(service)
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
            issues_for_trace = _build_issues(
                missing_slots, time_issues_for_trace)

        execution_trace["response"] = {
            "status": "needs_clarification" if needs_clarification else "ready",
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
            final_response_issues = _build_issues(
                missing_slots, time_issues_for_final)

        final_response = {
            "status": "needs_clarification" if needs_clarification else "ready",
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

        # INVARIANT: Do not resurrect missing booking state by rebuilding it silently
        # If booking_payload is None when it should exist, this is an invariant violation
        # Log and force clarification instead of silently rebuilding
        if api_intent == "CREATE_BOOKING" and booking_payload is None and not needs_clarification:
            logger.error(
                f"INVARIANT VIOLATION: CREATE_BOOKING intent but booking_payload is None for user {user_id}",
                extra={
                    'request_id': request_id,
                    'intent': api_intent,
                    'decision_status': decision_result.status if decision_result else None,
                    'has_calendar_booking': bool(calendar_booking),
                    'has_merged_memory': bool(merged_memory),
                    'note': 'This should not happen - decision/binder should have produced booking_payload'
                }
            )
            # Force clarification instead of silently rebuilding
            needs_clarification = True
            clarification_reason = "INCOMPLETE_BOOKING_STATE"

        # booking_payload is already set above for both RESOLVED and PARTIAL cases
        # For non-booking intents, booking_payload may be None (which is fine)

        # Project minimal booking output shape
        if booking_payload is not None:
            # Normalize services to public canonical form
            if booking_payload.get("services"):
                booking_payload["services"] = [
                    _format_service_for_response(s)
                    for s in booking_payload.get("services", [])
                    if isinstance(s, dict)
                ]

            # Appointment responses: only datetime_range
            if intent_payload_name == "CREATE_APPOINTMENT":
                # Always copy datetime_range from calendar_booking if present
                # This comes from calendar binding and should override any memory values
                if "datetime_range" in calendar_booking:
                    booking_payload["datetime_range"] = calendar_booking["datetime_range"]
                booking_payload.pop("date", None)
                booking_payload.pop("time", None)
                booking_payload.pop("date_range", None)
                booking_payload.pop("time_range", None)
                booking_payload.pop("start_date", None)
                booking_payload.pop("end_date", None)

            # Reservation responses: only date_range (for internal booking_payload)
            if intent_payload_name == "CREATE_RESERVATION":
                # Build date_range from calendar_booking if available
                if "start_date" in calendar_booking and "end_date" in calendar_booking:
                    booking_payload["date_range"] = {
                        "start": calendar_booking["start_date"],
                        "end": calendar_booking["end_date"]
                    }
                # Clean up legacy fields
                booking_payload.pop("datetime_range", None)
                booking_payload.pop("date", None)
                booking_payload.pop("time", None)
                booking_payload.pop("time_range", None)
                booking_payload.pop("start_date", None)
                booking_payload.pop("end_date", None)

            # Remove legacy booking_state from response payloads
            booking_payload.pop("booking_state", None)

        # Build issues object from missing_slots and time_issues
        issues: Dict[str, Any] = {}
        if needs_clarification:
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
            if (intent_payload_name == "CREATE_RESERVATION" and
                "service_id" in missing_slots and
                    semantic_services):
                # Remove service_id from missing_slots display, but don't change status
                # Status comes from decision layer and must not be recomputed
                filtered_missing_slots = [
                    s for s in missing_slots if s != "service_id"]

            # Check for ambiguous service resolution for reservations
            if (intent_payload_name == "CREATE_RESERVATION" and
                    service_resolution_reason == "AMBIGUOUS_SERVICE"):
                # For ambiguous reservations, set issues.service_id = "ambiguous"
                issues = _build_issues(
                    filtered_missing_slots, time_issues_for_issues)
                issues["service_id"] = "ambiguous"
            else:
                issues = _build_issues(
                    filtered_missing_slots, time_issues_for_issues)

        # Build response body - status mirrors decision exactly (no recomputation)
        # Response must mirror decision: if decision says ambiguous service → needs_clarification
        response_body = {
            "success": True,
            "intent": intent_payload,
            "status": "needs_clarification" if needs_clarification else "ready",
            "issues": issues if issues else {},
            "clarification_reason": clarification_reason if needs_clarification else None,
            "needs_clarification": needs_clarification,
        }

        # Build slots (single source of truth for temporal data)
        # Slots MUST be present whenever any resolved data exists (service, date, datetime)
        # Slots are built for both ready and clarification cases if data is resolved
        slots: Dict[str, Any] = {}

        # Get resolved data sources (prioritize booking_payload for ready, semantic for clarification)
        resolved_services = None
        resolved_date_range = None
        resolved_datetime_range = None

        if booking_payload is not None:
            # Ready case: use booking_payload (most authoritative)
            resolved_services = booking_payload.get("services") or []
            resolved_date_range = booking_payload.get("date_range")
            resolved_datetime_range = booking_payload.get(
                "datetime_range") or calendar_booking.get("datetime_range")
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

        # Build service_id slot (if service is resolved)
        if resolved_services:
            # Use tenant_service_id from service object (presentation layer)
            # Contract: tenant_service_id is the public API, canonical is internal-only
            primary = resolved_services[-1] if isinstance(resolved_services[-1], dict) else (
                resolved_services[0] if isinstance(resolved_services[0], dict) else {})

            # Prefer tenant_service_id from service object (set during annotation)
            tenant_service_id = primary.get(
                "tenant_service_id") if isinstance(primary, dict) else None
            if tenant_service_id:
                slots["service_id"] = tenant_service_id
                logger.debug(
                    f"[slots] Using tenant_service_id from service: '{tenant_service_id}'"
                )
            else:
                # Fallback: use resolved_tenant_service_id from decision layer
                resolved_tenant_service_id = results.get("stages", {}).get(
                    "decision", {}).get("resolved_tenant_service_id")
                if resolved_tenant_service_id:
                    slots["service_id"] = resolved_tenant_service_id
                    logger.debug(
                        f"[slots] Using resolved tenant_service_id from decision: '{resolved_tenant_service_id}'"
                    )

        # Build temporal slots based on intent-specific temporal shapes
        # CREATE_RESERVATION → slots.date_range
        # CREATE_APPOINTMENT → slots.datetime_range
        if intent_payload_name == "CREATE_RESERVATION":
            # Reservations use date_range {start, end}
            if resolved_date_range:
                slots["date_range"] = resolved_date_range
        elif intent_payload_name == "CREATE_APPOINTMENT":
            # Appointments use datetime_range {start, end}
            if resolved_datetime_range:
                slots["datetime_range"] = resolved_datetime_range

        # Include slots if it has any content (for both ready and clarification)
        if slots:
            response_body["slots"] = slots

        # Handle clarification vs ready cases
        if needs_clarification:
            # For needs_clarification: include context but NO booking block
            if context_payload:
                response_body["context"] = context_payload
        else:
            # For ready status: include booking block (minimal, only confirmation_state)
            if booking_payload is not None:
                # Attach confirmation_state for ready bookings
                booking_payload["confirmation_state"] = "pending"

                # Build minimal booking block (temporal and service data is in slots)
                # Remove all fields that are exposed via slots
                booking_payload_copy = booking_payload.copy()
                # Exposed via slots.service_id
                booking_payload_copy.pop("services", None)
                # Exposed via slots.date_range
                booking_payload_copy.pop("date_range", None)
                # Exposed via slots.datetime_range
                booking_payload_copy.pop("datetime_range", None)
                booking_payload_copy.pop("duration", None)  # Removed entirely
                booking_payload_copy.pop("start_date", None)  # Legacy field
                booking_payload_copy.pop("end_date", None)  # Legacy field

                response_body["booking"] = booking_payload_copy

        # Add entities field for non-booking intents (always include, even if empty)
        if entities_payload is not None:
            response_body["entities"] = entities_payload

        # Attach full internal pipeline data only in debug mode
        if debug_mode:
            response_body["debug"] = results

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
