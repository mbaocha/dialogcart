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
from luma.calendar.calendar_binder import bind_calendar, CalendarBindingResult  # noqa: E402
from luma.resolution.semantic_resolver import resolve_semantics  # noqa: E402
from luma.grouping.appointment_grouper import group_appointment  # noqa: E402
from luma.structure.interpreter import interpret_structure  # noqa: E402
from luma.grouping.reservation_intent_resolver import ReservationIntentResolver  # noqa: E402
from luma.extraction.matcher import EntityMatcher  # noqa: E402
from luma.config import config  # noqa: E402
from luma.config.temporal import (  # noqa: E402
    APPOINTMENT_TEMPORAL_TYPE,
    RESERVATION_TEMPORAL_TYPE,
    INTENT_TEMPORAL_SHAPE,
    INTENT_REQUIRE_END_DATE,
    TimeMode,
)
from luma.config.intent_meta import validate_required_slots  # noqa: E402
from luma.logging_config import setup_logging, generate_request_id  # noqa: E402
from luma.memory import RedisMemoryStore  # noqa: E402
from luma.memory.merger import merge_booking_state, extract_memory_state_for_response  # noqa: E402
from luma.decision import decide_booking_status  # noqa: E402
from luma.clarification import ClarificationReason  # noqa: E402
from luma.pipeline import LumaPipeline  # noqa: E402
from luma.trace_contract import validate_stable_fields, TRACE_VERSION  # noqa: E402
from luma.perf import StageTimer  # noqa: E402
from luma.app.resolve_service import resolve_message  # noqa: E402

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
    g.start_time = time.perf_counter()
    g.request_id = request.headers.get('X-Request-ID', generate_request_id())


@app.after_request
def after_request(response):
    """Log request completion with timing and status."""
    if hasattr(g, 'start_time') and config.ENABLE_REQUEST_LOGGING:
        duration_ms = round((time.perf_counter() - g.start_time) * 1000, 2)

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


# Cache for normalization directory (computed once per process)
_normalization_dir_cache: Optional[Path] = None


def find_normalization_dir():
    """
    Find the normalization directory.
    
    Cached per process to avoid repeated file system checks.
    """
    global _normalization_dir_cache
    if _normalization_dir_cache is not None:
        return _normalization_dir_cache
    
    current_file = Path(__file__).resolve()
    store_dir = current_file.parent / "store" / "normalization"
    if store_dir.exists():
        _normalization_dir_cache = store_dir
        return _normalization_dir_cache
    src_dir = current_file.parent.parent
    intents_norm = src_dir / "intents" / "normalization"
    if intents_norm.exists():
        _normalization_dir_cache = intents_norm
        return _normalization_dir_cache
    
    _normalization_dir_cache = None
    return None


def _build_issues(
    missing_slots: List[str],
    time_issues: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Build issues object from missing_slots and time_issues.

    Args:
        missing_slots: List of missing slot names (e.g., ["time", "date"])
        time_issues: Optional list of time-related issues from semantic resolution

    Returns:
        Issues object with structure:
        {
            "time": {"raw": "...", "start_hour": 2, "end_hour": 5, "candidates": ["am", "pm"]} or "missing",
            "date": "missing"
        }
    """
    issues: Dict[str, Any] = {}

    # Handle missing slots (simple string)
    for slot in missing_slots:
        if slot not in issues:
            issues[slot] = "missing"

    # Handle time issues (rich object with details - simplified, no type/kind)
    if time_issues:
        for issue in time_issues:
            issue_kind = issue.get("kind")
            if issue_kind == "ambiguous_meridiem":
                # Override "missing" with rich ambiguous object (data only, no classification)
                issues["time"] = {
                    "raw": issue.get("raw"),
                    "start_hour": issue.get("start_hour"),
                    "end_hour": issue.get("end_hour"),
                    "candidates": issue.get("candidates", [])
                }

    return issues


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


def plan_clarification(
    intent_result: Dict[str, Any],
    entities: Dict[str, Any],
    semantic_result: Optional[Any],
    decision_result: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Plan clarification payload based on resolver slots and semantic clarifications.

    Args:
        intent_result: dict from _build_response (intent, confidence, status, missing_slots)
        entities: extraction entities
        semantic_result: SemanticResolutionResult or None
        decision_result: DecisionResult or None

    Returns:
        Dict with status, missing_slots, and clarification_reason
    """
    status = intent_result.get("status", "ready")
    missing_slots = intent_result.get("missing_slots", []) or []
    clarification_reason = None

    # Prefer semantic clarifications (e.g., SERVICE_VARIANT) if present
    if semantic_result and getattr(semantic_result, "needs_clarification", False):
        sem_dict = semantic_result.to_dict()
        sem_clar = sem_dict.get("clarification") or {}
        if sem_clar:
            # Extract reason from Clarification object
            reason = sem_clar.get("reason")
            if isinstance(reason, str):
                clarification_reason = reason
            elif hasattr(reason, "value"):
                # ClarificationReason enum
                clarification_reason = reason.value
            status = "needs_clarification"

    # Check for ambiguous meridiem in time_issues FIRST (before decision layer)
    # This takes priority because ambiguous meridiem is more specific than MISSING_TIME
    if not clarification_reason and semantic_result:
        resolved_booking = semantic_result.resolved_booking
        time_issues = resolved_booking.get("time_issues", [])
        for issue in time_issues:
            if issue.get("kind") == "ambiguous_meridiem":
                clarification_reason = "AMBIGUOUS_TIME_MERIDIEM"
                status = "needs_clarification"
                break

    # Check decision layer for temporal shape violations
    if decision_result and decision_result.status == "NEEDS_CLARIFICATION":
        decision_reason = decision_result.reason
        if decision_reason and not clarification_reason:  # Only use if no more specific reason set
            # Map decision layer reasons to ClarificationReason enum values
            if decision_reason == "MISSING_TIME":
                clarification_reason = "MISSING_TIME"
            elif decision_reason == "MISSING_DATE":
                clarification_reason = "MISSING_DATE"
            elif decision_reason == "MISSING_START_DATE":
                clarification_reason = "MISSING_DATE"  # Use MISSING_DATE for start
            elif decision_reason == "MISSING_END_DATE":
                clarification_reason = "MISSING_DATE"  # Could add MISSING_END_DATE if needed
            elif decision_reason == "temporal_shape_not_satisfied":
                # Determine which slot is missing from missing_slots
                if "time" in missing_slots:
                    clarification_reason = "MISSING_TIME"
                elif "date" in missing_slots:
                    clarification_reason = "MISSING_DATE"
            else:
                clarification_reason = decision_reason  # Use as-is if it matches enum
            status = "needs_clarification"

    # Fallback: map missing slots to reasons
    if not clarification_reason and missing_slots:
        if "time" in missing_slots:
            clarification_reason = "MISSING_TIME"
        elif "date" in missing_slots:
            clarification_reason = "MISSING_DATE"
        elif "service_id" in missing_slots or "service" in missing_slots:
            clarification_reason = "MISSING_SERVICE"
        elif "booking_id" in missing_slots:
            clarification_reason = "MISSING_BOOKING_REFERENCE"

    return {
        "status": status,
        "missing_slots": missing_slots,
        "clarification_reason": clarification_reason
    }




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

    # TIME_ISSUES: Prefer current (new parsing issues), fallback to memory
    current_time_issues = current_resolved_booking.get("time_issues", [])
    memory_time_issues = memory_booking.get("time_issues", [])
    merged["time_issues"] = current_time_issues if current_time_issues else memory_time_issues

    return merged


def init_pipeline():
    """Initialize the pipeline components."""
    global entity_matcher, intent_resolver, memory_store  # noqa: PLW0603

    logger.info("=" * 60)
    logger.info("Initializing Luma Service/Reservation Booking Pipeline")

    try:
        # Initialize intent resolver (lightweight, no file I/O)
        intent_resolver = ReservationIntentResolver()

        # Initialize memory store (required for multi-turn conversations)
        memory_store = RedisMemoryStore()
        logger.info("Memory store initialized successfully")

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
    return resolve_message(
        # Flask request globals
        g=g,
        request=request,
        # Module globals
        intent_resolver=intent_resolver,
        memory_store=memory_store,
        logger=logger,
        # Constants
        APPOINTMENT_TEMPORAL_TYPE_CONST=APPOINTMENT_TEMPORAL_TYPE,
        INTENT_TEMPORAL_SHAPE_CONST=INTENT_TEMPORAL_SHAPE,
        MEMORY_TTL=config.MEMORY_TTL,
        # Helper functions
        _merge_semantic_results=_merge_semantic_results,
        _localize_datetime=_localize_datetime,
        find_normalization_dir=find_normalization_dir,
        _get_business_categories=_get_business_categories,
        _count_mutable_slots_modified=_count_mutable_slots_modified,
        _has_booking_verb=_has_booking_verb,
        validate_required_slots=validate_required_slots,
        _build_issues=_build_issues,
        _format_service_for_response=_format_service_for_response,
        plan_clarification=plan_clarification,
        _log_stage=_log_stage,
    )


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
    
    # TEMPORARY: Log Redis configuration at startup
    redis_password_masked = "***" if config.REDIS_PASSWORD else None
    logger.info(
        f"Redis Config: host={config.REDIS_HOST}, port={config.REDIS_PORT}, "
        f"db={config.REDIS_DB}, password={redis_password_masked}, ttl={config.MEMORY_TTL}s",
        extra={
            "redis_host": config.REDIS_HOST,
            "redis_port": config.REDIS_PORT,
            "redis_db": config.REDIS_DB,
            "redis_password": redis_password_masked,
            "memory_ttl": config.MEMORY_TTL
        }
    )

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
