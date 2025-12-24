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
import yaml  # noqa: E402
from luma.calendar.calendar_binder import bind_calendar, _bind_times, _combine_datetime_range, _get_timezone, _get_booking_policy, CalendarBindingResult  # noqa: E402
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
from luma.logging_config import setup_logging, generate_request_id  # noqa: E402
from luma.memory import RedisMemoryStore  # noqa: E402
from luma.memory.merger import merge_booking_state, extract_memory_state_for_response  # noqa: E402
from luma.decision import decide_booking_status  # noqa: E402
from luma.clarification import ClarificationReason  # noqa: E402
from luma.pipeline import LumaPipeline  # noqa: E402
from luma.trace_contract import validate_stable_fields, TRACE_VERSION  # noqa: E402
from luma.perf import StageTimer  # noqa: E402

# Cache for intent metadata (required_slots, etc.)
_INTENT_META_CACHE = {}


def _load_intent_meta() -> Dict[str, Dict[str, Any]]:
    """Load intent metadata (including required_slots) from intent_signals.yaml."""
    global _INTENT_META_CACHE
    if _INTENT_META_CACHE:
        return _INTENT_META_CACHE
    path = (
        Path(__file__).resolve().parent
        / "store"
        / "normalization"
        / "intent_signals.yaml"
    )
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    intents_cfg = raw.get("intents", raw) if isinstance(raw, dict) else {}
    for intent, cfg in intents_cfg.items():
        if isinstance(cfg, dict):
            _INTENT_META_CACHE[intent] = cfg
    return _INTENT_META_CACHE


def validate_required_slots(intent_name: str, resolved_slots: Dict[str, Any], entities: Dict[str, Any]) -> List[str]:
    """
    Validate required slots from intent_signals.yaml.
    Returns list of missing slots.
    """
    intent_meta = _load_intent_meta().get(intent_name, {}) or {}
    required_slots = intent_meta.get("required_slots") or []
    missing: List[str] = []
    temporal_shape = INTENT_TEMPORAL_SHAPE.get(intent_name)

    def _slot_present(slot: str) -> bool:
        val = resolved_slots.get(slot)
        if val is None or val == "" or val == []:
            val = entities.get(slot)
        if val:
            return True
        # Special cases
        if slot == "date":
            return bool(resolved_slots.get("date_refs"))
        if slot == "time":
            return bool(
                resolved_slots.get("time_refs")
                or resolved_slots.get("time_constraint")
                or resolved_slots.get("time_range")
                or resolved_slots.get("datetime_range")
            )
        if slot == "start_date":
            refs = resolved_slots.get("date_refs") or []
            return len(refs) >= 1
        if slot == "end_date":
            refs = resolved_slots.get("date_refs") or []
            return len(refs) >= 2
        if slot == "booking_id":
            bid = entities.get("booking_id")
            return bool(bid)
        return False

    for slot in required_slots:
        if not _slot_present(slot):
            missing.append(slot)

    # Temporal-shape based enforcement
    if temporal_shape == APPOINTMENT_TEMPORAL_TYPE:
        # Requires both date and time; fuzzy time not allowed
        has_date = bool(resolved_slots.get("date_refs"))
        has_time = False
        time_refs = resolved_slots.get("time_refs") or []
        if time_refs:
            has_time = True
        else:
            tc = resolved_slots.get("time_constraint") or {}
            tc_mode = tc.get("mode")
            if tc_mode in {TimeMode.EXACT.value, TimeMode.WINDOW.value, TimeMode.RANGE.value}:
                has_time = True
            elif tc_mode == TimeMode.FUZZY.value:
                has_time = False  # fuzzy not allowed for appointments
        if not has_date and "date" not in missing:
            missing.append("date")
        if not has_time and "time" not in missing:
            missing.append("time")
    elif temporal_shape == RESERVATION_TEMPORAL_TYPE:
        # Requires start_date; end_date if configured
        date_refs = resolved_slots.get("date_refs") or []
        if len(date_refs) < 1 and "start_date" not in missing:
            missing.append("start_date")
        require_end = INTENT_REQUIRE_END_DATE.get(
            intent_name) or INTENT_REQUIRE_END_DATE.get("CREATE_RESERVATION")
        # Enforce two refs (or explicit end_date) when require_end is True
        end_present = bool(resolved_slots.get(
            "end_date")) or len(date_refs) >= 2
        if require_end and not end_present and "end_date" not in missing:
            missing.append("end_date")

    return missing


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
            # If an active booking exists, force intent to CREATE_BOOKING regardless of raw classification
            # This ensures merge logic always runs for continuations like "at 10" or "make it 10"
            if _has_active_booking(memory_state):
                original_intent = intent
                intent = "CREATE_BOOKING"
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

            # Merge semantic results: preserve memory, fill with current
            merged_resolved_booking = _merge_semantic_results(
                memory_resolved_booking,
                semantic_result.resolved_booking
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

            # Removed per-stage logging - consolidated trace emitted at end

            # Removed per-stage logging - consolidated trace emitted at end

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
                logger.debug(
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
                        logger.debug(
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
                        # Time memory write operation
                        with StageTimer(execution_trace, "memory", request_id=request_id):
                            memory_store.set(
                                user_id=user_id,
                                domain=domain,
                                state=memory_state,
                                ttl=config.MEMORY_TTL
                            )
                        logger.debug(
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
                    logger.debug(
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
                    logger.debug(
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
                logger.debug(
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
                        logger.debug(
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
                        # Time memory write operation
                        with StageTimer(execution_trace, "memory", request_id=request_id):
                            memory_store.set(
                                user_id=user_id,
                                domain=domain,
                                state=memory_state,
                                ttl=config.MEMORY_TTL
                            )
                        logger.debug(
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
                    logger.debug(
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
                    logger.debug(
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
        try:
            # Load booking policy from config
            booking_policy = _get_booking_policy()

            # CRITICAL INVARIANT: decision must see fully merged semantics
            # If there is an active booking, the semantic object passed to decision
            # MUST be the merged semantic booking, not the current fragment
            if memory_state and _has_active_booking(memory_state) and merged_semantic_result:
                semantic_for_decision = merged_semantic_result.resolved_booking
            else:
                semantic_for_decision = semantic_result.resolved_booking
            # Attach booking_mode for decision policy (service vs reservation)
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
            if expected_shape == APPOINTMENT_TEMPORAL_TYPE:
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
                has_service = len(
                    _get_business_categories(extraction_result)) > 0
                has_booking_verb = _has_booking_verb(text)

                # Safety: No booking_id, no service, no booking verb, and at least one mutable slot
                # IMPORTANT: Apply CONTEXTUAL_UPDATE even if intent is UNKNOWN when time/date/duration is extracted
                # This handles cases like "6p," where normalization fixes the time but intent resolver returns UNKNOWN
                if mutable_slots_modified >= 1 and not has_service and not has_booking_verb:
                    effective_intent = CONTEXTUAL_UPDATE
                    # Override intent for processing, but keep original for logging
                    logger.debug(
                        f"Detected CONTEXTUAL_UPDATE for user {user_id}",
                        extra={'request_id': request_id,
                               'original_intent': intent,
                               'slots_modified': mutable_slots_modified}
                    )
            elif intent == "CREATE_BOOKING" and _is_partial_booking(memory_state):
                # CREATE_BOOKING with PARTIAL memory is already handled above as continuation
                # This is just for logging - the merge already happened
                logger.debug(
                    f"CREATE_BOOKING continuation of PARTIAL booking for user {user_id}",
                    extra={'request_id': request_id}
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
                    temporal_shape_for_trace = INTENT_TEMPORAL_SHAPE.get(
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
                temporal_shape_for_trace = INTENT_TEMPORAL_SHAPE.get(
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
        if intent_name and intent_name in INTENT_TEMPORAL_SHAPE:
            shape = INTENT_TEMPORAL_SHAPE.get(intent_name)
            if shape == APPOINTMENT_TEMPORAL_TYPE:
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
                            # CONTEXTUAL_UPDATE is for appointments, so pass external_intent=None
                            # (reservations don't use CONTEXTUAL_UPDATE)
                            new_datetime_range = _combine_datetime_range(
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
                    # Time memory write operation
                    with StageTimer(execution_trace, "memory", request_id=request_id):
                        memory_store.set(
                            user_id=user_id,
                            domain=domain,
                            state=merged_memory,
                            ttl=config.MEMORY_TTL
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
                    # Time memory write operation
                    with StageTimer(execution_trace, "memory", request_id=request_id):
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
                if calendar_booking.get("datetime_range"):
                    booking_payload["datetime_range"] = calendar_booking.get(
                        "datetime_range")
                booking_payload.pop("date", None)
                booking_payload.pop("time", None)
                booking_payload.pop("date_range", None)
                booking_payload.pop("time_range", None)
                booking_payload.pop("start_date", None)
                booking_payload.pop("end_date", None)

            # Reservation responses: only start_date / end_date
            if intent_payload_name == "CREATE_RESERVATION":
                if calendar_booking.get("start_date"):
                    booking_payload["start_date"] = calendar_booking.get(
                        "start_date")
                if calendar_booking.get("end_date"):
                    booking_payload["end_date"] = calendar_booking.get(
                        "end_date")
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
                if booking_payload.get("datetime_range") or calendar_booking.get("datetime_range"):
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
