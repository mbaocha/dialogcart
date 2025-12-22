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
from typing import Dict, Any, Optional, List  # noqa: E402
from datetime import datetime, timezone as dt_timezone  # noqa: E402
from flask import Flask, request, jsonify, g  # noqa: E402
from luma.calendar.calendar_binder import bind_calendar, _bind_times, _combine_datetime_range, _get_timezone, _get_booking_policy  # noqa: E402
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
from luma.decision import decide_booking_status  # noqa: E402

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

# Structured stage logger
def _log_stage(logger, request_id: str, stage: str, input_data=None, output_data=None, notes=None, duration_ms=None, level="info"):
    """Log a pipeline stage with structured format."""
    # Create a meaningful message for the log
    message = f"stage={stage}"
    if duration_ms is not None:
        message += f", duration_ms={duration_ms}"
    
    payload = {
        "request_id": request_id,
        "stage": stage,
    }
    if input_data is not None:
        payload["input"] = input_data
    if output_data is not None:
        payload["output"] = output_data
    if notes is not None:
        payload["notes"] = notes
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    log_fn = logger.debug if level == "debug" else logger.info
    log_fn(message, extra=payload)


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


def _format_service_for_response(service: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format a service dict for API response, preserving resolved alias if present.
    
    When a tenant alias was explicitly matched, use it for the text field.
    Otherwise, use the original text field.
    
    Args:
        service: Service dict with text, canonical, and optionally resolved_alias
        
    Returns:
        Formatted service dict with text and canonical
    """
    # If resolved_alias exists (from explicit tenant alias match), use it
    resolved_alias = service.get("resolved_alias")
    if resolved_alias:
        return {
            "text": resolved_alias,
            "canonical": service.get("canonical", "")
        }
    
    # Otherwise, use existing text field
    return {
        "text": service.get("text", ""),
        "canonical": service.get("canonical", "")
    }


def _get_business_categories(extraction_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get business_categories from extraction result with backward compatibility.
    Supports both new 'business_categories' key and legacy 'service_families' key.
    """
    return extraction_result.get("business_categories") or extraction_result.get("service_families", [])


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


def _is_partial_booking(memory_state: Optional[Dict[str, Any]]) -> bool:
    """
    Check if memory contains a PARTIAL booking draft.

    A PARTIAL booking is one that:
    - Has intent == "CREATE_BOOKING"
    - Has clarification (needs_clarification) OR booking_state == "PARTIAL"
    """
    if not memory_state:
        return False

    if memory_state.get("intent") != "CREATE_BOOKING":
        return False

    # Check for clarification (indicates PARTIAL)
    if memory_state.get("clarification") is not None:
        return True

    # Check booking_state if stored
    booking_state = memory_state.get("booking_state", {})
    if isinstance(booking_state, dict) and booking_state.get("booking_state") == "PARTIAL":
        return True

    return False


def _has_active_booking(memory_state: Optional[Dict[str, Any]]) -> bool:
    """
    Check if memory contains an active booking (PARTIAL or RESOLVED).

    An active booking is one that:
    - Has intent == "CREATE_BOOKING"
    - Has booking_state == "PARTIAL" or "RESOLVED" OR has clarification (PARTIAL)
    """
    if not memory_state:
        return False

    if memory_state.get("intent") != "CREATE_BOOKING":
        return False

    # Check for clarification (indicates PARTIAL)
    if memory_state.get("clarification") is not None:
        return True

    # Check booking_state if stored
    booking_state = memory_state.get("booking_state", {})
    if isinstance(booking_state, dict):
        booking_state_value = booking_state.get("booking_state")
        if booking_state_value in ("PARTIAL", "RESOLVED"):
            return True

    return False


def _merge_semantic_results(
    memory_booking: Dict[str, Any],
    current_resolved_booking: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Merge semantic results from memory and current input.

    Rules:
    - SERVICES: Keep from memory if present, otherwise use current
    - DATE: Keep from memory if present, otherwise use current
    - TIME: Use current if present, otherwise keep from memory
    - DURATION: Use current if present, otherwise keep from memory
    - TIME_CONSTRAINT: Use current if present, otherwise keep from memory

    This preserves existing fields and fills missing ones only.
    """
    merged = {}

    # SERVICES: Prefer memory (existing booking), fallback to current
    # CRITICAL: If current has no services, preserve memory services (services are sticky)
    memory_services = memory_booking.get("services", [])
    current_services = current_resolved_booking.get("services", [])
    if current_services:
        # Current input has services → use current (explicit change)
        merged["services"] = current_services
    else:
        # Current input has no services → preserve memory services (sticky)
        merged["services"] = memory_services if memory_services else []

    # DATE: Prefer memory if present, otherwise use current (promote current if memory is empty)
    # CRITICAL: If memory has NO date and current provides date, promote current date
    # This enables "time only" → "date only" → RESOLVED flow
    memory_date_mode = memory_booking.get("date_mode", "none")
    memory_date_refs = memory_booking.get("date_refs", [])
    current_date_mode = current_resolved_booking.get("date_mode", "none")
    current_date_refs = current_resolved_booking.get("date_refs", [])

    if memory_date_refs and memory_date_mode != "none":
        # Memory has date → keep memory date
        merged["date_mode"] = memory_date_mode
        merged["date_refs"] = memory_date_refs
        merged["date_modifiers"] = memory_booking.get("date_modifiers", [])
    elif current_date_refs and current_date_mode != "none":
        # Memory has no date, current has date → promote current date
        merged["date_mode"] = current_date_mode
        merged["date_refs"] = current_date_refs
        merged["date_modifiers"] = current_resolved_booking.get(
            "date_modifiers", [])
    else:
        # Neither has date
        merged["date_mode"] = "none"
        merged["date_refs"] = []
        merged["date_modifiers"] = []

    # TIME: Prefer current (new input), fallback to memory
    memory_time_mode = memory_booking.get("time_mode", "none")
    memory_time_refs = memory_booking.get("time_refs", [])
    current_time_mode = current_resolved_booking.get("time_mode", "none")
    current_time_refs = current_resolved_booking.get("time_refs", [])

    if current_time_refs and current_time_mode != "none":
        merged["time_mode"] = current_time_mode
        merged["time_refs"] = current_time_refs
    elif memory_time_refs and memory_time_mode != "none":
        merged["time_mode"] = memory_time_mode
        merged["time_refs"] = memory_time_refs
    else:
        merged["time_mode"] = "none"
        merged["time_refs"] = []

    # TIME_CONSTRAINT: Prefer current, fallback to memory
    current_time_constraint = current_resolved_booking.get("time_constraint")
    memory_time_constraint = memory_booking.get("time_constraint")
    merged["time_constraint"] = current_time_constraint if current_time_constraint else memory_time_constraint

    # DURATION: Prefer current, fallback to memory
    current_duration = current_resolved_booking.get("duration")
    memory_duration = memory_booking.get("duration")
    merged["duration"] = current_duration if current_duration is not None else memory_duration

    return merged


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
        tenant_context = data.get("tenant_context")  # Optional tenant context with aliases
        
        # Log tenant_context for debugging
        if tenant_context:
            aliases_count = len(tenant_context.get("aliases", {})) if isinstance(tenant_context.get("aliases"), dict) else 0
            logger.info(
                f"Received tenant_context with {aliases_count} aliases",
                extra={'request_id': request_id, 'aliases_count': aliases_count}
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
        if memory_store:
            try:
                memory_state = memory_store.get(user_id, domain)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to load memory: {e}", extra={
                               'request_id': request_id})

        # Stage 1: INPUT - Log request entry
        _log_stage(
            logger, request_id, "input",
            input_data={"raw_text": text, "domain": domain, "timezone": timezone},
            output_data=None,
            notes=None
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

        # Stage 2: TOKENIZATION & ENTITY EXTRACTION
        try:
            stage_start = time.time()
            matcher = EntityMatcher(domain=domain, entity_file=entity_file)
            extraction_result = matcher.extract_with_parameterization(
                text, request_id=request_id, tenant_aliases=tenant_context.get("aliases") if tenant_context else None
            )
            stage_duration = round((time.time() - stage_start) * 1000, 2)
            results["stages"]["extraction"] = extraction_result
            
            # Stage 2: TOKENIZATION - Log token list only
            tokens = extraction_result.get("_tokens", [])
            if tokens:
                _log_stage(
                    logger, request_id, "tokenization",
                    input_data={"text": text},
                    output_data={"tokens": tokens},
                    duration_ms=stage_duration
                )
            
            # Stage 3: ENTITY EXTRACTION - Log extracted entities grouped by type
            entities_by_type = {
                "services": _get_business_categories(extraction_result),
                "dates": extraction_result.get("dates", []) + extraction_result.get("dates_absolute", []),
                "times": extraction_result.get("times", []),
                "durations": extraction_result.get("durations", [])
            }
            _log_stage(
                logger, request_id, "entity_extraction",
                input_data={"text": text},
                output_data=entities_by_type,
                duration_ms=stage_duration
            )
            
            # Stage 4: PARAMETERIZATION PHASE 1 - Service/tenant alias replacement
            phase1_replacements = extraction_result.get("_phase1_replacements", [])
            if phase1_replacements:
                phase1_sentence = extraction_result.get("osentence", "")
                _log_stage(
                    logger, request_id, "parameterization.phase1",
                    input_data={"sentence": phase1_sentence},
                    output_data={
                        "replacements": phase1_replacements,
                        "resulting_sentence": phase1_sentence
                    },
                    notes="Service/tenant alias replacement"
                )
            
            # Stage 5: PARAMETERIZATION PHASE 2 - Date/time replacement
            phase2_replacements = extraction_result.get("_phase2_replacements", [])
            final_psentence = extraction_result.get("psentence", "")
            if phase2_replacements or final_psentence:
                _log_stage(
                    logger, request_id, "parameterization.phase2",
                    input_data={"sentence": extraction_result.get("osentence", "")},
                    output_data={
                        "date_replacements": [r for r in phase2_replacements if r.get("type") == "date"],
                        "time_replacements": [r for r in phase2_replacements if r.get("type") == "time"],
                        "final_parameterized_sentence": final_psentence
                    },
                    notes="Date/time token replacement"
                )
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

        # CRITICAL: Normalize intent for active booking continuations (PARTIAL or RESOLVED)
        # If an active booking exists, force intent to CREATE_BOOKING regardless of raw classification
        # This ensures merge logic always runs for continuations like "at 10" or "make it 10"
        if _has_active_booking(memory_state):
            original_intent = intent
            intent = "CREATE_BOOKING"
            logger.info(
                f"Normalized intent to CREATE_BOOKING for active booking continuation: user {user_id}",
                extra={
                    'request_id': request_id,
                    'original_intent': original_intent,
                    'normalized_intent': intent,
                    'is_partial': _is_partial_booking(memory_state),
                    'is_resolved': memory_state.get("booking_state", {}).get("booking_state") == "RESOLVED" if memory_state and isinstance(memory_state.get("booking_state"), dict) else False
                }
            )
            # Update results to reflect normalization
            results["stages"]["intent"]["original_intent"] = original_intent
            results["stages"]["intent"]["intent"] = intent
            results["stages"]["intent"]["normalized_for_active_booking"] = True

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

        # Stage 6: Semantic Resolution
        try:
            stage_start = time.time()
            semantic_result = resolve_semantics(
                grouped_result, extraction_result, tenant_context=tenant_context)
            stage_duration = round((time.time() - stage_start) * 1000, 2)
            results["stages"]["semantic"] = semantic_result.to_dict()
            
            # Stage 6: SEMANTIC RESOLUTION - Log resolved semantic values
            resolved_booking = semantic_result.resolved_booking
            _log_stage(
                logger, request_id, "semantic_resolution",
                input_data={"grouped_result": grouped_result},
                output_data={
                    "service_family": [s.get("text", "") for s in resolved_booking.get("services", []) if isinstance(s, dict)],
                    "date": resolved_booking.get("date_refs", []),
                    "time": resolved_booking.get("time_refs", []),
                    "confidence": "high" if resolved_booking.get("services") else "low"
                },
                duration_ms=stage_duration
            )
        except Exception as e:
            results["stages"]["semantic"] = {"error": str(e)}
            return jsonify({"success": False, "data": results}), 500

        # Check for PARTIAL booking continuation
        # Continuation detection: flag continuation and load draft semantics for merge
        # Continuation does NOT determine outcome - only merged completeness determines booking_state
        # Initialize merged_semantic_result to semantic_result (will be updated if continuation detected)
        merged_semantic_result = semantic_result
        is_continuation = False
        if intent == "CREATE_BOOKING" and _has_active_booking(memory_state):
            # This is a continuation of an active booking (PARTIAL or RESOLVED)
            is_continuation = True
            memory_booking_state = memory_state.get("booking_state", {})
            # Determine if this is a PARTIAL or RESOLVED booking for logging
            booking_state_value = memory_booking_state.get(
                "booking_state") if isinstance(memory_booking_state, dict) else None
            is_resolved_continuation = booking_state_value == "RESOLVED"

            # Extract resolved_booking from memory (if stored) or reconstruct from booking_state
            # Memory stores booking_state with services, datetime_range, etc.
            # We also store resolved_booking_semantics if available for proper merging
            memory_resolved_booking = memory_state.get(
                "resolved_booking_semantics", {})

            # If not stored, reconstruct from booking_state
            if not memory_resolved_booking:
                memory_resolved_booking = {}
                # Services from memory
                memory_services = memory_booking_state.get("services", [])
                if memory_services:
                    memory_resolved_booking["services"] = memory_services
                # Note: date/time refs not available from booking_state alone
                # We'll rely on current input to fill them

            # Diagnostic: Log what was loaded from memory
            logger.info(
                f"Loaded memory resolved_booking for continuation: user {user_id}",
                extra={
                    'request_id': request_id,
                    'has_resolved_booking_semantics': bool(memory_resolved_booking),
                    'memory_date_refs': memory_resolved_booking.get('date_refs', []),
                    'memory_date_mode': memory_resolved_booking.get('date_mode', 'none'),
                    'memory_time_refs': memory_resolved_booking.get('time_refs', []),
                    'memory_time_mode': memory_resolved_booking.get('time_mode', 'none'),
                    'memory_services': len(memory_resolved_booking.get('services', []))
                }
            )

            # Merge semantic results: preserve memory, fill with current
            merged_resolved_booking = _merge_semantic_results(
                memory_resolved_booking,
                semantic_result.resolved_booking
            )

            # Diagnostic: Log merged result
            logger.info(
                f"Merged resolved_booking: user {user_id}",
                extra={
                    'request_id': request_id,
                    'merged_date_refs': merged_resolved_booking.get('date_refs', []),
                    'merged_date_mode': merged_resolved_booking.get('date_mode', 'none'),
                    'merged_time_refs': merged_resolved_booking.get('time_refs', []),
                    'merged_time_mode': merged_resolved_booking.get('time_mode', 'none'),
                    'merged_services': len(merged_resolved_booking.get('services', []))
                }
            )

            # Create merged semantic result
            # CRITICAL: Preserve semantic clarifications (e.g., SERVICE_VARIANT) from original semantic_result
            # These must not be lost during merge
            from luma.resolution.semantic_resolver import SemanticResolutionResult
            merged_needs_clarification = semantic_result.needs_clarification
            merged_clarification = semantic_result.clarification
            
            # Only override if the original didn't have clarification
            # Semantic clarifications (like SERVICE_VARIANT) take precedence
            if not merged_needs_clarification or not merged_clarification:
                # Will be re-evaluated by decision layer if no semantic clarification exists
                merged_needs_clarification = False
                merged_clarification = None
            
            merged_semantic_result = SemanticResolutionResult(
                resolved_booking=merged_resolved_booking,
                needs_clarification=merged_needs_clarification,
                clarification=merged_clarification
            )


            # Store merged result in debug output
            results["stages"]["semantic_merged"] = {
                "original": semantic_result.to_dict(),
                "merged": merged_semantic_result.to_dict(),
                "is_continuation": True
            }

            logger.info(
                f"Detected {'RESOLVED' if is_resolved_continuation else 'PARTIAL'} booking continuation for user {user_id}",
                extra={
                    'request_id': request_id,
                    'original_intent': intent,
                    'booking_state': booking_state_value,
                    'is_resolved_continuation': is_resolved_continuation,
                    'merged_services': len(merged_resolved_booking.get("services", [])),
                    'merged_date_refs': len(merged_resolved_booking.get("date_refs", [])),
                    'merged_time_refs': len(merged_resolved_booking.get("time_refs", []))
                }
            )

        # NEW: Persist service-only and service+date drafts as active booking drafts
        # This ensures multi-turn slot filling works (service → date → time)
        # Check the merged result (or current if no merge happened)
        resolved_booking_for_draft_check = merged_semantic_result.resolved_booking
        if resolved_booking_for_draft_check:
            has_service = bool(
                resolved_booking_for_draft_check.get("services"))
            has_date = bool(
                resolved_booking_for_draft_check.get("date_refs") or
                resolved_booking_for_draft_check.get("date_range")
            )
            has_time = bool(
                resolved_booking_for_draft_check.get("time_refs") or
                resolved_booking_for_draft_check.get("time_range") or
                resolved_booking_for_draft_check.get("time_constraint")
            )

            # Case 1: Service-only draft (persist for continuation)
            if has_service and not has_date and not has_time:
                # Ensure memory_state exists and is properly structured for persistence
                if not memory_state:
                    memory_state = {}

                # Set intent if not already set
                if "intent" not in memory_state:
                    memory_state["intent"] = "CREATE_BOOKING"

                # Store the semantic draft for proper merging on next turn
                memory_state["resolved_booking_semantics"] = resolved_booking_for_draft_check
                memory_state["booking_state"] = {
                    "booking_state": "PARTIAL",
                    "reason": "MISSING_DATE_AND_TIME"
                }

                # Debug log #2: After memory mutation
                logger.info(
                    "MEMORY_WRITE",
                    extra={
                        'request_id': request_id,
                        'user_id': user_id,
                        'keys': list(memory_state.keys()),
                        'booking_state': memory_state.get("booking_state"),
                        'has_resolved_booking': "resolved_booking_semantics" in memory_state,
                    }
                )

                # Persist immediately to memory store if available
                if memory_store and intent == "CREATE_BOOKING":
                    try:
                        # Debug log #1: Right before memory write
                        logger.info(
                            "SERVICE_ONLY_CHECK",
                            extra={
                                'request_id': request_id,
                                'user_id': user_id,
                                'has_service': bool(resolved_booking_for_draft_check.get("services")),
                                'has_date': bool(resolved_booking_for_draft_check.get("date_refs") or resolved_booking_for_draft_check.get("date_range")),
                                'has_time': bool(resolved_booking_for_draft_check.get("time_refs") or resolved_booking_for_draft_check.get("time_range")),
                                'decision': None,  # Decision not yet made at this point
                            }
                        )
                        memory_state["last_updated"] = datetime.now(
                            dt_timezone.utc).isoformat()
                        memory_store.set(
                            user_id=user_id,
                            domain=domain,
                            state=memory_state,
                            ttl=config.MEMORY_TTL
                        )
                        logger.info(
                            "Persisted service-only booking draft to memory store",
                            extra={
                                'request_id': request_id,
                                'user_id': user_id,
                                'services': [s.get("text", "") for s in resolved_booking_for_draft_check.get("services", []) if isinstance(s, dict)]
                            }
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            f"Failed to persist service-only draft: {e}",
                            extra={'request_id': request_id}
                        )
                else:
                    # Debug log #1: Right before memory write (when persistence will happen later)
                    logger.info(
                        "SERVICE_ONLY_CHECK",
                        extra={
                            'request_id': request_id,
                            'user_id': user_id,
                            'has_service': bool(resolved_booking_for_draft_check.get("services")),
                            'has_date': bool(resolved_booking_for_draft_check.get("date_refs") or resolved_booking_for_draft_check.get("date_range")),
                            'has_time': bool(resolved_booking_for_draft_check.get("time_refs") or resolved_booking_for_draft_check.get("time_range")),
                            'decision': None,  # Decision not yet made at this point
                        }
                    )
                    logger.info(
                        "Prepared service-only semantic draft (will be persisted later)",
                        extra={
                            'request_id': request_id,
                            'user_id': user_id,
                            'services': [s.get("text", "") for s in resolved_booking_for_draft_check.get("services", []) if isinstance(s, dict)]
                        }
                    )

            # Case 2: Service + date draft (persist for continuation)
            elif has_service and has_date and not has_time:
                # Ensure memory_state exists and is properly structured for persistence
                if not memory_state:
                    memory_state = {}

                # Set intent if not already set
                if "intent" not in memory_state:
                    memory_state["intent"] = "CREATE_BOOKING"

                # Store the semantic draft for proper merging on next turn
                memory_state["resolved_booking_semantics"] = resolved_booking_for_draft_check
                memory_state["booking_state"] = {
                    "booking_state": "PARTIAL",
                    "reason": "MISSING_TIME"
                }

                # Debug log #2: After memory mutation
                logger.info(
                    "MEMORY_WRITE",
                    extra={
                        'request_id': request_id,
                        'user_id': user_id,
                        'keys': list(memory_state.keys()),
                        'booking_state': memory_state.get("booking_state"),
                        'has_resolved_booking': "resolved_booking_semantics" in memory_state,
                    }
                )

                # Persist immediately to memory store if available
                if memory_store and intent == "CREATE_BOOKING":
                    try:
                        # Debug log #1: Right before memory write
                        logger.info(
                            "SERVICE_ONLY_CHECK",
                            extra={
                                'request_id': request_id,
                                'user_id': user_id,
                                'has_service': bool(resolved_booking_for_draft_check.get("services")),
                                'has_date': bool(resolved_booking_for_draft_check.get("date_refs") or resolved_booking_for_draft_check.get("date_range")),
                                'has_time': bool(resolved_booking_for_draft_check.get("time_refs") or resolved_booking_for_draft_check.get("time_range")),
                                'decision': None,  # Decision not yet made at this point
                            }
                        )
                        memory_state["last_updated"] = datetime.now(
                            dt_timezone.utc).isoformat()
                        memory_store.set(
                            user_id=user_id,
                            domain=domain,
                            state=memory_state,
                            ttl=config.MEMORY_TTL
                        )
                        logger.info(
                            "Persisted service+date booking draft to memory store",
                            extra={
                                'request_id': request_id,
                                'user_id': user_id,
                                'services': len(resolved_booking_for_draft_check.get("services", [])),
                                'date_refs': resolved_booking_for_draft_check.get("date_refs", []),
                                'date_mode': resolved_booking_for_draft_check.get("date_mode", "none")
                            }
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            f"Failed to persist service+date draft: {e}",
                            extra={'request_id': request_id}
                        )
                else:
                    # Debug log #1: Right before memory write (when persistence will happen later)
                    logger.info(
                        "SERVICE_ONLY_CHECK",
                        extra={
                            'request_id': request_id,
                            'user_id': user_id,
                            'has_service': bool(resolved_booking_for_draft_check.get("services")),
                            'has_date': bool(resolved_booking_for_draft_check.get("date_refs") or resolved_booking_for_draft_check.get("date_range")),
                            'has_time': bool(resolved_booking_for_draft_check.get("time_refs") or resolved_booking_for_draft_check.get("time_range")),
                            'decision': None,  # Decision not yet made at this point
                        }
                    )
                    logger.info(
                        "Prepared service+date semantic draft (will be persisted later)",
                        extra={
                            'request_id': request_id,
                            'user_id': user_id,
                            'services': len(resolved_booking_for_draft_check.get("services", [])),
                            'date_refs': resolved_booking_for_draft_check.get("date_refs", []),
                            'date_mode': resolved_booking_for_draft_check.get("date_mode", "none")
                        }
                    )

        # Contextual time-only update detection
        # If user says "make it 10" and there's an active booking in memory,
        # treat it as a modification by reusing date from memory
        logger.info(
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
        logger.info(
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

            logger.info(
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

                    logger.info(
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
                        logger.info(
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

                    logger.info(
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
                        from luma.resolution.semantic_resolver import SemanticResolutionResult
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

                        logger.info(
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
                        logger.info(
                            f"Contextual update condition met but no date in memory: user {user_id}",
                            extra={
                                'request_id': request_id,
                                'memory_date_refs': memory_date_refs,
                                'memory_date_mode': memory_date_mode
                            }
                        )
                else:
                    # merged_semantic_result already has date_refs (from PARTIAL continuation merge)
                    logger.info(
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
                logger.info(
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
            logger.info(
                f"Not checking contextual update: user {user_id}",
                extra={
                    'request_id': request_id,
                    'intent': intent,
                    'intent_is_create': intent == "CREATE_BOOKING",
                    'has_resolved_booking_semantics': has_resolved_booking_semantics
                }
            )

        # Decision / Policy Layer - ACTIVE
        # Decision layer determines if clarification is needed BEFORE calendar binding
        # Policy operates ONLY on semantic roles, never on raw text or regex
        decision_result = None
        try:
            # Load booking policy from config
            booking_policy = _get_booking_policy()

            # CRITICAL INVARIANT: decision must see fully merged semantics
            # If there is an active booking, the semantic object passed to decision
            # MUST be the merged semantic booking, not the current fragment
            if memory_state and _has_active_booking(memory_state) and merged_semantic_result:
                semantic_for_decision = merged_semantic_result.resolved_booking
                decision_source = "merged"
            else:
                semantic_for_decision = semantic_result.resolved_booking
                decision_source = "current"

            logger.info(
                "Decision semantic source",
                extra={
                    'request_id': request_id,
                    'user_id': user_id,
                    'source': decision_source,
                    'has_active_booking': bool(memory_state and _has_active_booking(memory_state)),
                    'has_merged_result': bool(merged_semantic_result and merged_semantic_result != semantic_result)
                }
            )

            stage_start = time.time()
            decision_result = decide_booking_status(
                semantic_for_decision,
                entities=extraction_result,
                policy=booking_policy
            )
            stage_duration = round((time.time() - stage_start) * 1000, 2)

            # Store decision result in results
            results["stages"]["decision"] = {
                "status": decision_result.status,
                "reason": decision_result.reason,
                "effective_time": decision_result.effective_time
            }

            # Stage 7: DECISION - Log decision state and reason
            _log_stage(
                logger, request_id, "decision",
                input_data={"semantic_for_decision": semantic_for_decision},
                output_data={
                    "decision_state": decision_result.status,
                    "reason": decision_result.reason
                },
                duration_ms=stage_duration
            )

            # If decision requires clarification, return early (before calendar binding)
            if decision_result.status == "NEEDS_CLARIFICATION":
                # Convert decision reason to ClarificationReason enum
                # Note: ClarificationReason is already imported at top of file
                reason_enum = ClarificationReason.CONFLICTING_SIGNALS  # default
                if decision_result.reason == "MISSING_DATE":
                    reason_enum = ClarificationReason.MISSING_DATE
                elif decision_result.reason == "MISSING_TIME":
                    reason_enum = ClarificationReason.MISSING_TIME

                # Debug: Log service-only booking detection
                resolved_booking_for_check = merged_semantic_result.resolved_booking
                has_service_only = (
                    bool(resolved_booking_for_check.get("services")) and
                    not bool(resolved_booking_for_check.get("date_refs") or resolved_booking_for_check.get("date_range")) and
                    not bool(resolved_booking_for_check.get("time_refs")
                             or resolved_booking_for_check.get("time_range"))
                )
                if has_service_only:
                    logger.info(
                        "Detected service-only booking - will persist as PARTIAL",
                        extra={
                            'request_id': request_id,
                            'user_id': user_id,
                            'services_count': len(resolved_booking_for_check.get("services", [])),
                            'decision_reason': decision_result.reason
                        }
                    )

                from luma.clarification import Clarification

                clarification_obj = Clarification(
                    reason=reason_enum,
                    data={}
                )

                # Build PARTIAL booking with known semantic information
                # Use merged semantic result if this is a continuation
                resolved_booking = merged_semantic_result.resolved_booking

                # CRITICAL: Extract services FIRST before checking memory_state
                # This ensures service-only bookings always have services in resolved_booking
                services = resolved_booking.get("services", [])

                # If no services in resolved_booking, try extraction_result
                # CRITICAL: For service-only bookings, services MUST be extracted and persisted
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
                    # If we found services in extraction, update resolved_booking for persistence
                    if services:
                        resolved_booking["services"] = services

                # CRITICAL: Check if memory_state already has resolved_booking_semantics from pre-decision persistence
                # This happens for service-only bookings that were detected before decision
                # But only use it if it has services (otherwise use our extracted services)
                if memory_state and memory_state.get("resolved_booking_semantics"):
                    memory_resolved_booking = memory_state.get(
                        "resolved_booking_semantics")
                    # Only use memory resolved_booking if it has services and we don't have services yet
                    if memory_resolved_booking.get("services") and not services:
                        resolved_booking = memory_resolved_booking
                        services = resolved_booking.get("services", [])
                        logger.info(
                            f"Using existing resolved_booking_semantics from memory_state: user {user_id}",
                            extra={
                                'request_id': request_id,
                                'has_services': bool(resolved_booking.get('services')),
                                'has_date_refs': bool(resolved_booking.get('date_refs')),
                                'has_time_refs': bool(resolved_booking.get('time_refs'))
                            }
                        )
                    # If we have services from extraction, merge them into memory resolved_booking
                    elif services and memory_resolved_booking.get("services"):
                        # Merge: use our extracted services, keep date/time from memory if present
                        resolved_booking = memory_resolved_booking.copy()
                        resolved_booking["services"] = services
                        logger.info(
                            f"Merged extracted services with memory resolved_booking_semantics: user {user_id}",
                            extra={
                                'request_id': request_id,
                                'services_count': len(services),
                                'has_date_refs': bool(resolved_booking.get('date_refs')),
                                'has_time_refs': bool(resolved_booking.get('time_refs'))
                            }
                        )

                # Build PARTIAL booking: services (always), date if known, datetime_range = null
                partial_booking = {
                    "services": services,
                    "datetime_range": None,
                    "booking_state": "PARTIAL"
                }

                # Include date_range if date is known (but time is missing)
                # Check if we have date information from semantic result
                date_refs = resolved_booking.get("date_refs", [])
                date_mode = resolved_booking.get("date_mode", "none")
                if date_refs and date_mode != "none":
                    # We have date but no time - include date_range if we can construct it
                    # For PARTIAL bookings, we'll include date_range if available from calendar binding attempt
                    # But since we're returning early, we'll leave date_range as None for now
                    # The date information is preserved in semantic_result for future processing
                    pass

                # Persist PARTIAL booking to memory for continuation
                # CRITICAL: Always persist PARTIAL bookings so they can be continued
                # This ensures _has_active_booking() returns True on next turn
                logger.info(
                    f"PARTIAL persistence check: user {user_id}",
                    extra={
                        'request_id': request_id,
                        'has_memory_store': bool(memory_store),
                        'intent': intent,
                        'intent_is_create': intent == "CREATE_BOOKING",
                        'services_count': len(services),
                        'resolved_booking_has_services': bool(resolved_booking.get('services'))
                    }
                )

                # CRITICAL: Ensure services are in resolved_booking before persistence
                if services and not resolved_booking.get("services"):
                    resolved_booking["services"] = services

                # CRITICAL: Always persist PARTIAL bookings for CREATE_BOOKING intent
                # This is required for multi-turn slot filling to work
                if memory_store and intent == "CREATE_BOOKING":
                    try:
                        # Diagnostic: Log what we're storing
                        logger.info(
                            f"Storing resolved_booking_semantics for PARTIAL: user {user_id}",
                            extra={
                                'request_id': request_id,
                                'stored_date_refs': resolved_booking.get('date_refs', []),
                                'stored_date_mode': resolved_booking.get('date_mode', 'none'),
                                'stored_time_refs': resolved_booking.get('time_refs', []),
                                'stored_time_mode': resolved_booking.get('time_mode', 'none'),
                                'stored_services': len(resolved_booking.get('services', []))
                            }
                        )

                        # Build memory state with PARTIAL booking
                        # Include resolved_booking_semantics for proper merging on continuation
                        # CRITICAL: Only store primitives and semantic dictionaries - no datetime objects
                        partial_memory = {
                            "intent": "CREATE_BOOKING",
                            "booking_state": partial_booking,
                            "clarification": clarification_obj.to_dict(),
                            "resolved_booking_semantics": resolved_booking,  # Store semantic info for merging
                            # Already ISO string
                            "last_updated": datetime.now(dt_timezone.utc).isoformat()
                        }
                        stage_start = time.time()
                        memory_store.set(
                            user_id=user_id,
                            domain=domain,
                            state=partial_memory,
                            ttl=config.MEMORY_TTL
                        )
                        stage_duration = round((time.time() - stage_start) * 1000, 2)
                        
                        # Stage 8: STATE PERSIST - Log what was stored
                        storage_backend = "redis" if hasattr(memory_store, 'redis') else "memory"
                        _log_stage(
                            logger, request_id, "state_persist",
                            input_data={"user_id": user_id, "domain": domain},
                            output_data={
                                "stored": {
                                    "intent": "CREATE_BOOKING",
                                    "booking_state": "PARTIAL",
                                    "has_services": bool(resolved_booking.get("services")),
                                    "has_datetime_range": False
                                },
                                "storage_backend": storage_backend
                            },
                            duration_ms=stage_duration
                        )
                    except Exception as e:  # noqa: BLE001
                        # Log loudly - persistence failures should not be silent
                        # Use ERROR level with full traceback to ensure visibility
                        logger.error(
                            f"CRITICAL: Failed to persist PARTIAL booking for user {user_id}: {e}",
                            extra={
                                'request_id': request_id,
                                'user_id': user_id,
                                'intent': intent,
                                'error_type': type(e).__name__
                            },
                            exc_info=True
                        )
                        # Do not re-raise - allow API to return PARTIAL booking response
                        # The error is logged loudly and will be visible in logs
                else:
                    # CRITICAL: Log why persistence did not happen
                    # This should not happen for CREATE_BOOKING intent with memory_store available
                    logger.error(
                        f"CRITICAL: Failed to persist PARTIAL booking - condition not met: user {user_id}",
                        extra={
                            'request_id': request_id,
                            'user_id': user_id,
                            'has_memory_store': bool(memory_store),
                            'intent': intent,
                            'intent_is_create': intent == "CREATE_BOOKING",
                            'services_count': len(services),
                            'resolved_booking_has_services': bool(resolved_booking.get('services'))
                        }
                    )
                    # CRITICAL: Even if condition fails, try to persist if we have services
                    # This ensures service-only bookings are always persisted
                    if services and memory_store:
                        try:
                            # Build minimal PARTIAL memory structure
                            minimal_partial_memory = {
                                "intent": "CREATE_BOOKING",
                                "booking_state": {
                                    "services": services,
                                    "datetime_range": None,
                                    "booking_state": "PARTIAL"
                                },
                                "clarification": clarification_obj.to_dict(),
                                "resolved_booking_semantics": resolved_booking,
                                "last_updated": datetime.now(dt_timezone.utc).isoformat()
                            }
                            memory_store.set(
                                user_id=user_id,
                                domain=domain,
                                state=minimal_partial_memory,
                                ttl=config.MEMORY_TTL
                            )
                            logger.info(
                                f"Persisted PARTIAL booking via fallback path: user {user_id}",
                                extra={'request_id': request_id}
                            )
                        except Exception as e:  # noqa: BLE001
                            logger.error(
                                f"CRITICAL: Fallback persistence also failed: {e}",
                                extra={'request_id': request_id},
                                exc_info=True
                            )

                # Return clarification immediately - do not proceed to calendar binding
                # This ensures clarification decisions come ONLY from DecisionResult
                # Determine debug mode (query param debug=1|true|yes)
                debug_flag = str(request.args.get("debug", "0")).lower()
                debug_mode = debug_flag in {"1", "true", "yes"}

                response_body = {
                    "success": True,
                    "intent": {"name": intent, "confidence": confidence},
                    "needs_clarification": True,
                    "clarification": clarification_obj.to_dict(),
                    "booking": partial_booking
                }

                if debug_mode:
                    response_body["debug"] = results

                return jsonify(response_body), 200

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
        # CONTEXTUAL_UPDATE is internal only - never returned in API
        # Also detect when CREATE_BOOKING is a continuation of PARTIAL booking
        effective_intent = intent
        if memory_state and memory_state.get("intent") == "CREATE_BOOKING":
            # Check if this is a contextual update to existing CREATE_BOOKING draft
            # This includes both PARTIAL bookings and regular drafts
            if intent == "MODIFY_BOOKING" or intent == "UNKNOWN":
                # Check if at least one mutable slot is modified (date, time, or duration)
                # Allows single or multiple slot updates (e.g., "Wednesday at 5pm")
                mutable_slots_modified = _count_mutable_slots_modified(
                    merged_semantic_result, extraction_result
                )
                # Check if no service or booking verb is present
                has_service = len(_get_business_categories(extraction_result)) > 0
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
            elif intent == "CREATE_BOOKING" and _is_partial_booking(memory_state):
                # CREATE_BOOKING with PARTIAL memory is already handled above as continuation
                # This is just for logging - the merge already happened
                logger.info(
                    f"CREATE_BOOKING continuation of PARTIAL booking for user {user_id}",
                    extra={'request_id': request_id}
                )

        # Stage 6: Calendar Binding
        # Use effective_intent for calendar binding (CONTEXTUAL_UPDATE treated as CREATE_BOOKING)
        # Use merged semantic result if this was a PARTIAL continuation
        binding_intent = effective_intent if effective_intent != CONTEXTUAL_UPDATE else "CREATE_BOOKING"
        try:
            calendar_result = bind_calendar(
                merged_semantic_result,
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
                extra={'request_id': request_id, 'clarification_reason': current_clarification.get('reason')}
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

            # CRITICAL: Branch strictly on decision result for persistence
            # RESOLVED: Clear PARTIAL state and persist RESOLVED booking
            # NEEDS_CLARIFICATION: Persist PARTIAL booking (already handled in early return)
            # This ensures decision outcome determines persistence, not continuation state
            if decision_result and decision_result.status == "RESOLVED":
                # Decision is RESOLVED - clear any existing PARTIAL state
                # Don't merge with old PARTIAL booking, start fresh with RESOLVED state
                if memory_state and _is_partial_booking(memory_state):
                    # Clear the PARTIAL booking - start with fresh state
                    memory_state = None
                    logger.info(
                        f"Clearing PARTIAL booking state for RESOLVED booking: user {user_id}",
                        extra={'request_id': request_id}
                    )

                # Ensure current_clarification is None for RESOLVED (already set above)
                # This ensures no PARTIAL clarification is persisted
                if current_clarification is not None:
                    logger.warning(
                        f"Unexpected clarification for RESOLVED booking, clearing: user {user_id}",
                        extra={'request_id': request_id}
                    )
                    current_clarification = None

            # Merge booking state (memory_state may be None if RESOLVED and PARTIAL was cleared)
            merged_memory = merge_booking_state(
                memory_state=memory_state,
                current_intent=persist_intent,  # Always persist as CREATE_BOOKING
                current_booking=current_booking,
                current_clarification=current_clarification
            )

            # CRITICAL: Verify merged state has no clarification if decision is RESOLVED
            # This is a safety check to ensure RESOLVED bookings never persist as PARTIAL
            if decision_result and decision_result.status == "RESOLVED":
                if merged_memory.get("clarification") is not None:
                    # Force clear clarification for RESOLVED bookings
                    merged_memory["clarification"] = None
                    logger.warning(
                        f"Force-cleared clarification for RESOLVED booking: user {user_id}",
                        extra={'request_id': request_id}
                    )

                # Store booking_state = "RESOLVED" inside the booking_state dict
                # This is required for _has_active_booking() to detect RESOLVED bookings
                if "booking_state" in merged_memory:
                    merged_memory["booking_state"]["booking_state"] = "RESOLVED"

                # Store resolved_booking_semantics for RESOLVED bookings to enable contextual updates
                # This allows "make it 10" to reuse date from memory
                resolved_booking = merged_semantic_result.resolved_booking
                if resolved_booking:
                    merged_memory["resolved_booking_semantics"] = resolved_booking
                    logger.info(
                        f"Storing resolved_booking_semantics for RESOLVED: user {user_id}",
                        extra={
                            'request_id': request_id,
                            'stored_date_refs': resolved_booking.get('date_refs', []),
                            'stored_date_mode': resolved_booking.get('date_mode', 'none'),
                            'stored_time_refs': resolved_booking.get('time_refs', []),
                            'stored_time_mode': resolved_booking.get('time_mode', 'none'),
                            'stored_services': len(resolved_booking.get('services', []))
                        }
                    )

            # Persist merged state with CREATE_BOOKING intent
            # Only persist if we have a valid booking state (RESOLVED or PARTIAL)
            if memory_store:
                try:
                    stage_start = time.time()
                    memory_store.set(
                        user_id=user_id,
                        domain=domain,
                        state=merged_memory,
                        ttl=config.MEMORY_TTL
                    )
                    stage_duration = round((time.time() - stage_start) * 1000, 2)
                    
                    # Stage 8: STATE PERSIST - Log what was stored
                    storage_backend = "redis" if hasattr(memory_store, 'redis') else "memory"
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
                        },
                        duration_ms=stage_duration
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
                "last_updated": datetime.now(dt_timezone.utc).isoformat()
            }

        # Post-semantic validation guard: Check for orphan slot updates
        # If extracted slots exist but cannot be applied (no booking_id, no draft, no booking),
        # return clarification instead of "successful" empty response
        # Only apply if no existing clarification (don't override existing clarifications)
        existing_clarification = merged_memory.get("clarification")
        if not existing_clarification:
            # Check if any slots were extracted
            has_services = len(_get_business_categories(extraction_result)) > 0
            has_dates = (len(extraction_result.get("dates", [])) > 0 or
                         len(extraction_result.get("dates_absolute", [])) > 0)
            has_times = (len(extraction_result.get("times", [])) > 0 or
                         len(extraction_result.get("time_windows", [])) > 0)
            has_duration = len(extraction_result.get("durations", [])) > 0

            # Check semantic result for date/time refs
            # Use merged_semantic_result to get merged semantics if this was a continuation
            resolved_booking = merged_semantic_result.resolved_booking
            has_date_refs = len(resolved_booking.get("date_refs", [])) > 0
            has_time_refs = len(resolved_booking.get("time_refs", [])) > 0

            # Check if any slots were extracted
            has_extracted_slots = (has_services or has_dates or has_times or
                                   has_duration or has_date_refs or has_time_refs)

            # Check if calendar binding produced a booking
            calendar_has_booking = current_booking.get(
                "datetime_range") is not None

            # Check context availability
            booking_id = data.get("booking_id")
            has_draft = memory_state and memory_state.get(
                "intent") == "CREATE_BOOKING"
            has_context = booking_id is not None or has_draft

            # If slots extracted but no context and no booking produced → clarification needed
            if (has_extracted_slots and
                not has_context and
                not calendar_has_booking and
                    effective_intent in {"UNKNOWN", "MODIFY_BOOKING"}):
                # Determine what's missing
                missing_requirements = []
                if not has_draft and not booking_id:
                    missing_requirements.append("booking_reference")
                if has_times or has_time_refs:
                    if not has_dates and not has_date_refs:
                        missing_requirements.append("date")
                if has_dates or has_date_refs:
                    if not has_times and not has_time_refs:
                        missing_requirements.append("time")
                if not has_services:
                    missing_requirements.append("service")

                # Set clarification
                context_clarification = {
                    "reason": ClarificationReason.MISSING_CONTEXT.value,
                    "data": {
                        "missing_requirements": missing_requirements
                    }
                }
                # Update merged_memory with clarification
                merged_memory["clarification"] = context_clarification

        # Build production response
        # Map CONTEXTUAL_UPDATE to CREATE_BOOKING in API response
        api_intent = "CREATE_BOOKING" if effective_intent == CONTEXTUAL_UPDATE else effective_intent
        intent_payload = {"name": api_intent, "confidence": confidence}

        # Extract clarification from merged state
        # Also check decision_result if available (for early return cases that might have been bypassed)
        merged_clarification = merged_memory.get("clarification")
        needs_clarification = merged_clarification is not None

        # CRITICAL: If semantic resolution has clarification (e.g., SERVICE_VARIANT),
        # it takes precedence over decision layer and merged memory
        if merged_semantic_result and merged_semantic_result.needs_clarification and merged_semantic_result.clarification:
            needs_clarification = True
            merged_clarification = merged_semantic_result.clarification.to_dict()

        # If decision layer returned NEEDS_CLARIFICATION but we didn't return early,
        # ensure we have clarification set from decision_result
        if decision_result and decision_result.status == "NEEDS_CLARIFICATION" and not needs_clarification:
            # Decision layer says clarification needed, but merged_memory doesn't have it
            # This can happen if we bypassed the early return somehow
            reason_enum = ClarificationReason.CONFLICTING_SIGNALS  # default
            if decision_result.reason == "MISSING_DATE":
                reason_enum = ClarificationReason.MISSING_DATE
            elif decision_result.reason == "MISSING_TIME":
                reason_enum = ClarificationReason.MISSING_TIME
            merged_clarification = {
                "reason": reason_enum.value,
                "data": {}
            }
            needs_clarification = True

        # Return booking state for CREATE_BOOKING, CONTEXTUAL_UPDATE, or MODIFY_BOOKING
        # CRITICAL: For CREATE_BOOKING, booking must NEVER be null, even when clarification is needed
        booking_payload = None
        if (is_booking_intent or is_modify_with_booking_id) and not needs_clarification:
            booking_payload = extract_memory_state_for_response(merged_memory)
            # Add booking_state = "RESOLVED" for resolved bookings
            if booking_payload:
                booking_payload["booking_state"] = "RESOLVED"
                # Format services to preserve resolved_alias if present in current semantic result
                if merged_semantic_result:
                    current_services = merged_semantic_result.resolved_booking.get("services", [])
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
                    current_services = merged_semantic_result.resolved_booking.get("services", [])
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
            # For CREATE_BOOKING with clarification (including semantic clarifications like SERVICE_VARIANT),
            # build PARTIAL booking
            # For CREATE_BOOKING with clarification, build PARTIAL booking
            # Extract services from semantic result or extraction result
            # Use merged_semantic_result to get merged semantics if this was a continuation
            resolved_booking = merged_semantic_result.resolved_booking
            services = resolved_booking.get("services", [])

            # If no services in resolved_booking, try extraction_result
            if not services:
                service_families = _get_business_categories(extraction_result)
                services = [
                    _format_service_for_response(service)
                    for service in service_families
                    if isinstance(service, dict) and service.get("text")
                ]
            else:
                # Format services, preserving resolved_alias if present
                services = [
                    _format_service_for_response(service)
                    for service in services
                    if isinstance(service, dict)
                ]

            # Build PARTIAL booking: services (always), date if known, datetime_range = null
            booking_payload = {
                "services": services,
                "datetime_range": None,
                "booking_state": "PARTIAL"
            }

            # If we have date information but no time, we could include date_range
            # For now, we'll keep datetime_range as None for PARTIAL bookings
            # The date information is preserved in semantic_result for future processing
        elif is_booking_intent and booking_payload is None:
            # Fallback: If we somehow got here with CREATE_BOOKING and no booking_payload,
            # build a minimal PARTIAL booking to ensure booking is never null
            # This should not normally happen, but ensures contract compliance
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
                # Format services, preserving resolved_alias if present
                services = [
                    _format_service_for_response(service)
                    for service in services
                    if isinstance(service, dict)
                ]
            booking_payload = {
                "services": services,
                "datetime_range": None,
                "booking_state": "PARTIAL"
            }

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

        # Final response validation guard: Check for orphan slot updates
        # Prevents "successful" responses when slots are extracted but cannot be applied
        if not needs_clarification:
            # Check if booking_payload is null OR has null datetime_range
            booking_is_invalid = (booking_payload is None or
                                  booking_payload.get("datetime_range") is None)

            if booking_is_invalid:
                # Check if slots were extracted
                # Use merged_semantic_result to get merged semantics if this was a continuation
                resolved_booking = merged_semantic_result.resolved_booking
                has_time_refs = len(resolved_booking.get("time_refs", [])) > 0
                has_date_refs = len(resolved_booking.get("date_refs", [])) > 0
                has_services = len(_get_business_categories(extraction_result)) > 0
                has_duration = len(extraction_result.get("durations", [])) > 0

                has_extracted_slots = has_time_refs or has_date_refs or has_services or has_duration

                # Check context availability
                booking_id = data.get("booking_id")
                has_draft = memory_state and memory_state.get(
                    "intent") == "CREATE_BOOKING"
                has_context = booking_id is not None or has_draft

                # If slots extracted but no context and booking is invalid → clarification needed
                if has_extracted_slots and not has_context:
                    # Determine which slots were provided
                    provided_slots = []
                    if has_time_refs:
                        provided_slots.append("time")
                    if has_date_refs:
                        provided_slots.append("date")
                    if has_services:
                        provided_slots.append("service")
                    if has_duration:
                        provided_slots.append("duration")

                    # Set clarification
                    needs_clarification = True
                    merged_clarification = {
                        "reason": ClarificationReason.MISSING_CONTEXT.value,
                        "data": {
                            "provided_slots": provided_slots
                        }
                    }
                    # For CREATE_BOOKING, build PARTIAL booking instead of null
                    if is_booking_intent and booking_payload is None:
                        # Extract services from semantic result or extraction result
                        # Use merged_semantic_result to get merged semantics if this was a continuation
                        resolved_booking = merged_semantic_result.resolved_booking
                        services = resolved_booking.get("services", [])

                        # If no services in resolved_booking, try extraction_result
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

                        # Build PARTIAL booking
                        booking_payload = {
                            "services": services,
                            "datetime_range": None,
                            "booking_state": "PARTIAL"
                        }

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

        # For CREATE_BOOKING, always include booking (never null)
        # Final safety check: ensure booking_payload is never null for CREATE_BOOKING
        if api_intent == "CREATE_BOOKING" and booking_payload is None:
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
                "booking_state": "PARTIAL" if needs_clarification else "RESOLVED"
            }
            logger.warning(
                f"Fallback: Built PARTIAL booking for CREATE_BOOKING when booking_payload was None",
                extra={'request_id': request_id, 'intent': api_intent}
            )

        # booking_payload is already set above for both RESOLVED and PARTIAL cases
        # For non-booking intents, booking_payload may be None (which is fine)
        response_body = {
            "success": True,
            "intent": intent_payload,
            "needs_clarification": needs_clarification,
            "clarification": merged_clarification if needs_clarification else None,
            "booking": booking_payload,
        }

        # Add entities field for non-booking intents (always include, even if empty)
        if entities_payload is not None:
            response_body["entities"] = entities_payload

        # Attach full internal pipeline data only in debug mode
        if debug_mode:
            response_body["debug"] = results

        # Stage 9: RESPONSE - Log response data
        _log_stage(
            logger, request_id, "response",
            input_data={"intent": api_intent},
            output_data={
                "booking_state": booking_payload.get("booking_state") if booking_payload else None,
                "services": [s.get("text", "") for s in booking_payload.get("services", []) if isinstance(s, dict)] if booking_payload else [],
                "datetime_range": booking_payload.get("datetime_range") if booking_payload else None
            },
            notes=f"needs_clarification={needs_clarification}"
        )

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
