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
from luma.response.builder import build_issues, format_service_for_response
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

# Enable slot tracking for debugging (can also use LOG_SLOT_TRACKING env var)
# config.LOG_SLOT_TRACKING = True  # Uncomment to enable

from luma.config.temporal import (  # noqa: E402
    APPOINTMENT_TEMPORAL_TYPE,
    RESERVATION_TEMPORAL_TYPE,
    TimeMode,
)
from luma.config.intent_meta import validate_required_slots  # noqa: E402
from luma.config.logging import setup_logging, generate_request_id  # noqa: E402
from luma.decision import decide_booking_status  # noqa: E402
from luma.clarification import ClarificationReason  # noqa: E402
from luma.pipeline import LumaPipeline  # noqa: E402
from luma.trace import validate_stable_fields, TRACE_VERSION  # noqa: E402
from luma.perf import StageTimer  # noqa: E402
from luma.config.core import STATUS_READY, STATUS_NEEDS_CLARIFICATION  # noqa: E402
from luma.app.resolve_service import resolve_message  # noqa: E402

# Check for required dependencies at startup


def _check_required_dependencies():
    """Check for required dependencies and fail fast if missing."""
    missing_deps = []

    # Check rapidfuzz (required for fuzzy matching in tenant alias detection)
    try:
        import rapidfuzz  # noqa: F401
    except ImportError:
        missing_deps.append("rapidfuzz")

    if missing_deps:
        error_msg = (
            f"ERROR: Missing required dependencies: {', '.join(missing_deps)}\n"
            f"Please install them using: pip install {' '.join(missing_deps)}\n"
            f"Or uncomment them in luma/requirements.txt and install all requirements."
        )
        print(error_msg, file=sys.stderr)
        sys.exit(1)


# Check dependencies before initializing app
_check_required_dependencies()

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
    config_data_dir = current_file.parent / "config" / "data"
    if config_data_dir.exists():
        _normalization_dir_cache = config_data_dir
        return _normalization_dir_cache
    # Fallback to old location for backward compatibility
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


# Response building functions moved to luma.response.builder
# Keep aliases for backward compatibility
_build_issues = build_issues
_format_service_for_response = format_service_for_response


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
    decision_result: Optional[Any] = None,
    decision_trace: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Plan clarification payload based on resolver slots and semantic clarifications.

    Args:
        intent_result: dict from _build_response (intent, confidence, status, missing_slots)
        entities: extraction entities
        semantic_result: SemanticResolutionResult or None
        decision_result: DecisionResult or None
        decision_trace: Optional decision trace containing missing_slots from temporal shape validation

    Returns:
        Dict with status, missing_slots, and clarification_reason
    """
    status = intent_result.get("status", STATUS_READY)
    missing_slots = intent_result.get("missing_slots", []) or []
    clarification_reason = None

    # CRITICAL: Merge decision layer's missing_slots FIRST (before semantic clarifications)
    # This ensures temporal shape validation results are preserved even when semantic clarifications exist
    # For MODIFY_BOOKING: decision layer's specific deltas (date/time or start_date/end_date) take precedence
    # over intent resolver's generic "change" marker
    if decision_trace and isinstance(decision_trace, dict):
        decision_missing = decision_trace.get("missing_slots", [])
        if decision_missing:
            # Extract intent name to check if it's MODIFY_BOOKING
            intent_name = intent_result.get("intent")
            if isinstance(intent_name, dict):
                intent_name = intent_name.get("name")
            elif not isinstance(intent_name, str):
                intent_name = None
            
            # For MODIFY_BOOKING: decision layer is authoritative for missing_slots
            # Decision layer handles generic vs specific wording correctly based on booking_mode
            # Use decision layer's missing_slots directly, only merge with intent resolver's if decision layer didn't provide any
            if intent_name == "MODIFY_BOOKING":
                # Decision layer is authoritative for MODIFY_BOOKING missing_slots
                # It correctly handles generic wording (["change"]) vs specific wording (["date", "time"] or ["start_date", "end_date"])
                # Use decision layer's missing_slots directly, but also include booking_id if present from intent resolver
                if decision_missing:
                    # Decision layer provided missing_slots - use those as authoritative
                    # Remove "change" from intent resolver's missing_slots if decision layer provided specific deltas
                    specific_deltas = {"date", "time", "start_date", "end_date"}
                    decision_has_specific_deltas = any(slot in specific_deltas for slot in decision_missing)
                    decision_has_change = "change" in decision_missing
                    
                    if decision_has_specific_deltas:
                        # Decision layer returned specific deltas - use those, remove "change" from intent resolver's
                        missing_slots = [s for s in missing_slots if s != "change"]  # Remove "change" if present
                        missing_slots = list(set(missing_slots + decision_missing))  # Merge with decision deltas
                    elif decision_has_change:
                        # Decision layer returned ["change"] for generic wording - use that, remove specific deltas from intent resolver's
                        # Remove any specific deltas that might have been added by intent resolver
                        intent_specific_deltas = {"date", "time", "start_date", "end_date"}
                        missing_slots = [s for s in missing_slots if s not in intent_specific_deltas]
                        missing_slots = list(set(missing_slots + decision_missing))  # Merge with decision ["change"]
                    else:
                        # Decision layer provided other missing_slots (e.g., ["booking_id", "date", "time"])
                        # Merge with intent resolver's missing_slots
                        missing_slots = list(set(missing_slots + decision_missing))
            else:
                # For other intents: standard merge
                missing_slots = list(set(missing_slots + decision_missing))

    # Prefer semantic clarifications (e.g., MULTIPLE_MATCHES, MISSING_DATE_RANGE) if present
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
            
            # Extract missing_slots from clarification data and merge
            # This ensures MISSING_DATE_RANGE properly normalizes missing slots
            clar_data = sem_clar.get("data", {})
            clar_missing_slots = clar_data.get("missing_slots", [])
            if clar_missing_slots:
                # Merge semantic clarification's missing_slots with intent resolver's
                missing_slots = list(set(missing_slots + clar_missing_slots))
            
            status = STATUS_NEEDS_CLARIFICATION

    # Check for ambiguous meridiem in time_issues FIRST (before decision layer)
    # This takes priority because ambiguous meridiem is more specific than MISSING_TIME
    if not clarification_reason and semantic_result:
        resolved_booking = semantic_result.resolved_booking
        time_issues = resolved_booking.get("time_issues", [])
        for issue in time_issues:
            if issue.get("kind") == "ambiguous_meridiem":
                clarification_reason = ClarificationReason.AMBIGUOUS_TIME_MERIDIEM.value
                status = STATUS_NEEDS_CLARIFICATION
                break

    # Check decision layer for temporal shape violations
    # NOTE: We still check decision layer even if semantic clarification exists, to merge missing_slots
    if decision_result and decision_result.status == "NEEDS_CLARIFICATION":
        decision_reason = decision_result.reason
        if decision_reason and not clarification_reason:  # Only use if no more specific reason set
            # Map decision layer reasons to ClarificationReason enum values
            if decision_reason == "MISSING_TIME":
                clarification_reason = ClarificationReason.MISSING_TIME.value
            elif decision_reason == "MISSING_DATE":
                clarification_reason = ClarificationReason.MISSING_DATE.value
            elif decision_reason == "MISSING_START_DATE":
                # Use MISSING_DATE for start
                clarification_reason = ClarificationReason.MISSING_DATE.value
            elif decision_reason == "MISSING_END_DATE":
                # Could add MISSING_END_DATE if needed
                clarification_reason = ClarificationReason.MISSING_DATE.value
            elif decision_reason == "temporal_shape_not_satisfied":
                # Determine which slot is missing from missing_slots
                if "time" in missing_slots:
                    clarification_reason = ClarificationReason.MISSING_TIME.value
                elif "date" in missing_slots:
                    clarification_reason = ClarificationReason.MISSING_DATE.value
            else:
                clarification_reason = decision_reason  # Use as-is if it matches enum
            status = STATUS_NEEDS_CLARIFICATION

    # Check for MISSING_DATE_RANGE first (specific reason for weekday-only ranges)
    # This takes priority over generic missing slot mapping
    if clarification_reason == ClarificationReason.MISSING_DATE_RANGE.value:
        # Ensure both start_date and end_date are in missing_slots
        if "start_date" not in missing_slots:
            missing_slots.append("start_date")
        if "end_date" not in missing_slots:
            missing_slots.append("end_date")
    
    # Fallback: map missing slots to reasons
    if not clarification_reason and missing_slots:
        # MODIFY_BOOKING delta semantics: "change" placeholder means booking_id exists but no deltas
        # This indicates at least one delta slot (date, time, service_id, etc.) is required
        # Keep "change" in missing_slots to indicate what needs clarification
        if "change" in missing_slots:
            # Extract intent name to check if it's MODIFY_BOOKING
            intent_name = intent_result.get("intent")
            if isinstance(intent_name, dict):
                intent_name = intent_name.get("name")
            elif not isinstance(intent_name, str):
                intent_name = None
            
            if intent_name == "MODIFY_BOOKING":
                # booking_id is present but no deltas - indicate clarification needed
                # Use MISSING_CONTEXT since we're asking "what should be modified?" (not missing booking_id)
                clarification_reason = ClarificationReason.MISSING_CONTEXT.value
                # Keep "change" in missing_slots - it indicates what needs clarification
        
        if not clarification_reason:
            if "time" in missing_slots:
                clarification_reason = ClarificationReason.MISSING_TIME.value
            elif "date" in missing_slots:
                clarification_reason = ClarificationReason.MISSING_DATE.value
            elif "service_id" in missing_slots or "service" in missing_slots:
                clarification_reason = ClarificationReason.MISSING_SERVICE.value
            elif "booking_id" in missing_slots:
                clarification_reason = ClarificationReason.MISSING_BOOKING_REFERENCE.value
            elif "start_date" in missing_slots and "end_date" in missing_slots:
                # If both start_date and end_date are missing, use MISSING_DATE_RANGE
                clarification_reason = ClarificationReason.MISSING_DATE_RANGE.value

    return {
        "status": status,
        "missing_slots": missing_slots,
        "clarification_reason": clarification_reason
    }


def init_pipeline():
    """Initialize the pipeline components."""
    global entity_matcher, intent_resolver  # noqa: PLW0603

    logger.info("=" * 60)
    logger.info("Initializing Luma Service/Reservation Booking Pipeline")

    try:
        # Initialize intent resolver (lightweight, no file I/O)
        intent_resolver = ReservationIntentResolver()

        # Pre-load vocabularies to avoid first-request latency
        from luma.resolution.semantic_resolver import initialize_vocabularies
        initialize_vocabularies()
        logger.info("Vocabularies pre-loaded successfully")

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
        logger=logger,
        # Constants
        APPOINTMENT_TEMPORAL_TYPE_CONST=APPOINTMENT_TEMPORAL_TYPE,
        # Helper functions
        _localize_datetime=_localize_datetime,
        find_normalization_dir=find_normalization_dir,
        _get_business_categories=_get_business_categories,
        _count_mutable_slots_modified=_count_mutable_slots_modified,
        _has_booking_verb=_has_booking_verb,
        validate_required_slots=validate_required_slots,
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
