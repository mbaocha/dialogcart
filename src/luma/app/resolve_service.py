"""
Service for resolving conversational input and resolving intent/state.

This module contains the core resolution logic extracted from api.py.
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
    is_active_booking,
    is_partial_booking,
    maybe_persist_draft,
    normalize_intent_for_continuation,
    is_continuation_applicable,
    detect_continuation,
    merge_continuation_semantics,
    detect_contextual_update,
    should_clear_memory,
    should_persist_memory,
    prepare_memory_for_persistence,
    get_final_memory_state,
    CONTEXTUAL_UPDATE
)
from luma.resolution.semantic_resolver import SemanticResolutionResult
from luma.perf import StageTimer
from luma.config.temporal import APPOINTMENT_TEMPORAL_TYPE, INTENT_TEMPORAL_SHAPE
from luma.trace_contract import validate_stable_fields


# CONTEXTUAL_UPDATE is now imported from memory.policy


def resolve_message(
    # Flask request globals
    g,
    request,
    
    # Module globals
    intent_resolver,
    memory_store,
    logger,
    
    # Constants (CONTEXTUAL_UPDATE now imported from memory.policy, kept for backward compatibility)
    CONTEXTUAL_UPDATE_CONST,  # Alias for CONTEXTUAL_UPDATE from policy
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
                with StageTimer(execution_trace, "memory", request_id=request_id):
                    memory_state = memory_store.get(user_id, domain)
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
        start_time = time.time()

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

            # Store stage results
            results["stages"]["extraction"] = extraction_result
            results["stages"]["intent"] = intent_resp
            results["stages"]["structure"] = structure_dict
            results["stages"]["grouping"] = grouped_result
            results["stages"]["semantic"] = semantic_result.to_dict()

            # Expose backward-compatible fields
            intent = intent_resp["intent"]
            confidence = intent_resp["confidence"]

            external_intent = intent
            if intent in {"CREATE_APPOINTMENT", "CREATE_RESERVATION"}:
                results["stages"]["intent"]["external_intent"] = intent
                intent = "CREATE_BOOKING"

            # CRITICAL: Normalize intent for active booking continuations (PARTIAL or RESOLVED)
            # Delegated to memory policy
            normalized_intent, original_intent = normalize_intent_for_continuation(intent, memory_state)
            if original_intent is not None:
                intent = normalized_intent
                # Update results to reflect normalization
                results["stages"]["intent"]["original_intent"] = original_intent
                results["stages"]["intent"]["intent"] = intent
                results["stages"]["intent"]["normalized_for_active_booking"] = True

        except Exception as e:
            # Fallback to individual stage execution on pipeline error
            logger.error(f"Pipeline execution failed: {e}", extra={
                         'request_id': request_id}, exc_info=True)
            results["stages"]["extraction"] = {"error": str(e)}
            return jsonify({"success": False, "data": results}), 500

        # Check for PARTIAL booking continuation
        # Continuation detection delegated to memory policy
        # Initialize merged_semantic_result to semantic_result (will be updated if continuation detected)
        merged_semantic_result = semantic_result
        is_continuation = False
        is_resolved_continuation = False
        
        if is_continuation_applicable(memory_state, intent):
            # This is a continuation of an active booking (PARTIAL or RESOLVED)
            is_continuation, memory_resolved_booking, is_resolved_continuation = detect_continuation(
                memory_state, semantic_result
            )
            
            if is_continuation:
                # Merge semantic results: preserve memory, fill with current
                merged_data = merge_continuation_semantics(
                    memory_resolved_booking,
                    semantic_result
                )
                
                merged_semantic_result = SemanticResolutionResult(
                    resolved_booking=merged_data["resolved_booking"],
                    needs_clarification=merged_data["needs_clarification"],
                    clarification=merged_data["clarification"]
                )

            # Store merged result in debug output
            results["stages"]["semantic_merged"] = {
                "original": semantic_result.to_dict(),
                "merged": merged_semantic_result.to_dict(),
                "is_continuation": True
            }

            # Removed per-stage logging - consolidated trace emitted at end

            # Removed per-stage logging - consolidated trace emitted at end

        # Persist service-only and service+date drafts as active booking drafts
        # This ensures multi-turn slot filling works (service → date → time)
        # Check the merged result (or current if no merge happened)
        resolved_booking_for_draft_check = merged_semantic_result.resolved_booking
        memory_state = maybe_persist_draft(
            resolved_booking=resolved_booking_for_draft_check,
            memory_state=memory_state,
            intent=intent,
            memory_store=memory_store,
            user_id=user_id,
            domain=domain,
            memory_ttl=MEMORY_TTL,
            execution_trace=execution_trace,
            request_id=request_id,
            logger=logger
        )

        # Contextual time-only update detection
        # If user says "make it 10" and there's an active booking in memory,
        # treat it as a modification by reusing date from memory
        logger.debug(
            f"Checking contextual time-only update: user {user_id}",
            extra={
                'request_id': request_id,
                'intent': intent,
                'has_memory_state': memory_state is not None,
                'memory_intent': memory_state.get("intent") if memory_state else None,
                'memory_booking_state': memory_state.get("booking_state", {}).get("booking_state") if memory_state and isinstance(memory_state.get("booking_state"), dict) else None,
                'has_resolved_booking_semantics': bool(memory_state.get("resolved_booking_semantics", {})) if memory_state else False
            }
        )

        # Check for resolved_booking_semantics in memory (gating condition for contextual updates)
        # This allows contextual updates for both PARTIAL and RESOLVED bookings
        has_resolved_booking_semantics = bool(
            memory_state and memory_state.get("resolved_booking_semantics")
        )
        logger.debug(
            f"Contextual update gate check: user {user_id}",
            extra={
                'request_id': request_id,
                'has_resolved_booking_semantics': has_resolved_booking_semantics,
                'intent': intent,
                'intent_matches': intent == "CREATE_BOOKING"
            }
        )

        if intent == "CREATE_BOOKING" and has_resolved_booking_semantics:
            # Check current turn for time_refs (from original semantic_result)
            current_resolved_booking = semantic_result.resolved_booking
            current_time_refs = current_resolved_booking.get("time_refs", [])
            current_time_mode = current_resolved_booking.get(
                "time_mode", "none")

            # Check merged semantic state for date_refs (may already be merged from PARTIAL continuation)
            merged_resolved_booking = merged_semantic_result.resolved_booking
            merged_date_refs = merged_resolved_booking.get("date_refs", [])
            merged_date_mode = merged_resolved_booking.get("date_mode", "none")

            # If merged state doesn't have date_refs, check memory
            # This handles RESOLVED bookings where merged_semantic_result is just semantic_result
            if not merged_date_refs or merged_date_mode == "none":
                memory_resolved_booking = memory_state.get(
                    "resolved_booking_semantics", {})
                if memory_resolved_booking:
                    merged_date_refs = memory_resolved_booking.get(
                        "date_refs", [])
                    merged_date_mode = memory_resolved_booking.get(
                        "date_mode", "none")

            logger.debug(
                f"Evaluating contextual update conditions: user {user_id}",
                extra={
                    'request_id': request_id,
                    'current_time_refs': current_time_refs,
                    'current_time_mode': current_time_mode,
                    'merged_date_refs': merged_date_refs,
                    'merged_date_mode': merged_date_mode,
                    'has_time': bool(current_time_refs) and current_time_mode != "none",
                    'has_merged_date': bool(merged_date_refs) and merged_date_mode != "none",
                    'condition_1_time_refs': bool(current_time_refs),
                    'condition_2_time_mode': current_time_mode != "none",
                    'condition_3_merged_date_refs': bool(merged_date_refs),
                    'condition_4_merged_date_mode': merged_date_mode != "none",
                    'all_conditions_met': bool(current_time_refs) and current_time_mode != "none" and bool(merged_date_refs) and merged_date_mode != "none"
                }
            )

            # Check if this is a contextual time-only update
            # Allow if: current turn has time_refs AND merged state (or memory) has date_refs
            # Do NOT require current turn to have no date_refs
            if current_time_refs and current_time_mode != "none" and merged_date_refs and merged_date_mode != "none":
                logger.info(
                    f"Contextual time-only update condition met: user {user_id}",
                    extra={'request_id': request_id}
                )

                # Check if merged_semantic_result already has date_refs (from PARTIAL continuation merge)
                # If not, merge date from memory into current turn's resolved_booking
                if not merged_resolved_booking.get("date_refs") or merged_resolved_booking.get("date_mode") == "none":
                    # Load date from memory
                    memory_resolved_booking = memory_state.get(
                        "resolved_booking_semantics", {})

                    logger.debug(
                        f"Loaded resolved_booking_semantics from memory: user {user_id}",
                        extra={
                            'request_id': request_id,
                            'has_resolved_booking_semantics': bool(memory_resolved_booking),
                            'memory_date_refs': memory_resolved_booking.get("date_refs", []),
                            'memory_date_mode': memory_resolved_booking.get("date_mode", "none")
                        }
                    )

                    # If not stored, try to reconstruct from booking_state
                    if not memory_resolved_booking:
                        logger.debug(
                            f"No resolved_booking_semantics in memory, trying booking_state: user {user_id}",
                            extra={'request_id': request_id}
                        )
                        memory_booking_state = memory_state.get(
                            "booking_state", {})
                        memory_resolved_booking = {}
                        memory_services = memory_booking_state.get(
                            "services", [])
                        if memory_services:
                            memory_resolved_booking["services"] = memory_services

                    memory_date_refs = memory_resolved_booking.get(
                        "date_refs", [])
                    memory_date_mode = memory_resolved_booking.get(
                        "date_mode", "none")

                    logger.debug(
                        f"Memory date info: user {user_id}",
                        extra={
                            'request_id': request_id,
                            'memory_date_refs': memory_date_refs,
                            'memory_date_mode': memory_date_mode,
                            'can_merge': bool(memory_date_refs and memory_date_mode != "none")
                        }
                    )

                    # If memory has date information, merge it
                    if memory_date_refs and memory_date_mode != "none":
                        # Create updated resolved_booking with date from memory
                        updated_resolved_booking = current_resolved_booking.copy()
                        updated_resolved_booking["date_refs"] = memory_date_refs
                        updated_resolved_booking["date_mode"] = memory_date_mode
                        if "date_modifiers" in memory_resolved_booking:
                            updated_resolved_booking["date_modifiers"] = memory_resolved_booking.get(
                                "date_modifiers", [])

                        # Update merged_semantic_result
                        # CRITICAL: Preserve semantic clarifications (e.g., SERVICE_VARIANT) from original semantic_result
                        # Preserve original clarification if it exists
                        preserved_needs_clarification = semantic_result.needs_clarification
                        preserved_clarification = semantic_result.clarification

                        # Only override if the original didn't have clarification
                        if not preserved_needs_clarification or not preserved_clarification:
                            preserved_needs_clarification = False
                            preserved_clarification = None

                        merged_semantic_result = SemanticResolutionResult(
                            resolved_booking=updated_resolved_booking,
                            needs_clarification=preserved_needs_clarification,
                            clarification=preserved_clarification
                        )

                        logger.debug(
                            "Contextual time-only update detected, reusing date from memory",
                            extra={
                                'request_id': request_id,
                                'user_id': user_id,
                                'memory_date_refs': memory_date_refs,
                                'memory_date_mode': memory_date_mode,
                                'current_time_refs': current_time_refs,
                                'merged_date_refs': updated_resolved_booking.get('date_refs', []),
                                'merged_date_mode': updated_resolved_booking.get('date_mode', 'none')
                            }
                        )
                    else:
                        logger.debug(
                            f"Contextual update condition met but no date in memory: user {user_id}",
                            extra={
                                'request_id': request_id,
                                'memory_date_refs': memory_date_refs,
                                'memory_date_mode': memory_date_mode
                            }
                        )
                else:
                    # merged_semantic_result already has date_refs (from PARTIAL continuation merge)
                    logger.debug(
                        "Contextual time-only update detected, date already in merged state",
                        extra={
                            'request_id': request_id,
                            'user_id': user_id,
                            'current_time_refs': current_time_refs,
                            'merged_date_refs': merged_resolved_booking.get('date_refs', []),
                            'merged_date_mode': merged_resolved_booking.get('date_mode', 'none')
                        }
                    )
            else:
                logger.debug(
                    f"Contextual update condition not met: user {user_id}",
                    extra={
                        'request_id': request_id,
                        'current_time_refs': current_time_refs,
                        'current_time_mode': current_time_mode,
                        'merged_date_refs': merged_date_refs,
                        'merged_date_mode': merged_date_mode,
                        'has_time_refs': bool(current_time_refs),
                        'time_mode_not_none': current_time_mode != "none",
                        'has_merged_date_refs': bool(merged_date_refs),
                        'merged_date_mode_not_none': merged_date_mode != "none",
                        'condition_1': bool(current_time_refs),
                        'condition_2': current_time_mode != "none",
                        'condition_3': bool(merged_date_refs),
                        'condition_4': merged_date_mode != "none",
                        'all_conditions': bool(current_time_refs) and current_time_mode != "none" and bool(merged_date_refs) and merged_date_mode != "none"
                    }
                )
        else:
            logger.debug(
                f"Not checking contextual update: user {user_id}",
                extra={
                    'request_id': request_id,
                    'intent': intent,
                    'intent_is_create': intent == "CREATE_BOOKING",
                    'has_resolved_booking_semantics': has_resolved_booking_semantics
                }
            )

        # SEMANTIC layer: Structured DEBUG log (if no merge happened, log current semantic result)
        # Legacy log - downgraded to DEBUG as part of logging refactor
        if not is_continuation:
            resolved_booking = semantic_result.resolved_booking
            logger.debug(
                "SEMANTIC_SNAPSHOT",
                extra={
                    'request_id': request_id,
                    'booking_mode': resolved_booking.get("booking_mode", "service"),
                    'date_mode': resolved_booking.get("date_mode", "none"),
                    'time_mode': resolved_booking.get("time_mode", "none"),
                    'time_constraint': resolved_booking.get("time_constraint")
                }
            )

        # Decision / Policy Layer - ACTIVE
        # Decision layer determines if clarification is needed BEFORE calendar binding
        # Policy operates ONLY on semantic roles, never on raw text or regex
        decision_result = None
        # Initialize semantic_for_decision before try block to ensure it's always defined
        if memory_state and is_active_booking(memory_state) and merged_semantic_result:
            semantic_for_decision = merged_semantic_result.resolved_booking or {}
        else:
            semantic_for_decision = (semantic_result.resolved_booking if semantic_result and hasattr(semantic_result, 'resolved_booking') else {}) or {}
        
        try:
            # Load booking policy from config
            booking_policy = get_booking_policy()

            # CRITICAL INVARIANT: decision must see fully merged semantics
            # If there is an active booking, the semantic object passed to decision
            # MUST be the merged semantic booking, not the current fragment
            # (Already initialized above, but ensure booking_mode is set)
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
            with StageTimer(execution_trace, "decision", request_id=request_id):
                decision_result, decision_trace = decide_booking_status(
                    semantic_for_decision,
                    entities=extraction_result,
                    policy=booking_policy,
                    intent_name=intent_name_for_decision
                )

            # Store decision result in results
            results["stages"]["decision"] = {
                "status": decision_result.status,
                "reason": decision_result.reason,
                "effective_time": decision_result.effective_time
            }
            # Update execution_trace with decision trace (overwrites pipeline's trace with merged semantic result)
            execution_trace.update(decision_trace)

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

        # Post-classification: Detect CONTEXTUAL_UPDATE
        # CONTEXTUAL_UPDATE detection delegated to memory policy
        effective_intent = detect_contextual_update(
            memory_state=memory_state,
            intent=intent,
            text=text,
            merged_semantic_result=merged_semantic_result,
            extraction_result=extraction_result,
            logger=logger,
            user_id=user_id,
            request_id=request_id
        )

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
                binding_intent = effective_intent if effective_intent != CONTEXTUAL_UPDATE else "CREATE_BOOKING"
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

        # CONTEXTUAL_UPDATE: If exact time is provided and memory has date, rebuild datetime_range
        # This ensures exact times override time windows from previous input
        if effective_intent == CONTEXTUAL_UPDATE and memory_state:
            memory_booking = memory_state.get("booking_state", {})
            memory_datetime_range = memory_booking.get("datetime_range")
            current_datetime_range = current_booking.get("datetime_range")

            # Check if semantic result has exact time but calendar binding didn't create datetime_range
            # Use merged_semantic_result to get merged semantics if this was a continuation
            resolved_booking = merged_semantic_result.resolved_booking
            time_mode = resolved_booking.get("time_mode", "none")
            time_refs = resolved_booking.get("time_refs", [])
            date_refs = resolved_booking.get("date_refs", [])

            # If exact time is provided but no date in current input, and memory has date
            if (time_mode == "exact" and time_refs and
                not date_refs and
                memory_datetime_range and
                    not current_datetime_range):
                # Rebuild datetime_range using memory date + new exact time
                # Extract date from memory datetime_range
                try:
                    memory_start = memory_datetime_range.get("start")
                    if memory_start:
                        # Parse memory start datetime to get date
                        memory_dt = datetime.fromisoformat(
                            memory_start.replace("Z", "+00:00"))
                        memory_date = memory_dt.date()

                        # Re-bind time with the memory date
                        # Get timezone from request
                        tz = get_timezone(timezone)
                        now_tz = _localize_datetime(now, tz)

                        # Parse the exact time
                        time_range = bind_times(
                            time_refs, "exact", now_tz, tz, time_windows=None)

                        if time_range:
                            # Create date_range from memory date
                            date_range = {
                                "start_date": memory_date.strftime("%Y-%m-%d"),
                                "end_date": memory_date.strftime("%Y-%m-%d")
                            }

                            # Combine date + exact time into datetime_range
                            # CONTEXTUAL_UPDATE is for appointments, so pass external_intent=None
                            # (reservations don't use CONTEXTUAL_UPDATE)
                            new_datetime_range = combine_datetime_range(
                                date_range, time_range, now_tz, tz, external_intent=None)

                            if new_datetime_range:
                                # Exact time overrides window: set start == end
                                start_str = new_datetime_range.get("start")
                                if start_str:
                                    start_dt = datetime.fromisoformat(
                                        start_str.replace("Z", "+00:00"))
                                    # Set end to same as start (exact time, not window)
                                    current_booking["datetime_range"] = {
                                        "start": start_dt.isoformat(),
                                        "end": start_dt.isoformat()
                                    }
                except Exception as e:  # noqa: BLE001
                    # If rebuilding fails, fall back to normal merge
                    logger.warning(f"Failed to rebuild datetime_range for CONTEXTUAL_UPDATE: {e}",
                                   extra={'request_id': request_id})

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
                    with StageTimer(execution_trace, "memory", request_id=request_id):
                        memory_store.set(
                            user_id=user_id,
                            domain=domain,
                            state=merged_memory,
                            ttl=MEMORY_TTL
                        )

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
                    with StageTimer(execution_trace, "memory", request_id=request_id):
                        memory_store.set(
                            user_id=user_id,
                            domain=domain,
                            state=merged_memory,
                            ttl=MEMORY_TTL
                        )
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
            merged_memory=merged_memory
        )

        # Post-semantic validation guard: Check for orphan slot updates
        # If extracted slots exist but cannot be applied (no booking_id, no draft, no booking),
        # return clarification instead of "successful" empty response
        # Build production response
        # Map CONTEXTUAL_UPDATE to CREATE_BOOKING in API response
        api_intent = "CREATE_BOOKING" if effective_intent == CONTEXTUAL_UPDATE else effective_intent
        intent_payload_name = external_intent if external_intent in {
            "CREATE_APPOINTMENT", "CREATE_RESERVATION"} else api_intent
        intent_payload = {"name": intent_payload_name,
                          "confidence": confidence}

        # Clarification fields from plan_clarification / calendar
        # Return booking state for CREATE_BOOKING, CONTEXTUAL_UPDATE, or MODIFY_BOOKING
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

        processing_time = round((time.time() - start_time) * 1000, 2)

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

        # For CREATE_BOOKING, ensure booking is present only when ready
        if api_intent == "CREATE_BOOKING" and booking_payload is None and not needs_clarification:
            # This should not happen, but ensures contract compliance
            # Build minimal PARTIAL booking from merged semantic result
            resolved_booking = merged_semantic_result.resolved_booking
            services = resolved_booking.get("services", [])
            if not services:
                service_families = _get_business_categories(extraction_result)
                services = [
                    {
                        "text": service.get("text", ""),
                        "canonical": service.get("canonical", "")
                    }
                    for service in service_families
                    if isinstance(service, dict) and service.get("text")
                ]
            booking_payload = {
                "services": services,
                "datetime_range": None,
            }
            logger.warning(
                f"Fallback: Built PARTIAL booking for CREATE_BOOKING when booking_payload was None",
                extra={'request_id': request_id, 'intent': api_intent}
            )

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

            # Reservation responses: only start_date / end_date
            if intent_payload_name == "CREATE_RESERVATION":
                # Always copy start_date and end_date from calendar_booking if present
                # These come from calendar binding and should override any memory values
                if "start_date" in calendar_booking:
                    booking_payload["start_date"] = calendar_booking["start_date"]
                if "end_date" in calendar_booking:
                    booking_payload["end_date"] = calendar_booking["end_date"]
                booking_payload.pop("datetime_range", None)
                booking_payload.pop("date", None)
                booking_payload.pop("time", None)
                booking_payload.pop("date_range", None)
                booking_payload.pop("time_range", None)

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

            issues = _build_issues(missing_slots, time_issues_for_issues)

        response_body = {
            "success": True,
            "intent": intent_payload,
            "status": "needs_clarification" if needs_clarification else "ready",
            "issues": issues if issues else {},
            "clarification_reason": clarification_reason if needs_clarification else None,
            "needs_clarification": needs_clarification,
        }

        if needs_clarification:
            if context_payload:
                response_body["context"] = context_payload
        else:
            if booking_payload is not None:
                # Attach confirmation_state for ready bookings
                booking_payload["confirmation_state"] = "pending"
                # For reservations, provide a datetime_range spanning the stay so date helpers work uniformly
                if intent_payload_name == "CREATE_RESERVATION" and booking_payload.get("start_date") and booking_payload.get("end_date") and not booking_payload.get("datetime_range"):
                    start_iso = f"{booking_payload['start_date']}T00:00:00Z"
                    end_iso = f"{booking_payload['end_date']}T23:59:00Z"
                    booking_payload["datetime_range"] = {
                        "start": start_iso, "end": end_iso}

                response_body["booking"] = booking_payload
                # Expose flat slots for tests/consumers
                slots: Dict[str, Any] = {}
                if booking_payload.get("start_date"):
                    slots["start_date"] = booking_payload.get("start_date")
                if booking_payload.get("end_date"):
                    slots["end_date"] = booking_payload.get("end_date")
                # Check both booking_payload and calendar_booking for datetime_range
                # calendar_booking is the source of truth for calendar binding results
                datetime_range = booking_payload.get("datetime_range") or calendar_booking.get("datetime_range")
                if datetime_range:
                    slots["has_datetime"] = True
                services = booking_payload.get("services") or []
                if services:
                    primary = services[-1] if isinstance(services[-1], dict) else (
                        services[0] if isinstance(services[0], dict) else {})
                    canonical = primary.get("canonical") if isinstance(
                        primary, dict) else None
                    if canonical:
                        slots["service_id"] = canonical.split(".")[-1]
                response_body["slots"] = slots
        # Omit null datetime_range for cleanliness
        if response_body.get("booking") and response_body["booking"].get("datetime_range") is None:
            response_body["booking"].pop("datetime_range", None)

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

