#!/usr/bin/env python3
"""
Luma Service/Reservation Booking REST API

A Flask-based REST API for service/reservation booking processing with support for:
- Entity extraction (services, dates, times)
- Intent resolution
- Structural interpretation
- Appointment grouping
- Semantic resolution
- Calendar binding

Usage:
    python luma/api.py
    
    or
    
    gunicorn -w 4 -b 0.0.0.0:9001 luma.api:app

Endpoints:
    POST /resolve - Process conversational input and resolve intent/state
    POST /book - Deprecated alias for /resolve (will be removed)
    GET /health - Health check
    GET /info - API information
"""
import sys
from pathlib import Path

# Add src/ to path if running directly
if __name__ == "__main__":
    src_path = Path(__file__).parent.parent
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

import time  # noqa: E402
from typing import Dict, Any, Optional  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from flask import Flask, request, jsonify, g  # noqa: E402
from luma.calendar.calendar_binder import bind_calendar, _bind_times, _combine_datetime_range, _get_timezone  # noqa: E402
from luma.resolution.semantic_resolver import resolve_semantics  # noqa: E402
from luma.grouping.appointment_grouper import group_appointment  # noqa: E402
from luma.structure.interpreter import interpret_structure  # noqa: E402
from luma.grouping.reservation_intent_resolver import ReservationIntentResolver  # noqa: E402
from luma.extraction.matcher import EntityMatcher  # noqa: E402
from luma.config import config  # noqa: E402
from luma.logging_config import setup_logging, generate_request_id  # noqa: E402
from luma.memory import RedisMemoryStore  # noqa: E402
from luma.memory.merger import merge_booking_state, extract_memory_state_for_response  # noqa: E402
from luma.clarification import ClarificationReason  # noqa: E402

# Internal intent (never returned in API, never persisted)
CONTEXTUAL_UPDATE = "CONTEXTUAL_UPDATE"

# Apply config settings
PORT = config.API_PORT

# Flask app
app = Flask(__name__)

# Setup logging
logger = setup_logging(
    app_name='luma-api',
    log_level=config.LOG_LEVEL,
    log_format=config.LOG_FORMAT,
    log_file=config.LOG_FILE
)

# Global pipeline components
entity_matcher = None
intent_resolver = None
memory_store = None


# Request tracking middleware
@app.before_request
def before_request():
    """Track request start time and generate request ID."""
    g.start_time = time.time()
    g.request_id = request.headers.get('X-Request-ID', generate_request_id())


@app.after_request
def after_request(response):
    """Log request completion with timing and status."""
    if hasattr(g, 'start_time') and config.ENABLE_REQUEST_LOGGING:
        duration_ms = round((time.time() - g.start_time) * 1000, 2)

        # Create structured log
        log_record = logger.makeRecord(
            logger.name,
            20,  # INFO level
            '',
            0,
            f'{request.method} {request.path} {response.status_code}',
            (),
            None
        )
        log_record.request_id = g.request_id
        log_record.method = request.method
        log_record.path = request.path
        log_record.status_code = response.status_code
        log_record.duration_ms = duration_ms

        logger.handle(log_record)

    # Add request ID to response headers
    if hasattr(g, 'request_id'):
        response.headers['X-Request-ID'] = g.request_id

    return response


def find_normalization_dir():
    """Find the normalization directory."""
    current_file = Path(__file__).resolve()
    store_dir = current_file.parent / "store" / "normalization"
    if store_dir.exists():
        return store_dir
    src_dir = current_file.parent.parent
    intents_norm = src_dir / "intents" / "normalization"
    if intents_norm.exists():
        return intents_norm
    return None


def _count_mutable_slots_modified(
    semantic_result: Any,  # noqa: ARG001
    extraction_result: Dict[str, Any]
) -> int:
    """
    Count how many mutable slots (date, time, duration) are modified in the input.

    Returns:
        Number of mutable slots modified (0-3)
    """
    count = 0

    # Check for date modifications
    dates = extraction_result.get("dates", [])
    dates_absolute = extraction_result.get("dates_absolute", [])
    if dates or dates_absolute:
        count += 1

    # Check for time modifications
    times = extraction_result.get("times", [])
    time_windows = extraction_result.get("time_windows", [])
    if times or time_windows:
        count += 1

    # Check for duration modifications
    durations = extraction_result.get("durations", [])
    if durations:
        count += 1

    return count


def _has_booking_verb(text: str) -> bool:
    """
    Check if text contains booking verbs.

    Booking verbs: book, schedule, reserve, appointment, appoint, set, arrange, plan, make
    """
    text_lower = text.lower()
    booking_verbs = {
        "book", "schedule", "reserve", "appointment", "appoint",
        "set", "arrange", "plan", "make"
    }
    return any(verb in text_lower for verb in booking_verbs)


def _localize_datetime(dt: datetime, timezone: str) -> datetime:
    """Localize datetime to timezone."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(timezone)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    except Exception:
        try:
            import pytz
            tz = pytz.timezone(timezone)
            if dt.tzinfo is None:
                return tz.localize(dt)
            return dt.astimezone(tz)
        except Exception:
            return dt


def init_pipeline():
    """Initialize the pipeline components."""
    global entity_matcher, intent_resolver, memory_store  # noqa: PLW0603

    logger.info("=" * 60)
    logger.info("Initializing Luma Service/Reservation Booking Pipeline")

    try:
        # Initialize intent resolver (lightweight, no file I/O)
        intent_resolver = ReservationIntentResolver()

        # Initialize memory store
        try:
            memory_store = RedisMemoryStore()
            logger.info("Memory store initialized successfully")
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"Memory store initialization failed: {e}. Memory will not persist.")
            memory_store = None

        # Entity matcher will be initialized per-request with entity file
        logger.info("Pipeline components initialized successfully")
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to initialize pipeline: {e}", exc_info=True)
        return False


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    if entity_matcher is None and intent_resolver is None:
        return jsonify({
            "status": "unhealthy",
            "message": "Pipeline components not initialized"
        }), 503

    return jsonify({
        "status": "healthy",
        "components": {
            "intent_resolver": intent_resolver is not None,
            "entity_matcher": "lazy_initialized"
        }
    })


@app.route("/info", methods=["GET"])
def info():
    """API information endpoint."""
    return jsonify({
        "name": "Luma Service/Reservation Booking API",
        "version": "1.0.0",
        "description": "Service and reservation booking processing with entity extraction, intent resolution, semantic resolution, and calendar binding",
        "endpoints": {
            "/resolve": {
                "method": "POST",
                "description": "Process conversational input and resolve intent/state",
                "parameters": {
                    "user_id": "string (required) - User identifier",
                    "text": "string (required) - Conversational input text",
                    "domain": "string (optional) - 'service' or 'reservation' (default: 'service')",
                    "timezone": "string (optional) - Timezone for calendar binding (default: 'UTC')",
                }
            },
            "/book": {
                "method": "POST",
                "description": "Deprecated: Use /resolve instead",
                "deprecated": True
            },
            "/health": {
                "method": "GET",
                "description": "Health check"
            },
            "/info": {
                "method": "GET",
                "description": "API information"
            }
        },
        "configuration": {
            "port": PORT,
        }
    })


@app.route("/resolve", methods=["POST"])
def resolve():
    """
    Process conversational input and resolve intent/state.

    Request body:
    {
        "user_id": "user123",          // required
        "text": "book haircut tomorrow at 2pm",
        "domain": "service",           // optional, default: "service"
        "timezone": "UTC"              // optional, default: "UTC"
    }

    Response:
    {
        "success": true,
        "intent": {"name": "CREATE_BOOKING", "confidence": "high"},
        "needs_clarification": false,
        "clarification": null,
        "booking": {
            "services": [...],
            "datetime_range": {...},
            "duration": null
        }
    }
    """
    request_id = g.request_id if hasattr(g, 'request_id') else 'unknown'

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

        if not text or not isinstance(text, str):
            logger.warning("Invalid text parameter", extra={
                           'request_id': request_id})
            return jsonify({
                "success": False,
                "error": "'text' must be a non-empty string"
            }), 400

        # Load memory state
        memory_state = None
        if memory_store:
            try:
                memory_state = memory_store.get(user_id, domain)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to load memory: {e}", extra={
                               'request_id': request_id})

        # Log request
        logger.info(
            "Processing conversational input",
            extra={
                'request_id': request_id,
                'user_id': user_id,
                'text_length': len(text),
                'domain': domain,
                'timezone': timezone,
                'has_memory': memory_state is not None
            }
        )

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

        # Stage 1: Entity Extraction
        try:
            matcher = EntityMatcher(domain=domain, entity_file=entity_file)
            extraction_result = matcher.extract_with_parameterization(text)
            results["stages"]["extraction"] = extraction_result
        except Exception as e:
            results["stages"]["extraction"] = {"error": str(e)}
            return jsonify({"success": False, "data": results}), 500

        # Stage 2: Intent Resolution
        try:
            intent, confidence = intent_resolver.resolve_intent(
                text, extraction_result)
            results["stages"]["intent"] = {
                "intent": intent, "confidence": confidence}
        except Exception as e:
            results["stages"]["intent"] = {"error": str(e)}
            return jsonify({"success": False, "data": results}), 500

        # Stage 3: Structural Interpretation
        try:
            psentence = extraction_result.get('psentence', '')
            structure = interpret_structure(psentence, extraction_result)
            results["stages"]["structure"] = structure.to_dict()["structure"]
        except Exception as e:
            results["stages"]["structure"] = {"error": str(e)}
            return jsonify({"success": False, "data": results}), 500

        # Stage 4: Appointment Grouping
        try:
            grouped_result = group_appointment(extraction_result, structure)
            results["stages"]["grouping"] = grouped_result
        except Exception as e:
            results["stages"]["grouping"] = {"error": str(e)}
            return jsonify({"success": False, "data": results}), 500

        # Stage 5: Semantic Resolution
        try:
            semantic_result = resolve_semantics(
                grouped_result, extraction_result)
            results["stages"]["semantic"] = semantic_result.to_dict()
        except Exception as e:
            results["stages"]["semantic"] = {"error": str(e)}
            return jsonify({"success": False, "data": results}), 500

        # Post-classification: Detect CONTEXTUAL_UPDATE
        # CONTEXTUAL_UPDATE is internal only - never returned in API
        effective_intent = intent
        if (intent == "MODIFY_BOOKING" or intent == "UNKNOWN") and memory_state:
            # Check if this is a contextual update to existing CREATE_BOOKING draft
            if memory_state.get("intent") == "CREATE_BOOKING":
                # Check if at least one mutable slot is modified (date, time, or duration)
                # Allows single or multiple slot updates (e.g., "Wednesday at 5pm")
                mutable_slots_modified = _count_mutable_slots_modified(
                    semantic_result, extraction_result
                )
                # Check if no service or booking verb is present
                has_service = len(extraction_result.get(
                    "service_families", [])) > 0
                has_booking_verb = _has_booking_verb(text)

                # Safety: No booking_id, no service, no booking verb, and at least one mutable slot
                # IMPORTANT: Apply CONTEXTUAL_UPDATE even if intent is UNKNOWN when time/date/duration is extracted
                # This handles cases like "6p," where normalization fixes the time but intent resolver returns UNKNOWN
                if mutable_slots_modified >= 1 and not has_service and not has_booking_verb:
                    effective_intent = CONTEXTUAL_UPDATE
                    # Override intent for processing, but keep original for logging
                    logger.info(
                        f"Detected CONTEXTUAL_UPDATE for user {user_id}",
                        extra={'request_id': request_id,
                               'original_intent': intent,
                               'slots_modified': mutable_slots_modified}
                    )

        # Stage 6: Calendar Binding
        # Use effective_intent for calendar binding (CONTEXTUAL_UPDATE treated as CREATE_BOOKING)
        binding_intent = effective_intent if effective_intent != CONTEXTUAL_UPDATE else "CREATE_BOOKING"
        try:
            calendar_result = bind_calendar(
                semantic_result,
                now,
                timezone,
                intent=binding_intent,
                entities=extraction_result
            )
            results["stages"]["calendar"] = calendar_result.to_dict()
        except Exception as e:
            results["stages"]["calendar"] = {"error": str(e)}
            return jsonify({"success": False, "data": results}), 500

        # Determine debug mode (query param debug=1|true|yes)
        debug_flag = str(request.args.get("debug", "0")).lower()
        debug_mode = debug_flag in {"1", "true", "yes"}

        # Extract current booking state from calendar result
        calendar_dict = calendar_result.to_dict()
        calendar_booking = calendar_dict.get(
            "calendar_booking", {}) if calendar_dict else {}

        # Extract current clarification
        current_clarification = None
        if calendar_result.needs_clarification and calendar_result.clarification:
            current_clarification = calendar_result.clarification.to_dict()
        elif semantic_result.needs_clarification and semantic_result.clarification:
            current_clarification = semantic_result.clarification.to_dict()

        # Prepare current booking state (only canonical fields)
        # Include date_range and time_range for merge logic to handle time-only updates
        current_booking = {
            "services": calendar_booking.get("services", []),
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
            resolved_booking = semantic_result.resolved_booking
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
                        tz = _get_timezone(timezone)
                        now_tz = _localize_datetime(now, tz)

                        # Parse the exact time
                        time_range = _bind_times(
                            time_refs, "exact", now_tz, tz, time_windows=None)

                        if time_range:
                            # Create date_range from memory date
                            date_range = {
                                "start_date": memory_date.strftime("%Y-%m-%d"),
                                "end_date": memory_date.strftime("%Y-%m-%d")
                            }

                            # Combine date + exact time into datetime_range
                            new_datetime_range = _combine_datetime_range(
                                date_range, time_range, now_tz, tz)

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

        # Validate MODIFY_BOOKING: requires booking_id or existing CREATE_BOOKING draft
        if effective_intent == "MODIFY_BOOKING":
            # Check if booking_id is provided (for confirmed bookings)
            booking_id = data.get("booking_id")
            has_draft = memory_state and memory_state.get(
                "intent") == "CREATE_BOOKING"

            if not booking_id and not has_draft:
                # MODIFY_BOOKING without booking_id or draft → clarification
                current_clarification = {
                    "reason": ClarificationReason.MISSING_BOOKING_REFERENCE.value,
                    "data": {}
                }
                # Don't merge or persist, just return clarification
                response_body = {
                    "success": True,
                    "intent": {"name": "MODIFY_BOOKING", "confidence": confidence},
                    "needs_clarification": True,
                    "clarification": current_clarification,
                    "booking": None,
                }
                if debug_mode:
                    response_body["debug"] = results
                return jsonify(response_body)

        # Clear memory if intent is cancel/confirm/commit
        should_clear_memory = intent in {
            "CANCEL_BOOKING", "CONFIRM_BOOKING", "COMMIT_BOOKING"}
        if should_clear_memory and memory_store:
            try:
                memory_store.clear(user_id, domain)
                logger.info(f"Cleared memory for {user_id} due to intent: {intent}", extra={
                            'request_id': request_id})
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to clear memory: {e}", extra={
                               'request_id': request_id})

        # CONTEXTUAL_UPDATE merges into CREATE_BOOKING draft
        # MODIFY_BOOKING with booking_id is a real workflow (persist as MODIFY_BOOKING)
        # Only CREATE_BOOKING and MODIFY_BOOKING (with booking_id) persist to memory
        is_booking_intent = effective_intent == "CREATE_BOOKING" or effective_intent == CONTEXTUAL_UPDATE
        is_modify_with_booking_id = effective_intent == "MODIFY_BOOKING" and data.get(
            "booking_id") is not None

        if not should_clear_memory and is_booking_intent:
            # CONTEXTUAL_UPDATE merges into CREATE_BOOKING draft
            # Persist with intent = CREATE_BOOKING (never persist CONTEXTUAL_UPDATE)
            persist_intent = "CREATE_BOOKING"
            merged_memory = merge_booking_state(
                memory_state=memory_state,
                current_intent=persist_intent,  # Always persist as CREATE_BOOKING
                current_booking=current_booking,
                current_clarification=current_clarification
            )

            # Persist merged state with CREATE_BOOKING intent
            if memory_store:
                try:
                    memory_store.set(
                        user_id=user_id,
                        domain=domain,
                        state=merged_memory,
                        ttl=config.MEMORY_TTL
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Failed to persist memory: {e}", extra={
                                   'request_id': request_id})
        elif not should_clear_memory and is_modify_with_booking_id:
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
                    memory_store.set(
                        user_id=user_id,
                        domain=domain,
                        state=merged_memory,
                        ttl=config.MEMORY_TTL
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Failed to persist memory: {e}", extra={
                                   'request_id': request_id})
        elif not should_clear_memory and memory_state:
            # For non-booking intents, keep existing memory but don't update it
            merged_memory = memory_state
        else:
            # If clearing or no memory, use current state only (no merge)
            merged_memory = {
                "intent": effective_intent if effective_intent != CONTEXTUAL_UPDATE else "CREATE_BOOKING",
                "booking_state": current_booking if (is_booking_intent or is_modify_with_booking_id) else {},
                "clarification": current_clarification,
                "last_updated": datetime.now(timezone.utc).isoformat()
            }

        # Build production response
        # Map CONTEXTUAL_UPDATE to CREATE_BOOKING in API response
        api_intent = "CREATE_BOOKING" if effective_intent == CONTEXTUAL_UPDATE else effective_intent
        intent_payload = {"name": api_intent, "confidence": confidence}

        # Extract clarification from merged state
        merged_clarification = merged_memory.get("clarification")
        needs_clarification = merged_clarification is not None

        # Return booking state for CREATE_BOOKING, CONTEXTUAL_UPDATE, or MODIFY_BOOKING
        booking_payload = None
        if (is_booking_intent or is_modify_with_booking_id) and not needs_clarification:
            booking_payload = extract_memory_state_for_response(merged_memory)
        elif memory_state and memory_state.get("intent") == "CREATE_BOOKING" and not needs_clarification:
            # Return existing booking state from memory for follow-ups
            booking_payload = extract_memory_state_for_response(memory_state)

        # Extract entities for non-booking intents (DISCOVERY, QUOTE, DETAILS, etc.)
        # CREATE_BOOKING and MODIFY_BOOKING should not include entities field
        entities_payload = None
        is_modify_booking = intent == "MODIFY_BOOKING"
        if not is_booking_intent and not is_modify_booking:
            # Extract services from extraction result
            service_families = extraction_result.get("service_families", [])
            # Always include entities field for non-booking intents
            entities_payload = {}
            if service_families:
                # Format services with text and canonical (same format as booking.services)
                entities_payload["services"] = [
                    {
                        "text": service.get("text", ""),
                        "canonical": service.get("canonical", "")
                    }
                    for service in service_families
                    if isinstance(service, dict) and service.get("text")
                ]

        processing_time = round((time.time() - start_time) * 1000, 2)

        # Log successful processing
        if config.LOG_PERFORMANCE_METRICS:
            logger.info(
                "Conversational input processed successfully",
                extra={
                    'request_id': request_id,
                    'processing_time_ms': processing_time,
                    'needs_clarification': needs_clarification,
                    'intent': intent
                }
            )

        response_body = {
            "success": True,
            "intent": intent_payload,
            "needs_clarification": needs_clarification,
            "clarification": merged_clarification if needs_clarification else None,
            "booking": booking_payload if not needs_clarification else None,
        }

        # Add entities field for non-booking intents (always include, even if empty)
        if entities_payload is not None:
            response_body["entities"] = entities_payload

        # Attach full internal pipeline data only in debug mode
        if debug_mode:
            response_body["debug"] = results

        return jsonify(response_body)

    except Exception as e:  # noqa: BLE001
        logger.error(
            f"Processing failed: {str(e)}",
            extra={
                'request_id': request_id,
                'error_type': type(e).__name__,
                'text_length': len(text)
            },
            exc_info=True
        )
        return jsonify({
            "success": False,
            "error": f"Processing failed: {str(e)}"
        }), 500


# TODO: Remove /book endpoint in future version - use /resolve instead
@app.route("/book", methods=["POST"])
def book():
    """
    Deprecated: Use /resolve instead.

    This endpoint is kept for backward compatibility but will be removed in a future version.
    """
    return resolve()


@app.errorhandler(404)
def not_found(error):  # noqa: ARG001, pylint: disable=unused-argument
    """Handle 404 errors."""
    return jsonify({
        "success": False,
        "error": "Endpoint not found",
        "available_endpoints": ["/resolve", "/book (deprecated)", "/health", "/info"]
    }), 404


@app.errorhandler(500)
def internal_error(error):  # noqa: ARG001, pylint: disable=unused-argument
    """Handle 500 errors."""
    return jsonify({
        "success": False,
        "error": "Internal server error"
    }), 500


def main():
    """Run the Flask development server."""
    logger.info("=" * 60)
    logger.info("Luma Service/Reservation Booking API")
    logger.info(f"Starting server on http://localhost:{PORT}")
    logger.info("=" * 60)

    # Initialize pipeline
    if not init_pipeline():
        logger.error("Failed to start API - pipeline initialization failed")
        sys.exit(1)

    logger.info(f"API ready! Listening on port {PORT}")
    logger.info(
        f"Try: curl -X POST http://localhost:{PORT}/resolve -H 'Content-Type: application/json' -d '{{\"user_id\": \"user123\", \"text\": \"book haircut tomorrow at 2pm\"}}'")

    # Run Flask
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False
    )


if __name__ == "__main__":
    main()
