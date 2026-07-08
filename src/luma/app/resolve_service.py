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

import logging
import os
import re
import time
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any, Dict, List, Optional

from luma.calendar.calendar_binder import (
    CalendarBindingResult,
    _bind_single_date,
    _localize_datetime,
    bind_calendar,
    bind_times,
    combine_datetime_range,
    get_booking_policy,
    get_timezone,
)
from luma.config import config
from luma.config.core import (
    STATUS_NEEDS_CLARIFICATION,
    STATUS_PARTIAL,
    STATUS_READY,
    STATUS_RESOLVED,
)
from luma.config.intent_meta import get_intent_registry
from luma.config.temporal import (
    APPOINTMENT_TEMPORAL_TYPE,
    RESERVATION_TEMPORAL_TYPE,
    DateMode,
    TimeMode,
)
from luma.extraction.date_time_pairing import (
    detect_date_time_pairs,
    normalize_date_time_pairs,
)
from luma.perf import StageTimer
from luma.pipeline import LumaPipeline
from luma.resolution.semantic_resolver import SemanticResolutionResult
from luma.response.builder import (
    ResponseBuilder,
    build_issues,
    format_service_for_response,
)
from luma.trace import log_field_removal, validate_stable_fields
from luma.trace.stage_snapshot import capture_stage_snapshot


def aggregate_extraction_facts(
    extraction_result: Optional[Dict[str, Any]] = None,
    slots: Optional[Dict[str, Any]] = None,
    intent: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Aggregate extraction facts from extraction_result and slots.

    Collects facts from all extraction sources:
    - service_id: from slots (resolved service)
    - dates[]: from slots (normalized dates)
    - times[]: from slots (normalized times)
    - date_time_pairs[]: from slots (explicit pairings)
    - booking_id: from extraction_result or slots

    Does NOT infer or validate completeness.
    Only collects facts that are explicitly present.

    Args:
        extraction_result: Raw extraction result from pipeline
        slots: Normalized slots dict with resolved dates/times/service_id

    Returns:
        Facts dict with structure:
        {
            "service_id": str,
            "dates": List[str],
            "times": List[str],
            "date_time_pairs": List[Dict],
            "booking_id": str
        }
    """
    facts: Dict[str, Any] = {}
    slots = slots or {}  # Null safety

    # Collect date_time_pairs[] from slots first (explicit pairings)
    # When paired, we'll remove those dates/times from individual arrays
    date_time_pairs = []
    if slots.get("date_time_pairs"):
        date_time_pairs = slots["date_time_pairs"]
        facts["date_time_pairs"] = date_time_pairs

    # Extract paired dates and times from date_time_pairs to exclude from individual arrays
    paired_dates = set()
    paired_times = set()
    if date_time_pairs:
        for pair in date_time_pairs:
            if isinstance(pair, dict):
                pair_date = pair.get("date")
                pair_time = pair.get("time")
                if pair_date:
                    paired_dates.add(pair_date)
                if pair_time:
                    paired_times.add(pair_time)

    # Collect dates[] from slots (normalized dates)
    # EXCLUDE dates that are part of date_time_pairs
    dates = []
    # Handle single date or list of dates
    if slots.get("date"):
        date_value = slots["date"]
        if isinstance(date_value, list):
            dates.extend(date_value)
        else:
            dates.append(date_value)
    # Handle date_range (legacy)
    if slots.get("date_range"):
        date_range = slots["date_range"]
        start = date_range.get("start") or date_range.get("start_date")
        end = date_range.get("end") or date_range.get("end_date")
        if start:
            dates.append(start)
        if end and end != start:
            dates.append(end)

    # Filter out paired dates
    if dates:
        unpaired_dates = [d for d in dates if d not in paired_dates]
        if unpaired_dates:
            # Remove duplicates while preserving order
            seen = set()
            facts["dates"] = [
                d for d in unpaired_dates if d not in seen and not seen.add(d)
            ]

    # APPOINTMENT INTENT RULE: Do NOT emit facts.times or facts.time for appointment intents
    # All temporal information for appointments should ONLY appear in time_constraint
    # Skip facts.times emission entirely for CREATE_APPOINTMENT intents
    if intent != "CREATE_APPOINTMENT":
        # Collect times[] from slots (normalized times)
        # EXCLUDE times that are part of date_time_pairs
        # Support both single time string and list of times
        times_list = []
        time_slot = slots.get("time")
        if time_slot:
            if isinstance(time_slot, list):
                times_list.extend(time_slot)
            else:
                times_list.append(time_slot)

        # Filter out paired times
        if times_list:
            unpaired_times = [t for t in times_list if t not in paired_times]
            if unpaired_times:
                facts["times"] = unpaired_times

    # Collect service_id from slots (resolved service)
    if slots.get("service_id"):
        facts["service_id"] = slots["service_id"]

    # Collect booking_id from extraction_result or slots
    booking_id = None
    if slots.get("booking_id"):
        booking_id = slots["booking_id"]
    elif extraction_result and extraction_result.get("booking_id"):
        booking_id = extraction_result["booking_id"]
    if booking_id:
        facts["booking_id"] = booking_id

    return facts


from flask import jsonify

from luma.config.conversation_signals import (
    get_confirmation_phrases,
    get_confirmation_terms,
    is_confirmation_enabled,
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


def _has_temporal_tokens(psentence: str) -> bool:
    """
    Check if parameterized sentence contains temporal tokens.

    Temporal resolution should run if the sentence contains:
    - datetoken (dates)
    - timetoken (times)
    - timewindowtoken (time windows)

    This enables temporal resolution for standalone temporal inputs
    (e.g., "friday", "next week", "tomorrow evening") even when
    intent is UNKNOWN.

    Args:
        psentence: Parameterized sentence string

    Returns:
        True if sentence contains temporal tokens, False otherwise
    """
    if not psentence:
        return False

    psentence_lower = psentence.lower()
    temporal_tokens = ["datetoken", "timetoken", "timewindowtoken"]
    return any(token in psentence_lower for token in temporal_tokens)


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


def _try_normalize_weekday(
    date_str: str, now_tz_aware: datetime, tz: Any
) -> Optional[str]:
    """
    Try to normalize a date string as a weekday if it matches weekday patterns.

    This ensures raw weekday tokens like "friday" are always normalized to ISO dates
    and never leak into facts.dates as raw tokens.

    Args:
        date_str: Date string to normalize (e.g., "friday", "monday")
        now_tz_aware: Current datetime (timezone-aware)
        tz: Timezone object

    Returns:
        ISO date string (YYYY-MM-DD) if normalization succeeds, None otherwise
    """
    if not date_str or not isinstance(date_str, str):
        return None

    weekday_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    date_lower = date_str.lower().strip()

    if date_lower in weekday_map:
        # Normalize bare weekday to next occurrence
        target_weekday = weekday_map[date_lower]
        today_weekday = now_tz_aware.weekday()
        days_ahead = (target_weekday - today_weekday) % 7
        if days_ahead == 0:
            days_ahead = 7
        import datetime as dt_module

        target_date = now_tz_aware + dt_module.timedelta(days=days_ahead)
        return target_date.strftime("%Y-%m-%d")

    return None


def build_datetime_range_for_api(
    slots: Dict[str, Any],
    semantic_booking: Dict[str, Any],
    domain: str,
    request_id: Optional[str] = None,
    user_id: Optional[str] = None,
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
    is_appointment_modify = semantic_booking_mode == "service" or domain == "service"

    if not is_appointment_modify:
        return

    # Skip if datetime_range already exists
    if slots.get("datetime_range"):
        return

    # Check if date is present (date_mode != "none" and date_refs exist)
    has_date = (
        semantic_booking.get("date_mode") is not None
        and semantic_booking.get("date_mode") != "none"
        and semantic_booking.get("date_refs")
    )

    # STAGE 2: Gate has_datetime on time_constraint.mode == "exact" AND date present
    # Fuzzy inputs like "morning", "evening", "by 5am" must NOT set has_datetime = True
    time_constraint = semantic_booking.get("time_constraint")
    has_exact_time_constraint = (
        time_constraint is not None
        and isinstance(time_constraint, dict)
        and time_constraint.get("mode") == "exact"
    )

    # Set has_datetime ONLY if:
    # 1. time_constraint exists AND mode == "exact" (exact time)
    # 2. AND date is present
    # This prevents fuzzy times (morning/evening/window constraints) from setting has_datetime
    has_datetime_condition = has_exact_time_constraint and has_date

    # Set has_datetime if already set, or if exact time constraint + date is present
    if slots.get("has_datetime") or has_datetime_condition:
        # Set has_datetime if not already set
        if not slots.get("has_datetime"):
            slots["has_datetime"] = True
            logger.info(
                f"[slots] MODIFY_BOOKING appointment: set has_datetime=True (time or date present, "
                f"has_time={has_time}, has_date={has_date}, "
                f"booking_mode={semantic_booking_mode}, "
                f"date_mode={semantic_booking.get('date_mode')}, time_mode={semantic_booking.get('time_mode')})",
                extra={"request_id": request_id, "user_id": user_id},
            )

        # Build minimal datetime_range when has_datetime is True but datetime_range is missing
        # This is required by the API contract: if has_datetime=True, datetime_range must exist
        time_refs = semantic_booking.get("time_refs", [])
        if time_refs:
            # Create a minimal datetime_range with time reference (date will be resolved later if needed)
            # Start and end are identical - this is a compatibility structure, not temporal resolution
            slots["datetime_range"] = {
                "start": time_refs[0] if time_refs else None,
                "end": time_refs[0] if time_refs else None,
            }
        else:
            # Fallback: create empty datetime_range structure (will be populated if date/time are bound)
            slots["datetime_range"] = {"start": None, "end": None}
        logger.info(
            f"[slots] MODIFY_BOOKING appointment: built minimal datetime_range for has_datetime=True "
            f"(time_refs={time_refs})",
            extra={"request_id": request_id, "user_id": user_id},
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
    request_id = g.request_id if hasattr(g, "request_id") else "unknown"
    booking_payload: Optional[Dict[str, Any]] = None
    calendar_booking: Dict[str, Any] = {}

    if intent_resolver is None:
        logger.error("Pipeline not initialized", extra={"request_id": request_id})
        return jsonify({"success": False, "error": "Pipeline not initialized"}), 503

    # Parse request
    try:
        data = request.get_json()
        if not data:
            logger.warning("Missing request body", extra={"request_id": request_id})
            return jsonify({"success": False, "error": "Missing request body"}), 400

        # Require user_id
        if "user_id" not in data:
            logger.warning(
                "Missing 'user_id' parameter", extra={"request_id": request_id}
            )
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Missing 'user_id' parameter in request body",
                    }
                ),
                400,
            )

        user_id = data["user_id"]
        if not user_id or not isinstance(user_id, str):
            logger.warning(
                "Invalid user_id parameter", extra={"request_id": request_id}
            )
            return (
                jsonify(
                    {"success": False, "error": "'user_id' must be a non-empty string"}
                ),
                400,
            )

        if "text" not in data:
            logger.warning("Missing 'text' parameter", extra={"request_id": request_id})
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Missing 'text' parameter in request body",
                    }
                ),
                400,
            )

        text = data["text"]
        domain = data.get("domain", "service")
        timezone = data.get("timezone", "UTC")
        # Optional tenant context with aliases
        tenant_context = data.get("tenant_context")

        # Log tenant_context for debugging
        if tenant_context:
            aliases_count = (
                len(tenant_context.get("aliases", {}))
                if isinstance(tenant_context.get("aliases"), dict)
                else 0
            )
            aliases = (
                tenant_context.get("aliases", {})
                if isinstance(tenant_context, dict)
                else {}
            )
            booking_mode = (
                tenant_context.get("booking_mode")
                if isinstance(tenant_context, dict)
                else None
            )
            logger.info(
                f"Received tenant_context with {aliases_count} aliases",
                extra={
                    "request_id": request_id,
                    "aliases_count": aliases_count,
                    "aliases": aliases,
                    "booking_mode": booking_mode,
                },
            )

        if not text or not isinstance(text, str):
            logger.warning("Invalid text parameter", extra={"request_id": request_id})
            return (
                jsonify(
                    {"success": False, "error": "'text' must be a non-empty string"}
                ),
                400,
            )

        # Luma is now stateless - no memory storage or recall
        memory_state = None
        # Initialize execution_trace
        execution_trace = {"timings": {}}

        # Early option-constrained resolution (before pipeline execution)
        # If tenant_context.options is present, validate input against provided options
        if tenant_context and isinstance(tenant_context, dict):
            options = tenant_context.get("options")
            if options:
                from luma.grouping.reservation_intent_resolver import HIGH_CONFIDENCE
                from luma.resolution.option_resolver import resolve_option

                # Ensure HIGH_CONFIDENCE is numeric (0.95)
                confidence_value = float(HIGH_CONFIDENCE) if HIGH_CONFIDENCE else 0.95

                logger.info(
                    f"Option-constrained resolution: checking input '{text}' against {len(options.get('choices', []))} options",
                    extra={
                        "request_id": request_id,
                        "text": text,
                        "slot": options.get("slot"),
                        "num_choices": len(options.get("choices", [])),
                    },
                )

                result = resolve_option(text, options)
                if result:
                    # Valid option resolved - return early with READY status
                    logger.info(
                        f"Option resolved successfully: {result['slot']}={result['value']}",
                        extra={
                            "request_id": request_id,
                            "slot": result["slot"],
                            "value": result["value"],
                            "input_text": text,
                        },
                    )
                    # Build success response
                    slots = slots or {}
                    slots[result["slot"]] = result["value"]
                    # Use HIGH_CONFIDENCE (0.95) for deterministic option resolution
                    return (
                        jsonify(
                            {
                                "success": True,
                                "status": STATUS_READY,
                                "intent": {
                                    "name": "UNKNOWN",
                                    "confidence": confidence_value,
                                },
                                "slots": slots,
                                "needs_clarification": False,
                                "clarification": None,
                            }
                        ),
                        200,
                    )
                else:
                    # Invalid or ambiguous option - return early with NEEDS_CLARIFICATION
                    logger.info(
                        f"Option resolution failed: input '{text}' does not match any valid option",
                        extra={
                            "request_id": request_id,
                            "text": text,
                            "slot": options.get("slot"),
                            "num_choices": len(options.get("choices", [])),
                        },
                    )
                    # Build clarification response
                    choices = options.get("choices", [])
                    slot = options.get("slot", "unknown")
                    # EXTRACTION-ONLY: Removed INVALID_OPTION clarification - just return UNKNOWN intent
                    # Use HIGH_CONFIDENCE (0.95) for deterministic option resolution
                    return (
                        jsonify(
                            {
                                "success": True,
                                "status": STATUS_READY,
                                "intent": {
                                    "name": "UNKNOWN",
                                    "confidence": confidence_value,
                                },
                                "facts": {},
                            }
                        ),
                        200,
                    )

    except Exception as e:  # noqa: BLE001
        logger.error(
            f"Invalid request format: {str(e)}",
            extra={"request_id": request_id},
            exc_info=True,
        )
        return (
            jsonify({"success": False, "error": f"Invalid request format: {str(e)}"}),
            400,
        )

    # Process conversational input
    try:
        start_time = time.perf_counter()

        # Find normalization directory
        normalization_dir = find_normalization_dir()
        if not normalization_dir:
            return (
                jsonify(
                    {"success": False, "error": "Normalization directory not found"}
                ),
                500,
            )

        entity_file = str(normalization_dir / "101.v1.json")

        # Initialize now datetime
        # Allow override via test_now in request payload (for testing) or LUMA_TEST_NOW environment variable
        # Request payload takes precedence over environment variable
        test_now = data.get("test_now") or os.getenv("LUMA_TEST_NOW")
        if test_now:
            try:
                # Parse ISO format datetime string (supports both with and without Z suffix)
                now_str = (
                    test_now.replace("Z", "+00:00")
                    if test_now.endswith("Z")
                    else test_now
                )
                now = datetime.fromisoformat(now_str)
                if now.tzinfo is None:
                    now = _localize_datetime(now, timezone)
                logger.debug(
                    f"Using LUMA_TEST_NOW='{test_now}' as reference date: {now.isoformat()}",
                    extra={
                        "request_id": request_id,
                        "test_now": test_now,
                        "parsed_now": now.isoformat(),
                    },
                )
            except (ValueError, AttributeError) as e:
                # Fallback to current time if parsing fails
                logger.warning(
                    f"Failed to parse LUMA_TEST_NOW='{test_now}': {e}. Using current time.",
                    extra={"request_id": request_id},
                )
                now = datetime.now()
                now = _localize_datetime(now, timezone)
        else:
            now = datetime.now()
            now = _localize_datetime(now, timezone)
            logger.debug(
                f"LUMA_TEST_NOW not set, using current time: {now.isoformat()}",
                extra={"request_id": request_id, "current_now": now.isoformat()},
            )

        # Initialize slots early to avoid "referenced before assignment" errors
        # Slots will be populated later in the function, but must exist from the start
        slots: Dict[str, Any] = {}

        results = {
            "input": {
                "sentence": text,
                "domain": domain,
                "timezone": timezone,
                "now": now.isoformat(),
            },
            "stages": {},
        }

        # Execute pipeline to get execution_trace
        try:
            pipeline = LumaPipeline(
                domain=domain, entity_file=entity_file, intent_resolver=intent_resolver
            )
            booking_mode_for_pipeline = "service"
            if tenant_context and isinstance(tenant_context, dict):
                booking_mode_for_pipeline = (
                    tenant_context.get("booking_mode", "service") or "service"
                )

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
                debug_mode=pipeline_debug_mode,
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
                output_data=extraction_result,
            )
            execution_trace["stage_snapshots"].append(extraction_snapshot)

            # Capture grouping snapshot (input: extraction_result + structure, output: grouped_result)
            grouping_input = {
                "extraction_result": extraction_result,
                "structure": structure_dict,
            }
            grouping_snapshot = capture_stage_snapshot(
                stage_name="grouping",
                input_data=grouping_input,
                output_data=grouped_result,
            )
            execution_trace["stage_snapshots"].append(grouping_snapshot)

            # Capture semantic snapshot (input: grouped_result, output: semantic_result.resolved_booking)
            semantic_snapshot = capture_stage_snapshot(
                stage_name="semantic",
                input_data={
                    "grouped_result": grouped_result,
                    "extraction_result": extraction_result,
                },
                output_data=semantic_result.resolved_booking if semantic_result else {},
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
            results["stages"]["intent"]["external_intent"] = (
                intent if is_booking_intent(intent) else None
            )

            # Luma is stateless - all requests are independent
            results["stages"]["intent"]["stateless"] = {
                "classifier_intent": classifier_intent,
                "effective_intent": intent,
            }

        except Exception as e:
            # Fallback to individual stage execution on pipeline error
            logger.error(
                f"Pipeline execution failed: {e}",
                extra={"request_id": request_id},
                exc_info=True,
            )
            results["stages"]["extraction"] = {"error": str(e)}
            return jsonify({"success": False, "data": results}), 500

        # INVARIANT: Do not parse or reinterpret raw text
        # If services are missing, let the decision layer handle clarification
        # This prevents silent corruption of semantic results
        # Note: We no longer scan text for reservation nouns - that violates the invariant
        service_missing = False
        if semantic_result and (
            not semantic_result.resolved_booking.get("services")
            or len(semantic_result.resolved_booking.get("services", [])) == 0
        ):
            service_missing = True
            logger.debug(
                f"Service missing in semantic result for user {user_id}",
                extra={
                    "request_id": request_id,
                    "domain": domain,
                    "note": "Decision layer will handle clarification",
                },
            )

        # Store trace flag for diagnostic purposes
        if "semantic" not in execution_trace:
            execution_trace["semantic"] = {}
        execution_trace["semantic"]["service_missing"] = service_missing

        # Luma is stateless - use semantic result directly (no memory merging)
        merged_semantic_result = semantic_result

        # Extract intent_name early to check for UNKNOWN
        intent_name_early = (
            intent
            if isinstance(intent, str)
            else (intent.get("name") if isinstance(intent, dict) else None)
        )
        is_unknown_intent = intent_name_early == "UNKNOWN"

        # TEMPORAL RESOLUTION INVARIANT: Semantic temporal resolution runs regardless of intent
        # If temporal tokens (datetoken, timetoken, timewindowtoken) exist in the sentence,
        # semantic resolution must produce temporal slots even when intent is UNKNOWN.
        # This enables standalone temporal inputs (e.g., "friday", "next week", "tomorrow evening")
        # to flow through the same temporal resolver as booking sentences.
        # Semantic resolution already runs in the pipeline (before intent determination),
        # so temporal slots are available in semantic_result.resolved_booking for UNKNOWN intents.
        # The _has_temporal_tokens() helper is available for future use if needed to gate
        # temporal-only processing, but semantic resolution already runs unconditionally in the pipeline.

        # EXTRACTION-ONLY: No decision layer - all intents are extraction-only
        # Decision layer removed - Luma only extracts facts, no validation
        decision_result = None
        service_resolution_info = {}
        resolved_tenant_service_id = None
        service_resolution_reason = None
        service_resolution_metadata = {}

        # Luma is stateless - effective intent is just the intent we determined
        effective_intent = intent

        # Stage 6: Required slots validation (before calendar binding)
        # UNKNOWN intents skip required slots validation (pure extraction, no validation)
        intent_name_for_slots_raw = intent or effective_intent
        intent_name_for_slots = (
            intent_name_for_slots_raw.get("name")
            if isinstance(intent_name_for_slots_raw, dict)
            else intent_name_for_slots_raw
        )
        missing_required = []
        skip_prebind = False
        # Extract calendar_result from pipeline_results (pipeline already called bind_calendar)
        # This is the authoritative source - resolve_service should use it instead of calling bind_calendar again
        # Extract calendar_result from pipeline_results (pipeline already called bind_calendar)
        # This is the authoritative source - resolve_service should use it instead of calling bind_calendar again
        # UNKNOWN intents: Skip calendar binding (intentionally skipped per design)
        # EXCEPTION: Call binder for flexible date_mode with week/weekend phrases (e.g., "next week", "this weekend")
        # Slots will be built directly from semantic output using normalization functions
        if is_unknown_intent:
            logger.info(
                "[UNKNOWN_INTENT] Entering UNKNOWN intent calendar binding block",
                extra={"request_id": request_id, "intent": intent_name_early},
            )
            # Check if this is a week/weekend range expression that needs binder
            semantic_booking_for_check = (
                merged_semantic_result.resolved_booking
                if merged_semantic_result
                else {}
            )
            date_mode = semantic_booking_for_check.get("date_mode")
            date_refs = semantic_booking_for_check.get("date_refs", [])
            date_modifiers = semantic_booking_for_check.get("date_modifiers", [])
            logger.info(
                "[UNKNOWN_INTENT] Semantic booking check",
                extra={
                    "request_id": request_id,
                    "date_mode": date_mode,
                    "date_refs": date_refs,
                    "date_modifiers": date_modifiers,
                },
            )

            # Check if this is a week/weekend range expression OR absolute single-day date
            is_week_range = False
            is_absolute_single_day = False

            # Check for absolute single-day dates
            if (
                date_mode == "single_day"
                and isinstance(date_refs, list)
                and len(date_refs) == 1
            ):
                is_absolute_single_day = True
                logger.info(
                    "[UNKNOWN_INTENT] Absolute single-day date detected",
                    extra={
                        "request_id": request_id,
                        "date_mode": date_mode,
                        "date_ref": date_refs[0],
                        "date_refs_len": len(date_refs),
                    },
                )

            # Check for week/weekend range expressions
            if (
                date_mode == "flexible"
                and len(date_refs) == 0
                and len(date_modifiers) > 0
            ):
                # Check original sentence for "week" or "weekend"
                osentence = (
                    extraction_result.get("osentence", "") if extraction_result else ""
                )
                logger.info(
                    "[UNKNOWN_INTENT] Checking for week/weekend range",
                    extra={
                        "request_id": request_id,
                        "osentence": osentence,
                        "date_mode": date_mode,
                        "date_refs_len": len(date_refs),
                        "date_modifiers": date_modifiers,
                    },
                )
                if osentence:
                    osentence_lower = osentence.lower()
                    # Check for week/weekend patterns with modifiers
                    has_week = (
                        "week" in osentence_lower and "weekend" not in osentence_lower
                    )
                    has_weekend = "weekend" in osentence_lower
                    has_modifier = any(
                        mod in osentence_lower for mod in ["next", "this"]
                    )
                    logger.info(
                        "[UNKNOWN_INTENT] Week/weekend pattern check",
                        extra={
                            "request_id": request_id,
                            "has_week": has_week,
                            "has_weekend": has_weekend,
                            "has_modifier": has_modifier,
                        },
                    )
                    if (has_week or has_weekend) and has_modifier:
                        is_week_range = True
                        logger.info(
                            "[UNKNOWN_INTENT] Week/weekend range detected",
                            extra={"request_id": request_id},
                        )
            else:
                logger.info(
                    "[UNKNOWN_INTENT] Not a week/weekend range (conditions not met)",
                    extra={
                        "request_id": request_id,
                        "date_mode": date_mode,
                        "date_refs_len": len(date_refs) if date_refs else 0,
                        "date_modifiers_len": (
                            len(date_modifiers) if date_modifiers else 0
                        ),
                    },
                )

            if is_week_range or is_absolute_single_day:
                if is_week_range:
                    logger.info(
                        "[UNKNOWN_INTENT] Calling binder for week/weekend range",
                        extra={"request_id": request_id},
                    )
                elif is_absolute_single_day:
                    logger.info(
                        "[UNKNOWN_INTENT] Calling binder for absolute single-day date",
                        extra={
                            "request_id": request_id,
                            "date_ref": date_refs[0] if date_refs else None,
                        },
                    )

                # Call binder for week/weekend range expressions OR absolute single-day dates
                try:
                    modified_semantic_result = None
                    if is_week_range:
                        # For flexible mode with empty date_refs, we need to construct date_refs from entities
                        # Create a modified semantic result with date_refs populated from entities
                        # Extract week/weekend phrase from entities
                        dates_from_entities = (
                            extraction_result.get("dates", [])
                            if extraction_result
                            else []
                        )
                        logger.info(
                            "[UNKNOWN_INTENT] Extracting week phrase from entities",
                            extra={
                                "request_id": request_id,
                                "dates_from_entities": dates_from_entities,
                            },
                        )
                        week_phrase = None
                        for date_entity in dates_from_entities:
                            date_text = date_entity.get("text", "").lower()
                            if (
                                "week" in date_text and "weekend" not in date_text
                            ) or "weekend" in date_text:
                                week_phrase = date_entity.get("text", "")
                                break

                        logger.info(
                            "[UNKNOWN_INTENT] Week phrase extraction result",
                            extra={
                                "request_id": request_id,
                                "week_phrase": week_phrase,
                            },
                        )

                        # If we found a week phrase, create a modified semantic result with it in date_refs
                        if week_phrase:
                            # Create a copy of resolved_booking with date_refs populated
                            modified_booking = semantic_booking_for_check.copy()
                            modified_booking["date_refs"] = [week_phrase]
                            # Keep date_mode as flexible (binder will handle it)
                            modified_booking["date_mode"] = "flexible"

                            logger.info(
                                "[UNKNOWN_INTENT] Created modified semantic result",
                                extra={
                                    "request_id": request_id,
                                    "modified_date_refs": modified_booking.get(
                                        "date_refs"
                                    ),
                                    "modified_date_mode": modified_booking.get(
                                        "date_mode"
                                    ),
                                },
                            )

                            # Create a modified semantic result using the existing import
                            modified_semantic_result = SemanticResolutionResult(
                                resolved_booking=modified_booking,
                                needs_clarification=False,
                                clarification=None,
                            )
                        else:
                            logger.warning(
                                "[UNKNOWN_INTENT] No week phrase found, using original semantic result",
                                extra={"request_id": request_id},
                            )
                            modified_semantic_result = merged_semantic_result
                    elif is_absolute_single_day:
                        # For absolute single-day dates, use the semantic result as-is (date_refs already populated)
                        modified_semantic_result = merged_semantic_result
                        logger.info(
                            "[UNKNOWN_INTENT] Using semantic result for absolute single-day date",
                            extra={"request_id": request_id, "date_refs": date_refs},
                        )

                    logger.info(
                        "[UNKNOWN_INTENT] Calling bind_calendar",
                        extra={
                            "request_id": request_id,
                            "intent": intent_name_early,
                            "modified_date_refs": (
                                modified_semantic_result.resolved_booking.get(
                                    "date_refs"
                                )
                                if modified_semantic_result
                                else None
                            ),
                        },
                    )
                    with StageTimer(execution_trace, "binder", request_id=request_id):
                        calendar_result, binder_trace = bind_calendar(
                            modified_semantic_result,
                            now,
                            timezone,
                            intent=intent_name_early,
                            entities=extraction_result,
                            external_intent=None,
                        )
                    logger.info(
                        "[UNKNOWN_INTENT] Binder returned",
                        extra={
                            "request_id": request_id,
                            "binding_success": (
                                calendar_result._binding_success
                                if calendar_result
                                else None
                            ),
                            "binding_error": (
                                calendar_result._binding_error
                                if calendar_result
                                else None
                            ),
                            "calendar_booking": (
                                calendar_result.calendar_booking
                                if calendar_result
                                else None
                            ),
                        },
                    )
                    results["stages"]["calendar"] = calendar_result.to_dict()
                    execution_trace.update(binder_trace)
                except Exception as e:
                    logger.warning(
                        f"[UNKNOWN] Calendar binding failed for week/weekend range: {str(e)}",
                        extra={"request_id": request_id},
                        exc_info=True,
                    )
                    # Fallback to empty calendar result
                    calendar_result = CalendarBindingResult(
                        calendar_booking={},
                        needs_clarification=False,
                        clarification=None,
                        _binding_success=False,
                        _binding_error=f"binding_failed: {str(e)}",
                    )
                    results["stages"]["calendar"] = calendar_result.to_dict()
            else:
                logger.info(
                    "[UNKNOWN_INTENT] Not a week/weekend range, skipping binder",
                    extra={"request_id": request_id},
                )
                # Create empty calendar_result for UNKNOWN (calendar binding is intentionally skipped)
                calendar_result = CalendarBindingResult(
                    calendar_booking={},
                    needs_clarification=False,
                    clarification=None,
                    _binding_success=False,
                    _binding_error="skipped_for_unknown_intent",
                )
                results["stages"]["calendar"] = calendar_result.to_dict()
        else:
            pipeline_calendar_result = pipeline_results.get("stages", {}).get(
                "calendar"
            )
            # Use pipeline's calendar_result as the base (it already has the binder output)
            calendar_result = (
                pipeline_calendar_result if pipeline_calendar_result else None
            )
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
                    _binding_error="skipped_due_to_missing_required_slots",
                )
            results["stages"]["calendar"] = calendar_result.to_dict()
        else:
            # Stage 6: Calendar Binding
            # MANDATORY: Calendar binding only runs when decision_state == RESOLVED
            # EXCEPTION: Also allow binding when date is present but time is missing
            # (to provide bound date in clarification context)
            # EXTRACTION-ONLY: Calendar binding runs for all intents (no decision layer)
            # guarantees that temporal shape requirements are satisfied
            # UNKNOWN intents are already handled above - skip this section
            if is_unknown_intent:
                logger.info(
                    "[UNKNOWN_INTENT] Skipping calendar binding section (already handled above)",
                    extra={
                        "request_id": request_id,
                        "calendar_result_set": calendar_result is not None,
                        "calendar_booking": (
                            calendar_result.calendar_booking
                            if calendar_result
                            else None
                        ),
                    },
                )
                # UNKNOWN intents already handled in the block above (lines 714-805)
                # Do not overwrite calendar_result here
                pass
            # EXTRACTION-ONLY: Calendar binding runs for all non-UNKNOWN intents
            # (UNKNOWN intents handled above)
            elif not is_unknown_intent:
                # Proceed with calendar binding
                # Use effective_intent for calendar binding
                # Luma is stateless - use current semantic result directly
                binding_intent = effective_intent
                # Get external_intent for reservation handling (CREATE_RESERVATION vs CREATE_APPOINTMENT)
                external_intent = results["stages"]["intent"].get("external_intent")
                try:
                    # BINDER layer: Structured DEBUG log (before binding)
                    logger.debug(
                        "BINDER_GATE",
                        extra={
                            "request_id": request_id,
                            "run": True,
                            "reason": "extraction-only: calendar binding",
                        },
                    )
                    # Time calendar binding re-run (with merged semantic result)
                    with StageTimer(execution_trace, "binder", request_id=request_id):
                        calendar_result, binder_trace = bind_calendar(
                            merged_semantic_result,
                            now,
                            timezone,
                            intent=binding_intent,
                            entities=extraction_result,
                            external_intent=external_intent,
                        )
                        results["stages"]["calendar"] = calendar_result.to_dict()
                    # Update execution_trace with binder trace (overwrites pipeline's trace with merged semantic result)
                    execution_trace.update(binder_trace)

                    # EXTRACTION-ONLY: Calendar binding validation (no decision layer)
                    if True:  # Always validate (no decision_result check needed)
                        calendar_booking = (
                            calendar_result.calendar_booking if calendar_result else {}
                        )
                        required_bound_field_present = False

                        if external_intent == "CREATE_APPOINTMENT":
                            # Early escape: If semantic result has date + time, don't require calendar binding
                            # Handle date-time combination
                            semantic_booking = (
                                merged_semantic_result.resolved_booking
                                if merged_semantic_result
                                else {}
                            )
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
                                if tc_mode in {
                                    TimeMode.EXACT.value,
                                    TimeMode.WINDOW.value,
                                    TimeMode.FUZZY.value,
                                }:
                                    has_time = True
                            elif time_mode in {
                                TimeMode.EXACT.value,
                                TimeMode.RANGE.value,
                                TimeMode.WINDOW.value,
                            }:
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
                                    calendar_booking.get("datetime_range")
                                )
                        elif external_intent == "CREATE_RESERVATION":
                            # Reservations require date_range OR (start_date AND end_date)
                            required_bound_field_present = bool(
                                calendar_booking.get("date_range")
                                or (
                                    calendar_booking.get("start_date")
                                    and calendar_booking.get("end_date")
                                )
                            )
                        else:
                            # For other intents, assume binding is optional
                            required_bound_field_present = True

                        # EXTRACTION-ONLY: No validation - removed guardrail logic

                    # Capture binder snapshot
                    binder_input = {
                        "semantic_result": (
                            merged_semantic_result.resolved_booking
                            if merged_semantic_result
                            else {}
                        ),
                        "intent": binding_intent,
                        "external_intent": external_intent,
                        "timezone": timezone,
                    }
                    binder_output = calendar_result.to_dict() if calendar_result else {}
                    binder_snapshot = capture_stage_snapshot(
                        stage_name="binder",
                        input_data=binder_input,
                        output_data=binder_output,
                        decision_flags={
                            "called": True,
                            "needs_clarification": (
                                calendar_result.needs_clarification
                                if calendar_result
                                else False
                            ),
                        },
                    )
                    if "stage_snapshots" not in execution_trace:
                        execution_trace["stage_snapshots"] = []
                    execution_trace["stage_snapshots"].append(binder_snapshot)
                except Exception as e:
                    results["stages"]["calendar"] = {"error": str(e)}
                    # Build binder input for error trace
                    semantic_for_binder = (
                        merged_semantic_result.resolved_booking
                        if merged_semantic_result
                        else semantic_result.resolved_booking
                    )
                    # Get temporal shape from IntentRegistry (sole policy source)
                    registry = get_intent_registry()
                    intent_meta = (
                        registry.get(external_intent) if external_intent else None
                    )
                    temporal_shape_for_trace = (
                        intent_meta.temporal_shape if intent_meta else None
                    )
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
                            "time_constraint": semantic_for_binder.get(
                                "time_constraint"
                            ),
                            "timezone": timezone,
                        },
                        "output": {},
                        "decision_reason": f"exception: {str(e)}",
                    }
                    # Create empty calendar_result for consistency (even though we return early)
                    calendar_result = CalendarBindingResult(
                        calendar_booking={},
                        needs_clarification=False,
                        clarification=None,
                        _binding_success=False,
                        _binding_error=f"exception: {str(e)}",
                    )
                    return jsonify({"success": False, "data": results}), 500
            elif not is_unknown_intent:
                # EXTRACTION-ONLY: This branch removed (calendar binding always runs above)
                results["stages"]["calendar"] = calendar_result.to_dict()
                # Binder was skipped - add trace with input even though not called
                semantic_for_binder = (
                    merged_semantic_result.resolved_booking
                    if merged_semantic_result
                    else semantic_result.resolved_booking
                )
                external_intent_for_trace = (
                    results["stages"]["intent"].get("external_intent") or intent
                )
                # Get temporal shape from IntentRegistry (sole policy source)
                registry = get_intent_registry()
                intent_meta = (
                    registry.get(external_intent_for_trace)
                    if external_intent_for_trace
                    else None
                )
                temporal_shape_for_trace = (
                    intent_meta.temporal_shape if intent_meta else None
                )
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
                        "timezone": timezone,
                    },
                    "output": {},
                    "decision_reason": reason,
                }

        # Determine debug mode (query param debug=1|true|yes)
        debug_flag = str(request.args.get("debug", "0")).lower()
        debug_mode = debug_flag in {"1", "true", "yes"}

        # Extract current booking state from calendar result
        calendar_dict = calendar_result.to_dict()
        calendar_booking = (
            calendar_dict.get("calendar_booking", {}) if calendar_dict else {}
        )
        cal_clar_dict = calendar_dict.get("clarification") if calendar_dict else None

        # EXTRACTION-ONLY: No clarification planning - all intents are extraction-only
        # Clarification planning removed - Luma only extracts facts, no validation
        intent_resp = results["stages"]["intent"]
        intent_name = (
            intent_resp.get("name")
            if isinstance(intent_resp, dict)
            else intent_resp or intent
        )

        # Determine if this is MODIFY_BOOKING (for special handling, but no validation)
        is_modify_booking = intent_name == "MODIFY_BOOKING"

        # EXTRACTION-ONLY: Always set needs_clarification=False and missing_slots=[]
        needs_clarification = False
        missing_slots = []
        clarification_reason = None

        # EXTRACTION-ONLY: All validation logic removed - no missing slot computation, no temporal enforcement
        # EXTRACTION-ONLY: No clarification handling - removed all semantic validation
        current_clarification = None

        # Prepare current booking state (only canonical fields)
        # Include date_range and time_range for merge logic to handle time-only updates
        # Format services to preserve resolved_alias if present
        calendar_services = calendar_booking.get("services", [])
        formatted_services = (
            [
                format_service_for_response(service)
                for service in calendar_services
                if isinstance(service, dict)
            ]
            if calendar_services
            else []
        )

        current_booking = {
            "services": formatted_services,
            "datetime_range": calendar_booking.get("datetime_range"),
            "date_range": calendar_booking.get("date_range"),
            "time_range": calendar_booking.get("time_range"),
            "duration": calendar_booking.get("duration"),
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
            "external_intent"
        )

        # Use real intent directly (no normalization needed)
        intent_payload_name = (
            external_intent_for_response
            if external_intent_for_response
            in {"CREATE_APPOINTMENT", "CREATE_RESERVATION"}
            else api_intent
        )
        intent_payload = {"name": intent_payload_name, "confidence": confidence}

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
        is_creates_booking = is_booking_intent_flag and effective_intent not in {
            "MODIFY_BOOKING",
            "CANCEL_BOOKING",
            "UNKNOWN",
        }

        if not is_unknown_intent and is_creates_booking:
            # Use current_booking directly (stateless - no memory merging)
            booking_payload = current_booking.copy() if current_booking else {}
            # Add booking_state = "RESOLVED" for resolved bookings
            if booking_payload:
                booking_payload["booking_state"] = "RESOLVED"
                # Format services to preserve resolved_alias if present in current semantic result
                if merged_semantic_result:
                    current_services = merged_semantic_result.resolved_booking.get(
                        "services", []
                    )
                    if current_services:
                        # Use services from current semantic result (which may have resolved_alias)
                        booking_payload["services"] = [
                            format_service_for_response(service)
                            for service in current_services
                            if isinstance(service, dict)
                        ]
            # EXTRACTION-ONLY: If booking_payload is missing or incomplete, log warning
            if not booking_payload or (
                not booking_payload.get("services")
                and not booking_payload.get("datetime_range")
                and not booking_payload.get("start_date")
                and not booking_payload.get("end_date")
            ):
                logger.warning(
                    f"Booking payload missing or incomplete for user {user_id} (invariant violation - not rebuilding)",
                    extra={
                        "request_id": request_id,
                        "intent": api_intent,
                        "has_calendar_booking": bool(calendar_booking),
                        "note": "EXTRACTION-ONLY: booking_payload may be incomplete",
                    },
                )
                # EXTRACTION-ONLY: Log warning but don't force clarification
                booking_payload = None
        # EXTRACTION-ONLY: Removed needs_clarification branch (always False)

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
            "service_ids": [
                s.get("text", "") if isinstance(s, dict) else str(s)
                for s in _get_business_categories(extraction_result)
            ],
            "dates": [
                d.get("text", "") if isinstance(d, dict) else str(d)
                for d in (
                    extraction_result.get("dates", [])
                    + extraction_result.get("dates_absolute", [])
                )
            ],
            "times": [
                t.get("text", "") if isinstance(t, dict) else str(t)
                for t in extraction_result.get("times", [])
            ],
        }

        # Add response trace (EXTRACTION-ONLY: no issues, no clarification)
        issues_for_trace: Dict[str, Any] = {}

        execution_trace["response"] = {
            "status": STATUS_READY,  # EXTRACTION-ONLY: always READY
            "intent": api_intent,
            "issues": {},
            "has_booking": booking_payload is not None,
            "has_clarification": False,  # EXTRACTION-ONLY: always False
        }

        # EXTRACTION-ONLY: Removed final_response_issues (always empty)

        # Build facts directly from semantic output (single source of truth)
        facts_from_semantic: Dict[str, Any] = {}
        semantic_booking = None
        if merged_semantic_result:
            semantic_booking = merged_semantic_result.resolved_booking
        elif semantic_result:
            semantic_booking = semantic_result.resolved_booking

        if semantic_booking:
            # Extract service_id from semantic.service_ids or semantic.services
            service_ids = semantic_booking.get("service_ids", [])
            if service_ids:
                facts_from_semantic["service_id"] = service_ids[0]
            else:
                # Fallback to services array
                services = semantic_booking.get("services", [])
                if services and isinstance(services[0], dict):
                    primary = services[0]
                    resolved_alias = primary.get("resolved_alias")
                    if resolved_alias:
                        facts_from_semantic["service_id"] = resolved_alias
                    else:
                        tenant_service_id = primary.get("tenant_service_id")
                        if tenant_service_id:
                            facts_from_semantic["service_id"] = tenant_service_id

            # Extract dates from semantic.date_refs (normalize ALL dates to ISO, including relative weekdays)
            # ALWAYS emit facts.dates when date_refs exists - never drop dates silently
            # Do NOT gate on date_mode, binder output, or decision.state
            # IMPORTANT: Combine date_modifiers with date_refs when modifiers exist
            date_refs = semantic_booking.get("date_refs", [])
            date_modifiers = semantic_booking.get("date_modifiers", [])
            if date_refs:
                tz = get_timezone(timezone)
                if now.tzinfo is None:
                    now_tz_aware = _localize_datetime(now, tz)
                else:
                    now_tz_aware = now

                # Normalize EACH date_ref to ISO using _bind_single_date
                # If date_modifiers exist, combine modifier + date_ref before normalization
                # ("this", "friday") → "this friday" → ISO date
                # ("next", "monday") → "next monday" → ISO date
                # For date ranges, normalize together to prevent year drift
                date_mode = semantic_booking.get("date_mode", "none")
                normalized_dates = []

                if date_mode == "range" and len(date_refs) >= 2:
                    # For ranges, normalize start and end together to prevent year drift
                    start_date_str = date_refs[0]
                    end_date_str = date_refs[1]

                    # Combine modifiers if present
                    if (
                        date_modifiers
                        and isinstance(date_modifiers, list)
                        and len(date_modifiers) > 0
                    ):
                        modifier = date_modifiers[0]
                        if modifier and isinstance(modifier, str):
                            start_date_str = f"{modifier} {start_date_str}".strip()
                    if (
                        date_modifiers
                        and isinstance(date_modifiers, list)
                        and len(date_modifiers) > 1
                    ):
                        modifier = date_modifiers[1]
                        if modifier and isinstance(modifier, str):
                            end_date_str = f"{modifier} {end_date_str}".strip()

                    try:
                        start_date = _bind_single_date(start_date_str, now_tz_aware, tz)
                        end_date = _bind_single_date(end_date_str, now_tz_aware, tz)
                        if start_date and end_date:
                            # Fix year drift: if start_date > end_date, re-normalize start_date using end_date.year
                            if start_date > end_date:
                                start_date = _localize_datetime(
                                    datetime(
                                        end_date.year, start_date.month, start_date.day
                                    ),
                                    tz,
                                )
                            normalized_dates.append(start_date.strftime("%Y-%m-%d"))
                            normalized_dates.append(end_date.strftime("%Y-%m-%d"))
                        else:
                            # Fallback to individual normalization
                            if start_date:
                                normalized_dates.append(start_date.strftime("%Y-%m-%d"))
                            # If normalization failed, try harder - never append raw date_ref
                            elif len(date_refs) > 0:
                                # Try normalizing the raw date_ref as a weekday
                                normalized_start = _try_normalize_weekday(
                                    date_refs[0], now_tz_aware, tz
                                )
                                if normalized_start:
                                    normalized_dates.append(normalized_start)
                            if end_date:
                                normalized_dates.append(end_date.strftime("%Y-%m-%d"))
                            # If normalization failed, try harder - never append raw date_ref
                            elif len(date_refs) > 1:
                                normalized_end = _try_normalize_weekday(
                                    date_refs[1], now_tz_aware, tz
                                )
                                if normalized_end:
                                    normalized_dates.append(normalized_end)
                    except Exception as e:
                        logger.debug(
                            f"[final_response] Range date normalization failed: {str(e)}",
                            extra={"request_id": request_id},
                        )
                        # Try harder to normalize - never append raw date_refs
                        if len(date_refs) > 0:
                            normalized_start = _try_normalize_weekday(
                                date_refs[0], now_tz_aware, tz
                            )
                            if normalized_start:
                                normalized_dates.append(normalized_start)
                        if len(date_refs) > 1:
                            normalized_end = _try_normalize_weekday(
                                date_refs[1], now_tz_aware, tz
                            )
                            if normalized_end:
                                normalized_dates.append(normalized_end)
                else:
                    # For single dates or flexible mode, normalize individually
                    for idx, date_ref in enumerate(date_refs):
                        if isinstance(date_ref, str):
                            # Combine modifier with date_ref if modifier exists for this index
                            date_str_to_normalize = date_ref
                            modifier = None
                            if (
                                date_modifiers
                                and isinstance(date_modifiers, list)
                                and len(date_modifiers) > idx
                            ):
                                modifier = date_modifiers[idx]
                                if modifier and isinstance(modifier, str):
                                    # Combine modifier and date_ref: "this friday", "next monday"
                                    date_str_to_normalize = (
                                        f"{modifier} {date_ref}".strip()
                                    )

                            try:
                                bound_date = _bind_single_date(
                                    date_str_to_normalize, now_tz_aware, tz
                                )
                                if bound_date:
                                    # Normalization succeeded → use ISO string
                                    normalized_dates.append(
                                        bound_date.strftime("%Y-%m-%d")
                                    )
                                else:
                                    # Normalization failed - try harder to normalize, never append raw date_ref
                                    # Always try weekday normalization for any intent
                                    normalized_date = _try_normalize_weekday(
                                        date_ref, now_tz_aware, tz
                                    )
                                    if normalized_date:
                                        normalized_dates.append(normalized_date)
                                    # If still can't normalize, skip it (don't append raw date_ref)
                                    # This ensures facts.dates only contains normalized ISO dates
                            except Exception as e:
                                # Normalization exception → try harder, never use raw date_ref
                                logger.debug(
                                    f"[final_response] Date normalization failed for '{date_str_to_normalize}': {str(e)}",
                                    extra={
                                        "request_id": request_id,
                                        "date_ref": date_ref,
                                        "date_str_to_normalize": date_str_to_normalize,
                                    },
                                )
                                # Try weekday normalization as fallback
                                normalized_date = _try_normalize_weekday(
                                    date_ref, now_tz_aware, tz
                                )
                                if normalized_date:
                                    normalized_dates.append(normalized_date)
                                # If still can't normalize, skip it (don't append raw date_ref)
                        else:
                            # Non-string date_ref → try to normalize, never append raw
                            date_str = str(date_ref)
                            normalized_date = _try_normalize_weekday(
                                date_str, now_tz_aware, tz
                            )
                            if normalized_date:
                                normalized_dates.append(normalized_date)

                # Remove duplicates while preserving order
                if normalized_dates:
                    seen = set()
                    unique_dates = [
                        d for d in normalized_dates if d not in seen and not seen.add(d)
                    ]
                    if unique_dates:
                        facts_from_semantic["dates"] = unique_dates

                # Post-process: Normalize any remaining raw weekday strings (safety net)
                # This handles edge cases where normalization might have failed earlier
                # DATE NORMALIZATION RULE: Always normalize dates to ISO, never store raw tokens
                if facts_from_semantic.get("dates"):
                    # Ensure now_tz_aware is available (reuse from date normalization above)
                    tz = get_timezone(timezone)
                    if now.tzinfo is None:
                        now_tz_aware_post = _localize_datetime(now, tz)
                    else:
                        now_tz_aware_post = now

                    # Check if any dates are raw weekday strings or other non-ISO formats
                    processed_dates = []
                    for date_str in facts_from_semantic["dates"]:
                        if isinstance(date_str, str):
                            # Check if it's already an ISO date (YYYY-MM-DD format)
                            if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
                                # Already normalized ISO date - keep it
                                processed_dates.append(date_str)
                            else:
                                # Not an ISO date - try to normalize it
                                # First try weekday normalization
                                normalized_date = _try_normalize_weekday(
                                    date_str, now_tz_aware_post, tz
                                )
                                if normalized_date:
                                    processed_dates.append(normalized_date)
                                else:
                                    # Try binding as a date string
                                    try:
                                        bound_date = _bind_single_date(
                                            date_str, now_tz_aware_post, tz
                                        )
                                        if bound_date:
                                            processed_dates.append(
                                                bound_date.strftime("%Y-%m-%d")
                                            )
                                        # If normalization fails, skip it (don't append raw string)
                                    except Exception:
                                        # Normalization failed - skip it (don't append raw string)
                                        logger.debug(
                                            f"[final_response] Post-process date normalization failed for '{date_str}', skipping",
                                            extra={
                                                "request_id": request_id,
                                                "date_str": date_str,
                                            },
                                        )
                        else:
                            # Non-string date - try to convert and normalize
                            date_str = str(date_str)
                            normalized_date = _try_normalize_weekday(
                                date_str, now_tz_aware_post, tz
                            )
                            if normalized_date:
                                processed_dates.append(normalized_date)
                            else:
                                # Try binding as a date string
                                try:
                                    bound_date = _bind_single_date(
                                        date_str, now_tz_aware_post, tz
                                    )
                                    if bound_date:
                                        processed_dates.append(
                                            bound_date.strftime("%Y-%m-%d")
                                        )
                                    # If normalization fails, skip it
                                except Exception:
                                    # Normalization failed - skip it
                                    logger.debug(
                                        f"[final_response] Post-process date normalization failed for '{date_str}', skipping",
                                        extra={
                                            "request_id": request_id,
                                            "date_str": date_str,
                                        },
                                    )
                    if processed_dates:
                        facts_from_semantic["dates"] = processed_dates
                    elif facts_from_semantic.get("dates"):
                        # All dates failed normalization - remove the key to avoid leaking raw tokens
                        facts_from_semantic.pop("dates", None)

            # APPOINTMENT INTENT RULE: Do NOT emit facts.times or facts.time for appointment intents
            # All temporal information for appointments should ONLY appear in time_constraint
            # Skip facts.times emission entirely for CREATE_APPOINTMENT intents
            if api_intent != "CREATE_APPOINTMENT":
                # Extract times from semantic.time_refs (normalize to HH:MM)
                # FUZZY TIME RULE: Do NOT emit facts.times for fuzzy times (morning, evening, etc.)
                # Fuzzy times should ONLY appear in time_constraint, not in facts.times
                time_refs = semantic_booking.get("time_refs", [])
                time_mode = semantic_booking.get("time_mode", "none")
                time_constraint = semantic_booking.get("time_constraint")
                normalized_times = []

                # Check if this is a fuzzy time - if so, skip facts.times emission
                # FUZZY TIME RULE: Do NOT emit facts.times for fuzzy times (morning, evening, etc.)
                is_fuzzy_time = False
                if time_constraint and isinstance(time_constraint, dict):
                    constraint_mode = time_constraint.get("mode")
                    if constraint_mode == "fuzzy":
                        # Fuzzy time (morning, evening, etc.) - do NOT emit facts.times
                        is_fuzzy_time = True
                    elif constraint_mode == "exact":
                        # Exact time constraint - include in facts.times
                        constraint_start = time_constraint.get("start")
                        if (
                            constraint_start
                            and isinstance(constraint_start, str)
                            and ":" in constraint_start
                        ):
                            # Already in HH:MM format (e.g., "12:00" from "noon")
                            normalized_times.append(constraint_start)

                # Also check if time_mode == "window" with fuzzy window names (morning, evening, etc.)
                # These should NOT be converted to discrete times in facts.times
                if not is_fuzzy_time and time_mode == "window" and time_refs:
                    # Check if time_refs contain fuzzy window names (not explicit time ranges)
                    from luma.calendar.calendar_binder import _get_time_window_bounds

                    time_window_bounds = _get_time_window_bounds()
                    fuzzy_window_names = set(time_window_bounds.keys())
                    # If first time_ref is a fuzzy window name, treat as fuzzy
                    if time_refs and isinstance(time_refs[0], str):
                        first_ref_lower = time_refs[0].lower().strip()
                        if first_ref_lower in fuzzy_window_names:
                            is_fuzzy_time = True

                # Only process time_refs if NOT a fuzzy time
                # Fuzzy times should only appear in time_constraint, not facts.times
                if time_refs and not is_fuzzy_time:
                    try:
                        tz = get_timezone(timezone)
                        if now.tzinfo is None:
                            now_tz_aware = _localize_datetime(now, tz)
                        else:
                            now_tz_aware = now

                        time_windows = (
                            extraction_result.get("time_windows", [])
                            if extraction_result
                            else []
                        )
                        # Normalize ALL time_refs - collect all times, not just the first one
                        # For exact mode, bind_times only processes first time, so we need to parse each time_ref
                        if time_mode == "exact":
                            # For exact times, parse each time_ref individually
                            from luma.calendar.calendar_binder import _parse_time

                            for time_ref in time_refs:
                                if isinstance(time_ref, str):
                                    bound_time, _ = _parse_time(time_ref)
                                    if bound_time:
                                        time_hhmm = bound_time.strftime("%H:%M")
                                        normalized_times.append(time_hhmm)
                        else:
                            # For window/range mode, use bind_times (handles multiple times)
                            time_result = bind_times(
                                time_refs,
                                time_mode,
                                now_tz_aware,
                                tz,
                                time_windows=time_windows,
                            )
                            if time_result:
                                start_time = time_result.get("start_time")
                                end_time = time_result.get("end_time")
                                if start_time:
                                    # Extract HH:MM from normalized time
                                    if isinstance(start_time, str):
                                        if ":" in start_time:
                                            hour_min = ":".join(
                                                start_time.split(":")[:2]
                                            )
                                            normalized_times.append(hour_min)
                                        else:
                                            normalized_times.append(start_time)
                                # For range mode, also include end_time if different
                                if end_time and end_time != start_time:
                                    if isinstance(end_time, str):
                                        if ":" in end_time:
                                            hour_min = ":".join(end_time.split(":")[:2])
                                            if hour_min not in normalized_times:
                                                normalized_times.append(hour_min)
                                        else:
                                            if end_time not in normalized_times:
                                                normalized_times.append(end_time)
                        # Remove duplicates while preserving order
                        if normalized_times:
                            seen = set()
                            unique_times = [
                                t
                                for t in normalized_times
                                if t not in seen and not seen.add(t)
                            ]
                            if unique_times:
                                facts_from_semantic["times"] = unique_times
                    except Exception as e:
                        logger.debug(
                            f"[final_response] Time normalization failed: {str(e)}",
                            extra={"request_id": request_id},
                        )
                        pass

            # Extract booking_id from extraction_result (for MODIFY_BOOKING, CANCEL_BOOKING, etc.)
            if extraction_result and extraction_result.get("booking_id"):
                facts_from_semantic["booking_id"] = extraction_result["booking_id"]

        # Build final_response with intent and facts (no status, no issues)
        final_response = {"intent": api_intent}

        # Include facts if at least one fact exists
        if facts_from_semantic:
            final_response["facts"] = facts_from_semantic

        # Store facts_from_semantic for use in actual API response
        # This will override the facts from aggregate_extraction_facts
        semantic_facts_for_response = (
            facts_from_semantic if facts_from_semantic else None
        )

        # Validate trace completeness (fail fast in debug mode)
        debug_flag = str(request.args.get("debug", "0")).lower()
        debug_mode = debug_flag in {"1", "true", "yes"}
        if debug_mode:
            required_trace_keys = [
                "entity",
                "semantic",
                "decision",
                "binder",
                "response",
            ]
            missing_keys = [
                key for key in required_trace_keys if key not in execution_trace
            ]
            if missing_keys:
                raise ValueError(
                    f"EXECUTION_TRACE incomplete: missing keys {missing_keys}"
                )

        # Ensure trace and final_response are always included (even if empty)
        # This ensures the log structure is consistent
        if not execution_trace:
            execution_trace = {}
        if not final_response:
            final_response = {}

        # Build sentence trace - capture sentence evolution through pipeline
        # Capture the actual values flowing through the pipeline (do not recompute)
        normalized_text = (
            extraction_result.get("osentence", text) if extraction_result else text
        )
        parameterized_text = (
            extraction_result.get("psentence", "") if extraction_result else ""
        )

        # Intent resolver is called with raw text, but should use osentence (normalized)
        # Capture what is actually passed to resolve_intent (currently raw text)
        # Note: The intent resolver normalizes internally, but we capture what was passed
        intent_input_text = text  # Currently passed as raw text to resolve_intent

        sentence_trace = {
            "raw_text": text,
            "normalized_text": normalized_text,
            "parameterized_text": parameterized_text,
            "intent_input_text": intent_input_text,
        }

        # Build complete input payload - capture all initial request data
        input_payload = {
            "user_id": user_id,
            "raw_text": text,
            "domain": domain,
            "timezone": timezone,
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
                    tenant_context_for_trace["booking_mode"] = tenant_context[
                        "booking_mode"
                    ]
            # Only add tenant_context to input if it has content
            if tenant_context_for_trace:
                input_payload["tenant_context"] = tenant_context_for_trace

        # Validate stable fields in debug mode only (non-breaking enforcement)
        # This ensures stable fields are present and have expected types
        # Debug fields are not validated and may change freely
        if debug_mode:
            trace_data = {
                "request_id": request_id,
                "input": input_payload,
                "trace": execution_trace,
                "final_response": final_response,
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
                "request_id": request_id,
                "input": input_payload,
                "sentence_trace": sentence_trace,
                "trace": execution_trace,
                "final_response": final_response,
                "processing_time_ms": processing_time,
            },
        )

        # INVARIANT: Only require booking_payload for intents that produce it
        # MODIFY_BOOKING and CANCEL_BOOKING do NOT produce booking_payload (intent-specific semantics)
        registry = get_intent_registry()
        intent_meta = registry.get(api_intent) if api_intent else None
        produces_booking = (
            intent_meta.produces_booking_payload if intent_meta else False
        )

        # Only enforce booking_payload requirement for intents that produce it
        if produces_booking and booking_payload is None and not needs_clarification:
            logger.error(
                f"INVARIANT VIOLATION: Intent {api_intent} requires booking_payload but it is None for user {user_id}",
                extra={
                    "request_id": request_id,
                    "intent": api_intent,
                    "decision_status": None,  # EXTRACTION-ONLY: no decision layer
                    "has_calendar_booking": bool(calendar_booking),
                    "produces_booking_payload": produces_booking,
                    "note": "EXTRACTION-ONLY: booking_payload may be None",
                },
            )
            # EXTRACTION-ONLY: Log error but don't force clarification

        # booking_payload is already set above for both RESOLVED and PARTIAL cases
        # For non-booking intents, booking_payload may be None (which is fine)

        # Project minimal booking output shape using ResponseBuilder
        response_builder = ResponseBuilder()
        if booking_payload is not None:
            booking_payload = response_builder.format_booking_payload(
                booking_payload,
                intent_payload_name,
                calendar_booking,
                request_id=request_id,
            )

        # EXTRACTION-ONLY: No issues building (always empty)
        issues: Dict[str, Any] = {}
        # Dead code - kept for structure reference
        if False:
            # Get time_issues from resolved_booking if available
            time_issues_for_issues = None
            if merged_semantic_result:
                time_issues_for_issues = merged_semantic_result.resolved_booking.get(
                    "time_issues", []
                )
            elif semantic_result:
                time_issues_for_issues = semantic_result.resolved_booking.get(
                    "time_issues", []
                )

            # Fix 4: Filter service_id from missing_slots for reservations when services exist
            # For CREATE_RESERVATION, remove service_id from issues if services exist
            filtered_missing_slots = missing_slots
            # Check for services in semantic result (available even when booking_payload is None)
            semantic_services = None
            if merged_semantic_result and merged_semantic_result.resolved_booking:
                semantic_services = merged_semantic_result.resolved_booking.get(
                    "services"
                )
            elif semantic_result and semantic_result.resolved_booking:
                semantic_services = semantic_result.resolved_booking.get("services")

            # EXTRACTION-ONLY: No validation - removed all missing slot filtering and issue building
            filtered_missing_slots = []
            issues = {}

        # Build slots first (single source of truth for temporal data)
        # Slots MUST be present whenever any resolved data exists (service, date, datetime)
        # Slots are built for both ready and clarification cases if data is resolved
        # Note: slots is already initialized at the start of the function, but ensure it exists here
        slots = slots or {}

        # UNKNOWN intent: Build slots from semantic output and binder output (if available)
        # Calendar binder is called for week/weekend range expressions (see line 735-762)
        # For other temporal inputs, we normalize date/time refs directly using existing functions
        # Reuse existing normalization functions (_bind_single_date, bind_times) - do NOT reparse text
        #
        # TEMPORAL RESOLUTION FOR UNKNOWN INTENTS:
        # Semantic temporal resolution already runs in the pipeline (before intent determination),
        # so temporal slots are available in semantic_result.resolved_booking for UNKNOWN intents.
        # This enables standalone temporal inputs (e.g., "friday", "next week", "tomorrow evening")
        # to flow through the same temporal resolver as booking sentences, respecting policies like
        # ALLOW_BARE_WEEKDAY_BINDING and ALLOW_BARE_WEEKDAY_RANGE_BINDING.
        # UNKNOWN intents should always have needs_clarification=False (forced at line 938)
        if is_unknown_intent:
            # First, check if binder produced output (for week/weekend ranges)
            if calendar_result and calendar_result.calendar_booking:
                calendar_booking = calendar_result.calendar_booking

                # Extract date or date_range from binder output
                # For week/weekend ranges: date_range with different start/end
                # For absolute single-day dates: date_range with same start/end (single date)
                if RESERVATION_TEMPORAL_TYPE in calendar_booking:
                    date_range = calendar_booking[RESERVATION_TEMPORAL_TYPE]
                    if date_range:
                        # Binder returns start_date/end_date, convert to start/end for response
                        start_date = date_range.get("start_date") or date_range.get(
                            "start"
                        )
                        end_date = date_range.get("end_date") or date_range.get("end")
                        if start_date and end_date:
                            # EXTRACTION-ONLY: Emit raw dates as facts, not semantic date_range
                            # "next week" → dates[ start_of_week, end_of_week ]
                            dates_list = []
                            dates_list.append(start_date)
                            if end_date != start_date:
                                dates_list.append(end_date)
                            slots["date"] = (
                                dates_list
                                if len(dates_list) > 1
                                else dates_list[0] if dates_list else None
                            )
                            logger.info(
                                "[UNKNOWN_INTENT] Extracted dates from binder as raw facts",
                                extra={"request_id": request_id, "date": slots["date"]},
                            )

                # Also check APPOINTMENT_TEMPORAL_TYPE for datetime_range (for appointments with time)
                if APPOINTMENT_TEMPORAL_TYPE in calendar_booking:
                    datetime_range = calendar_booking[APPOINTMENT_TEMPORAL_TYPE]
                    if datetime_range and datetime_range.get("start"):
                        start_str = datetime_range["start"]
                        # Extract date from ISO datetime string
                        if "T" in start_str:
                            slots["date"] = start_str.split("T")[0]
                        else:
                            slots["date"] = start_str

                # Extract time from binder output
                if calendar_booking.get("time_range"):
                    time_range = calendar_booking["time_range"]
                    if time_range.get("start_time"):
                        slots["time"] = time_range["start_time"]
                        logger.info(
                            "[UNKNOWN_INTENT] Extracted time from binder time_range",
                            extra={"request_id": request_id, "time": slots["time"]},
                        )

                # Fallback: Extract time from time_constraint if binder didn't produce time_range
                if not slots.get("time") and calendar_booking.get("time_constraint"):
                    time_constraint = calendar_booking["time_constraint"]
                    # For "by 3pm" type constraints, extract the time
                    if time_constraint.get("start"):
                        slots["time"] = time_constraint["start"]
                        logger.info(
                            "[UNKNOWN_INTENT] Extracted time from binder time_constraint",
                            extra={"request_id": request_id, "time": slots["time"]},
                        )

            # If binder didn't produce output, fall back to direct normalization
            if not slots.get("date"):
                # Get semantic_booking from merged_semantic_result or semantic_result (fallback)
                # merged_semantic_result = semantic_result for stateless Luma (line 510)
                semantic_booking = {}
                if merged_semantic_result and merged_semantic_result.resolved_booking:
                    semantic_booking = merged_semantic_result.resolved_booking
                elif semantic_result and semantic_result.resolved_booking:
                    # Fallback to semantic_result if merged_semantic_result is not available
                    semantic_booking = semantic_result.resolved_booking

                # SYNTACTIC DATE-TIME PAIRING: Detect explicit grammatical binding
                # Only emit date_time_pairs[] when grammar is explicit (e.g., "on March 3rd at 3pm")
                # Otherwise, emit dates[] and times[] independently
                try:
                    pairs = detect_date_time_pairs(
                        text, extraction_result if extraction_result else {}
                    )
                    if pairs:
                        # Normalize pairs using existing date/time normalizers
                        tz = get_timezone(timezone)
                        if now.tzinfo is None:
                            now_tz_aware = _localize_datetime(now, tz)
                        else:
                            now_tz_aware = now

                        # Create time normalizer wrapper that matches expected signature
                        time_windows = (
                            extraction_result.get("time_windows", [])
                            if extraction_result
                            else []
                        )

                        def time_normalizer_wrapper(time_refs, time_mode, now, tz):
                            return (
                                bind_times(
                                    time_refs,
                                    time_mode,
                                    now,
                                    tz,
                                    time_windows=time_windows,
                                )
                                if time_mode == "exact"
                                else None
                            )

                        normalized_pairs = normalize_date_time_pairs(
                            pairs,
                            date_normalizer=_bind_single_date,
                            time_normalizer=time_normalizer_wrapper,
                            now=now_tz_aware,
                            tz=tz,
                        )

                        if normalized_pairs:
                            # Store normalized pairs in slots
                            slots["date_time_pairs"] = normalized_pairs
                            logger.info(
                                "[UNKNOWN_INTENT] Detected explicit date-time pairs",
                                extra={
                                    "request_id": request_id,
                                    "pairs": normalized_pairs,
                                },
                            )
                except Exception as e:
                    # If pairing detection fails, fall back to independent date/time extraction
                    logger.debug(
                        f"[UNKNOWN] Date-time pairing detection failed: {str(e)}",
                        extra={"request_id": request_id, "error": str(e)},
                    )

                # Date handling: Normalize date_refs using existing date normalizer
                date_mode = semantic_booking.get("date_mode")
                date_refs = semantic_booking.get("date_refs", [])
                date_modifiers = semantic_booking.get("date_modifiers", [])

                if date_mode == "single_day" and len(date_refs) >= 1:
                    # Single date: normalize using _bind_single_date (reuse existing normalizer)
                    # For UNKNOWN intents with temporal tokens, allow bare weekday binding
                    # (this enables standalone temporal inputs like "friday" to be resolved)
                    #
                    # CRITICAL: If date_modifiers exist (e.g., "next", "this"), skip shortcut binding
                    # and use modifier-aware logic instead
                    has_modifiers = len(date_modifiers) > 0

                    try:
                        tz = get_timezone(timezone)
                        # Ensure now is timezone-aware for _bind_single_date
                        # now should already be timezone-aware from line 371, but ensure it is
                        if now.tzinfo is None:
                            now_tz_aware = _localize_datetime(now, tz)
                        else:
                            now_tz_aware = now

                        bound_date = None
                        if has_modifiers:
                            # Modifiers present: combine modifier with date_ref and use modifier-aware binding
                            # This ensures "next friday" is correctly bound to next week's Friday, not this week's
                            modifier = date_modifiers[0] if date_modifiers else ""
                            date_with_modifier = f"{modifier} {date_refs[0]}".strip()
                            # Normalize date string: remove spaces between numbers and ordinal suffixes
                            # e.g., "3 rd march" -> "3rd march"
                            date_with_modifier = re.sub(
                                r"(\d+)\s+(st|nd|rd|th)\b",
                                r"\1\2",
                                date_with_modifier,
                                flags=re.IGNORECASE,
                            )
                            bound_date = _bind_single_date(
                                date_with_modifier, now_tz_aware, tz
                            )
                        else:
                            # No modifiers: use shortcut binding path
                            # Normalize date string: remove spaces between numbers and ordinal suffixes
                            date_str_normalized = re.sub(
                                r"(\d+)\s+(st|nd|rd|th)\b",
                                r"\1\2",
                                date_refs[0],
                                flags=re.IGNORECASE,
                            )
                            bound_date = _bind_single_date(
                                date_str_normalized, now_tz_aware, tz
                            )

                            # If binding failed due to bare weekday policy, manually bind for UNKNOWN intents
                            # This enables standalone temporal inputs (e.g., "friday") to be resolved
                            # while still respecting ALLOW_BARE_WEEKDAY_BINDING for booking intents
                            if bound_date is None:
                                from luma.config.temporal import (
                                    ALLOW_BARE_WEEKDAY_BINDING,
                                )

                                if not ALLOW_BARE_WEEKDAY_BINDING:
                                    # Check if this is a bare weekday (no modifier like "this" or "next")
                                    date_str_lower = date_refs[0].lower().strip()
                                    weekday_match = re.search(
                                        r"\b(this|next)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
                                        date_str_lower,
                                    )
                                    if weekday_match and not weekday_match.group(1):
                                        # This is a bare weekday - allow binding for UNKNOWN intents
                                        # with temporal tokens (standalone temporal inputs)
                                        from datetime import timedelta

                                        from luma.calendar.calendar_binder import (
                                            _get_weekday_to_number,
                                        )

                                        weekday_map = _get_weekday_to_number()
                                        weekday_str = weekday_match.group(2)
                                        target_weekday = weekday_map.get(weekday_str)
                                        if target_weekday is not None:
                                            today_weekday = now_tz_aware.weekday()
                                            # Compute nearest future occurrence (base)
                                            # This ensures no past dates: if weekday <= today, resolve to next week
                                            days_ahead = (
                                                target_weekday - today_weekday
                                            ) % 7
                                            if days_ahead == 0:
                                                days_ahead = 7  # If today is the target weekday, resolve to next week
                                            # Bare weekday: use base (nearest future occurrence)
                                            # No modifier handling needed here as modifiers are handled earlier
                                            target_date = now_tz_aware + timedelta(
                                                days=days_ahead
                                            )
                                            bound_date = target_date.replace(
                                                hour=0,
                                                minute=0,
                                                second=0,
                                                microsecond=0,
                                            )

                        if bound_date:
                            slots["date"] = bound_date.strftime("%Y-%m-%d")
                        else:
                            logger.warning(
                                f"[UNKNOWN] _bind_single_date returned None for date_ref: {date_refs[0]}",
                                extra={
                                    "request_id": request_id,
                                    "date_ref": date_refs[0],
                                    "date_mode": date_mode,
                                },
                            )
                    except Exception as e:
                        # If normalization fails, skip date (extraction-only, no fallback)
                        logger.warning(
                            f"[UNKNOWN] Date normalization failed: {str(e)}",
                            extra={
                                "request_id": request_id,
                                "date_ref": date_refs[0] if date_refs else None,
                                "error": str(e),
                            },
                        )
                        pass

                elif date_mode == "range" and len(date_refs) >= 2:
                    # Date range: normalize both dates using _bind_single_date
                    # For UNKNOWN intents with temporal tokens, allow bare weekday range binding
                    try:
                        tz = get_timezone(timezone)
                        # Ensure now is timezone-aware for _bind_single_date
                        if now.tzinfo is None:
                            now_tz_aware = _localize_datetime(now, tz)
                        else:
                            now_tz_aware = now
                        # Normalize date strings: remove spaces between numbers and ordinal suffixes
                        start_date_str = re.sub(
                            r"(\d+)\s+(st|nd|rd|th)\b",
                            r"\1\2",
                            date_refs[0],
                            flags=re.IGNORECASE,
                        )
                        end_date_str = re.sub(
                            r"(\d+)\s+(st|nd|rd|th)\b",
                            r"\1\2",
                            date_refs[1],
                            flags=re.IGNORECASE,
                        )
                        start_date_dt = _bind_single_date(
                            start_date_str, now_tz_aware, tz
                        )
                        end_date_dt = _bind_single_date(end_date_str, now_tz_aware, tz)

                        # If binding failed due to bare weekday policy, manually bind for UNKNOWN intents
                        # This enables standalone temporal inputs (e.g., "friday to sunday") to be resolved
                        if start_date_dt is None or end_date_dt is None:
                            from luma.config.temporal import (
                                ALLOW_BARE_WEEKDAY_BINDING,
                                ALLOW_BARE_WEEKDAY_RANGE_BINDING,
                            )

                            if (
                                not ALLOW_BARE_WEEKDAY_BINDING
                                or not ALLOW_BARE_WEEKDAY_RANGE_BINDING
                            ):
                                from datetime import timedelta

                                from luma.calendar.calendar_binder import (
                                    _get_weekday_to_number,
                                )

                                weekday_map = _get_weekday_to_number()

                                # Try to bind start date if it failed
                                if start_date_dt is None:
                                    date_str_lower = date_refs[0].lower().strip()
                                    weekday_match = re.search(
                                        r"\b(this|next)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
                                        date_str_lower,
                                    )
                                    if weekday_match and not weekday_match.group(1):
                                        weekday_str = weekday_match.group(2)
                                        target_weekday = weekday_map.get(weekday_str)
                                        if target_weekday is not None:
                                            today_weekday = now_tz_aware.weekday()
                                            # Compute nearest future occurrence (base)
                                            # This ensures no past dates: if weekday <= today, resolve to next week
                                            days_ahead = (
                                                target_weekday - today_weekday
                                            ) % 7
                                            if days_ahead == 0:
                                                days_ahead = 7  # If today is the target weekday, resolve to next week
                                            # Bare weekday: use base (nearest future occurrence)
                                            target_date = now_tz_aware + timedelta(
                                                days=days_ahead
                                            )
                                            start_date_dt = target_date.replace(
                                                hour=0,
                                                minute=0,
                                                second=0,
                                                microsecond=0,
                                            )

                                # Try to bind end date if it failed
                                if end_date_dt is None:
                                    date_str_lower = date_refs[1].lower().strip()
                                    weekday_match = re.search(
                                        r"\b(this|next)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
                                        date_str_lower,
                                    )
                                    if weekday_match and not weekday_match.group(1):
                                        weekday_str = weekday_match.group(2)
                                        target_weekday = weekday_map.get(weekday_str)
                                        if target_weekday is not None:
                                            today_weekday = now_tz_aware.weekday()
                                            # Compute nearest future occurrence (base)
                                            # This ensures no past dates: if weekday <= today, resolve to next week
                                            days_ahead = (
                                                target_weekday - today_weekday
                                            ) % 7
                                            if days_ahead == 0:
                                                days_ahead = 7  # If today is the target weekday, resolve to next week
                                            # Bare weekday: use base (nearest future occurrence)
                                            target_date = now_tz_aware + timedelta(
                                                days=days_ahead
                                            )
                                            end_date_dt = target_date.replace(
                                                hour=0,
                                                minute=0,
                                                second=0,
                                                microsecond=0,
                                            )

                        if start_date_dt and end_date_dt:
                            # Fix year drift if needed (same logic as _bind_dates)
                            if start_date_dt > end_date_dt:
                                start_date_dt = _localize_datetime(
                                    datetime(
                                        end_date_dt.year,
                                        start_date_dt.month,
                                        start_date_dt.day,
                                    ),
                                    tz,
                                )
                            # EXTRACTION-ONLY: Emit raw dates as facts, not semantic date_range
                            # "march 3rd to march 8th" → dates["2026-03-03", "2026-03-08"]
                            start_date_str = start_date_dt.strftime("%Y-%m-%d")
                            end_date_str = end_date_dt.strftime("%Y-%m-%d")
                            dates_list = [start_date_str]
                            if end_date_str != start_date_str:
                                dates_list.append(end_date_str)
                            slots["date"] = (
                                dates_list if len(dates_list) > 1 else dates_list[0]
                            )
                    except Exception as e:
                        # If normalization fails, skip date_range (extraction-only)
                        logger.debug(
                            f"[UNKNOWN] Date range normalization failed: {str(e)}",
                            extra={"request_id": request_id},
                        )
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
                        time_windows = (
                            extraction_result.get("time_windows", [])
                            if extraction_result
                            else []
                        )
                        time_result = bind_times(
                            time_refs,
                            time_mode,
                            now_tz_aware,
                            tz,
                            time_windows=time_windows,
                        )
                        if time_result:
                            start_time = time_result.get("start_time")
                            if start_time:
                                slots["time"] = start_time
                    except Exception as e:
                        # If normalization fails, skip time (extraction-only, no fallback)
                        logger.debug(
                            f"[UNKNOWN] Time normalization failed: {str(e)}",
                            extra={"request_id": request_id},
                        )
                        pass

            # Service handling: Extract from semantic_booking (tenant alias normalization)
            # CRITICAL: service_id must be a TENANT alias key, never a canonical ID
            semantic_booking_for_services = {}
            if merged_semantic_result and merged_semantic_result.resolved_booking:
                semantic_booking_for_services = merged_semantic_result.resolved_booking
            elif semantic_result and semantic_result.resolved_booking:
                semantic_booking_for_services = semantic_result.resolved_booking

            services = semantic_booking_for_services.get("services", [])
            if len(services) == 1 and isinstance(services[0], dict):
                service = services[0]

                # Priority 1: Use resolved_alias if present (this is the tenant alias key)
                tenant_alias_key = service.get("resolved_alias")

                # Priority 2: If no resolved_alias, map canonical -> tenant alias key using tenant_context.aliases
                if not tenant_alias_key:
                    canonical = service.get("canonical")
                    if (
                        canonical
                        and tenant_context
                        and isinstance(tenant_context, dict)
                    ):
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
            resolved_services = booking_payload.get("services")
            # Only use booking_payload services if they exist and are non-empty
            if not resolved_services or (
                isinstance(resolved_services, list) and len(resolved_services) == 0
            ):
                resolved_services = None
            resolved_date_range = booking_payload.get("date_range")
            # For CREATE_APPOINTMENT ready responses, ensure datetime_range is available
            # Priority: booking_payload.datetime_range > calendar_booking.datetime_range
            resolved_datetime_range = booking_payload.get("datetime_range")
            if not resolved_datetime_range and calendar_booking:
                datetime_range_from_calendar = calendar_booking.get("datetime_range")
                if datetime_range_from_calendar:  # Only use if truthy
                    resolved_datetime_range = datetime_range_from_calendar
        # EXTRACTION-ONLY: Removed needs_clarification branch (always False)

        # For CREATE_RESERVATION, ensure date_range is set from calendar_booking if not already set
        if intent_payload_name == "CREATE_RESERVATION":
            # Fallback for date_range: check calendar_booking
            if not resolved_date_range:
                # Try calendar_booking first
                if calendar_booking:
                    date_range_from_calendar = calendar_booking.get("date_range")
                    if date_range_from_calendar:
                        resolved_date_range = date_range_from_calendar
                else:
                    # Fallback: get from results["stages"]["calendar"] directly (binder output)
                    calendar_stage = results.get("stages", {}).get("calendar", {})
                    calendar_booking_from_stage = (
                        calendar_stage.get("calendar_booking", {})
                        if calendar_stage
                        else {}
                    )
                    date_range_from_stage = (
                        calendar_booking_from_stage.get("date_range")
                        if calendar_booking_from_stage
                        else None
                    )
                    if date_range_from_stage:
                        resolved_date_range = date_range_from_stage

        # For CREATE_APPOINTMENT, ensure datetime_range is set from calendar_booking if not already set
        # This mirrors reservation behavior which always checks calendar_booking for date_range
        if intent_payload_name == "CREATE_APPOINTMENT" and not resolved_datetime_range:
            # Try calendar_booking first
            if calendar_booking:
                datetime_range_from_calendar = calendar_booking.get("datetime_range")
                # Only set if truthy (not None, not empty dict)
                if datetime_range_from_calendar:
                    resolved_datetime_range = datetime_range_from_calendar
            else:
                # Fallback: get from results["stages"]["calendar"] directly (binder output)
                calendar_stage = results.get("stages", {}).get("calendar", {})
                calendar_booking_from_stage = (
                    calendar_stage.get("calendar_booking", {}) if calendar_stage else {}
                )
                datetime_range_from_stage = (
                    calendar_booking_from_stage.get("datetime_range")
                    if calendar_booking_from_stage
                    else None
                )
                # Also try direct access in case datetime_range is at top level of calendar_stage
                if not datetime_range_from_stage:
                    datetime_range_from_stage = calendar_stage.get("datetime_range")
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
                if not resolved_datetime_range and semantic_booking.get(
                    "datetime_range"
                ):
                    resolved_datetime_range = semantic_booking.get("datetime_range")

        # SYNTHESIS: For CREATE_APPOINTMENT with RESOLVED status, construct datetime_range if missing
        # Decision layer is authoritative - if decision says RESOLVED, slots must include datetime_range
        # Binder output is optional; do not gate on calendar_booking.datetime_range
        if (
            intent_payload_name == "CREATE_APPOINTMENT"
            and decision_result
            and decision_result.status == "RESOLVED"
            and not resolved_datetime_range
        ):

            # Get date_range from resolved_date_range or calendar_booking
            date_range_for_synthesis = resolved_date_range
            if not date_range_for_synthesis and calendar_booking:
                date_range_for_synthesis = calendar_booking.get("date_range")

            # Get time_mode and time_refs from semantic result
            semantic_booking = (
                merged_semantic_result.resolved_booking
                if merged_semantic_result
                else {}
            )
            time_mode = semantic_booking.get("time_mode")
            time_refs = semantic_booking.get("time_refs", [])
            time_constraint = semantic_booking.get("time_constraint")

            # Check if we have date_range and valid time_mode
            if date_range_for_synthesis and time_mode in {"window", "fuzzy", "exact"}:

                # Extract start_date from date_range
                start_date = date_range_for_synthesis.get(
                    "start_date"
                ) or date_range_for_synthesis.get("start")
                if not start_date:
                    # Try end_date as fallback (for single-day appointments)
                    start_date = date_range_for_synthesis.get(
                        "end_date"
                    ) or date_range_for_synthesis.get("end")

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
                                "end": f"{date_part}T{window_end}Z",
                            }
                        else:
                            pass
                    except Exception as e:
                        pass

        # Build service_id slot from semantic output (single source of truth)
        # For CREATE_RESERVATION, always use semantic result
        if intent_payload_name == "CREATE_RESERVATION":
            if merged_semantic_result and merged_semantic_result.resolved_booking:
                semantic_services = merged_semantic_result.resolved_booking.get(
                    "services", []
                )
                if semantic_services:
                    # Get first service (primary)
                    primary = (
                        semantic_services[0]
                        if isinstance(semantic_services[0], dict)
                        else {}
                    )
                    # Prefer resolved_alias, else use tenant_service_id
                    resolved_alias = primary.get("resolved_alias")
                    if resolved_alias:
                        slots["service_id"] = resolved_alias
                    else:
                        tenant_service_id = primary.get("tenant_service_id")
                        if tenant_service_id:
                            slots["service_id"] = tenant_service_id
        elif resolved_services:
            # For other intents, use existing logic with resolved_services
            # Use tenant_service_id from service object (presentation layer)
            # Contract: tenant_service_id is the public API, canonical is internal-only
            primary = (
                resolved_services[-1]
                if isinstance(resolved_services[-1], dict)
                else (
                    resolved_services[0]
                    if isinstance(resolved_services[0], dict)
                    else {}
                )
            )

            # Priority 1: Check for resolved_alias (explicit tenant alias match from semantic resolution)
            # This preserves the explicitly mentioned tenant alias when multiple aliases map to the same canonical
            resolved_alias = (
                primary.get("resolved_alias") if isinstance(primary, dict) else None
            )
            if resolved_alias:
                slots["service_id"] = resolved_alias
                logger.debug(
                    f"[slots] Using resolved_alias (explicit match) from service: '{resolved_alias}'"
                )
            else:
                # Priority 2: Check for tenant_service_id from service object (set during annotation)
                tenant_service_id = (
                    primary.get("tenant_service_id")
                    if isinstance(primary, dict)
                    else None
                )
                if tenant_service_id:
                    slots["service_id"] = tenant_service_id
                    logger.debug(
                        f"[slots] Using tenant_service_id from service: '{tenant_service_id}'"
                    )
                else:
                    # Priority 3: Fallback to resolved_tenant_service_id from decision layer
                    resolved_tenant_service_id = (
                        results.get("stages", {})
                        .get("decision", {})
                        .get("resolved_tenant_service_id")
                    )
                    if resolved_tenant_service_id:
                        slots["service_id"] = resolved_tenant_service_id

            # FINAL NORMALIZATION: Ensure service_id is always a tenant alias key, never a canonical
            # INVARIANT: API responses must NEVER expose canonical service IDs
            if (
                slots.get("service_id")
                and tenant_context
                and isinstance(tenant_context, dict)
            ):
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
                        resolved_alias_from_service = (
                            primary.get("resolved_alias")
                            if isinstance(primary, dict)
                            else None
                        )

                        # If resolved_alias exists and maps to this canonical, use it (preserves explicit match)
                        if (
                            resolved_alias_from_service
                            and resolved_alias_from_service in aliases
                        ):
                            canonical_for_resolved = aliases.get(
                                resolved_alias_from_service
                            )
                            if canonical_for_resolved and (
                                service_id_value == canonical_for_resolved
                                or (
                                    "." in service_id_value
                                    and service_id_value.endswith(
                                        f".{canonical_for_resolved}"
                                    )
                                )
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
                                    elif (
                                        "." in service_id_value
                                        and service_id_value.endswith(
                                            f".{canonical_family}"
                                        )
                                    ):
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
                                extra={
                                    "request_id": request_id,
                                    "service_id": service_id_value,
                                },
                            )
                            slots.pop("service_id", None)

        # EXTRACTION-ONLY: Build raw temporal facts (dates[], times[]) not semantic structures
        # No date_range, datetime_range, start_date, end_date, has_datetime
        if not is_unknown_intent and intent_payload_name == "CREATE_RESERVATION":
            # Reservations: Extract raw dates from resolved_date_range
            # "next week" → dates[ start_of_week, end_of_week ]
            if resolved_date_range:
                dates_list = []
                if isinstance(resolved_date_range, dict):
                    start_date = resolved_date_range.get(
                        "start_date"
                    ) or resolved_date_range.get("start")
                    end_date = resolved_date_range.get(
                        "end_date"
                    ) or resolved_date_range.get("end")
                    if start_date:
                        dates_list.append(start_date)
                    if end_date and end_date != start_date:
                        dates_list.append(end_date)
                if dates_list:
                    slots["date"] = dates_list if len(dates_list) > 1 else dates_list[0]
            elif merged_semantic_result and merged_semantic_result.resolved_booking:
                # Fallback: bind dates from semantic result if resolved_date_range is not available
                # This handles cases where binder wasn't called but dates need to be extracted
                semantic_booking = merged_semantic_result.resolved_booking
                date_refs = semantic_booking.get("date_refs", [])
                date_mode = semantic_booking.get("date_mode", "single_day")
                if date_refs:
                    try:
                        from luma.calendar.calendar_binder import _bind_dates

                        tz = get_timezone(timezone)
                        if now.tzinfo is None:
                            now_tz_aware = _localize_datetime(now, tz)
                        else:
                            now_tz_aware = now
                        resolved_date_range = _bind_dates(
                            date_refs, date_mode, now_tz_aware, tz
                        )
                        if resolved_date_range:
                            dates_list = []
                            if isinstance(resolved_date_range, dict):
                                start_date = resolved_date_range.get(
                                    "start_date"
                                ) or resolved_date_range.get("start")
                                end_date = resolved_date_range.get(
                                    "end_date"
                                ) or resolved_date_range.get("end")
                                if start_date:
                                    dates_list.append(start_date)
                                if end_date and end_date != start_date:
                                    dates_list.append(end_date)
                            if dates_list:
                                slots["date"] = (
                                    dates_list if len(dates_list) > 1 else dates_list[0]
                                )
                    except Exception as e:
                        logger.debug(
                            f"[CREATE_RESERVATION] Date binding from semantic result failed: {str(e)}",
                            extra={"request_id": request_id},
                        )
                        pass
        elif not is_unknown_intent and intent_payload_name == "CREATE_APPOINTMENT":
            # Appointments: Extract raw dates and times from datetime_range
            # "march 3rd at 3pm" → dates["2026-03-03"], times["15:00"]
            calendar_stage = results.get("stages", {}).get("calendar", {})
            calendar_booking_from_binder = (
                calendar_stage.get("calendar_booking", {}) if calendar_stage else {}
            )
            datetime_range_from_binder = (
                calendar_booking_from_binder.get("datetime_range")
                if calendar_booking_from_binder
                else None
            )

            # Use binder output if available, otherwise fall back to resolved_datetime_range
            datetime_range_for_slots = (
                datetime_range_from_binder
                if datetime_range_from_binder
                else resolved_datetime_range
            )

            if datetime_range_for_slots:
                # Extract date from datetime_range start
                start_str = (
                    datetime_range_for_slots.get("start")
                    if isinstance(datetime_range_for_slots, dict)
                    else None
                )
                if start_str:
                    # Extract date from ISO datetime string (e.g., "2026-03-03T15:00:00Z" → "2026-03-03")
                    if "T" in start_str:
                        date_str = start_str.split("T")[0]
                    else:
                        date_str = start_str
                    slots["date"] = date_str

                    # Extract time from datetime_range start (e.g., "2026-03-03T15:00:00Z" → "15:00")
                    if "T" in start_str:
                        time_part = (
                            start_str.split("T")[1]
                            .split("+")[0]
                            .split("-")[0]
                            .split("Z")[0]
                        )
                        if ":" in time_part:
                            # Extract HH:MM portion (first 5 characters)
                            hour_min = ":".join(time_part.split(":")[:2])
                            slots["time"] = hour_min
        elif not is_unknown_intent and intent_payload_name in {
            "MODIFY_BOOKING",
            "CANCEL_BOOKING",
        }:
            # MODIFY_BOOKING and CANCEL_BOOKING use booking_id slot
            # Extract booking_id from entities
            if extraction_result and extraction_result.get("booking_id"):
                slots["booking_id"] = extraction_result["booking_id"]

        # Extract clarification_data from current_clarification if present
        clarification_data_for_response = None
        if current_clarification and isinstance(current_clarification, dict):
            clarification_data_for_response = current_clarification.get("data")

        # EXTRACTION-ONLY: No clarification reasons - always None
        public_clarification_reason = None

        # ============================================================
        # MODIFY_BOOKING TEMPORAL SLOT PROJECTION (needs_clarification)
        # ============================================================
        # Project explicitly resolved temporal values into slots for MODIFY_BOOKING
        # even when status = needs_clarification. This ensures extracted time/date values
        # are surfaced in slots, not just in semantic/context layers.
        #
        # Rules:
        # 1. If time_refs exist → project normalized time into slots.time (e.g., "15:00")
        # 2. If date_refs exist (single_day) → project normalized date into slots.date
        # 3. If date_range exists → project into slots.date_range
        #
        # This projection happens EVEN IF booking_id is missing or status = needs_clarification.
        # It's a projection fix, not an extraction fix - extraction already happened.
        if (
            not is_unknown_intent
            and intent_payload_name == "MODIFY_BOOKING"
            and needs_clarification
        ):
            # Get semantic_booking from merged_semantic_result
            semantic_booking = (
                merged_semantic_result.resolved_booking
                if merged_semantic_result
                else {}
            )

            # Project time from time_refs (if available)
            # Priority: calendar_booking.datetime_range (bound time) > time_refs (semantic reference)
            time_refs = semantic_booking.get("time_refs", [])
            time_mode = semantic_booking.get("time_mode")

            # First, try to extract bound time from calendar_booking (if available)
            time_projected = False
            if calendar_booking and calendar_booking.get("datetime_range"):
                dt_start = calendar_booking["datetime_range"].get("start", "")
                if dt_start:
                    try:
                        # Extract time portion from ISO datetime string (e.g., "2026-01-13T15:00:00Z")
                        if "T" in dt_start:
                            time_part = (
                                dt_start.split("T")[1]
                                .split("+")[0]
                                .split("-")[0]
                                .split("Z")[0]
                            )
                            # Extract HH:MM portion (first 5 characters)
                            if ":" in time_part:
                                hour_min = ":".join(time_part.split(":")[:2])
                                slots["time"] = hour_min
                                time_projected = True
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected time from calendar_booking: {slots['time']}",
                                    extra={"request_id": request_id},
                                )
                    except Exception as e:
                        logger.debug(
                            f"[MODIFY_BOOKING] Time extraction from calendar_booking failed: {str(e)}",
                            extra={"request_id": request_id},
                        )

            # Fallback: Extract time from time_refs (semantic reference)
            # If calendar binding didn't produce normalized time, normalize time_refs using bind_times
            if not time_projected and time_refs and time_mode == "exact":
                try:
                    # Try to normalize time_refs using bind_times (if calendar binding was skipped)
                    tz = get_timezone(timezone)
                    if now.tzinfo is None:
                        now_tz_aware = _localize_datetime(now, tz)
                    else:
                        now_tz_aware = now

                    time_windows = (
                        extraction_result.get("time_windows", [])
                        if extraction_result
                        else []
                    )
                    time_result = bind_times(
                        time_refs,
                        time_mode,
                        now_tz_aware,
                        tz,
                        time_windows=time_windows,
                    )

                    if time_result and time_result.get("start_time"):
                        # Extract HH:MM from normalized time (e.g., "15:00:00" -> "15:00")
                        start_time = time_result.get("start_time")
                        if isinstance(start_time, str):
                            if ":" in start_time:
                                hour_min = ":".join(start_time.split(":")[:2])
                                slots["time"] = hour_min
                                time_projected = True
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected time from normalized time_refs: {slots['time']}",
                                    extra={"request_id": request_id},
                                )
                except Exception as e:
                    logger.debug(
                        f"[MODIFY_BOOKING] Time normalization from time_refs failed: {str(e)}",
                        extra={"request_id": request_id},
                    )
                    pass

            # Project date from date_refs or date_range (if available)
            # Use date_roles to determine if dates should be projected as start_date/end_date or date
            date_mode = semantic_booking.get("date_mode")
            date_refs = semantic_booking.get("date_refs", [])
            date_roles = semantic_booking.get("date_roles", [])

            # Check if any dates have explicit roles (START_DATE or END_DATE)
            # For MODIFY_BOOKING: if date_roles are present, use start_date/end_date slots instead of date
            # CRITICAL: For MODIFY_BOOKING single dates, date_roles should be empty [] after normalization guard
            # Only assign to end_date/start_date if explicit roles are present
            has_start_role = len(date_roles) > 0 and date_roles[0] == "START_DATE"
            has_end_role = len(date_roles) > 1 and date_roles[1] == "END_DATE"
            # Also check if first date is END_DATE (single date with end_date role)
            has_end_role_single = len(date_roles) > 0 and date_roles[0] == "END_DATE"

            # MODIFY_BOOKING single date normalization: NEVER use end_date/start_date slots for single dates
            # This prevents missing_slots=["end_date"] from causing role leakage
            # CRITICAL: Force empty roles for ALL MODIFY_BOOKING single dates, regardless of semantic date_roles
            # This is a rule-removal fix: single dates in MODIFY_BOOKING must be treated as generic "date"
            is_modify_booking_single_date = (
                intent_payload_name == "MODIFY_BOOKING"
                and len(date_refs) == 1
                and date_mode == "single_day"
            )
            if is_modify_booking_single_date:
                # Force empty roles - do not assign to end_date/start_date based on date_roles
                # This overrides any incorrect role assignment from the semantic layer
                has_start_role = False
                has_end_role = False
                has_end_role_single = False
                logger.debug(
                    f"[MODIFY_BOOKING] Single date normalization guard: Forcing empty roles. "
                    f"date_roles={date_roles}, date_refs={date_refs}",
                    extra={
                        "request_id": request_id,
                        "date_roles": date_roles,
                        "date_refs": date_refs,
                    },
                )

            # Check if calendar_booking has resolved dates (from calendar binding)
            if calendar_booking:
                # For single-day dates, extract from datetime_range or date_range
                if date_mode == "single_day":
                    resolved_date = None
                    # Try datetime_range first (appointments)
                    if calendar_booking.get("datetime_range"):
                        dt_start = calendar_booking["datetime_range"].get("start", "")
                        if dt_start and "T" in dt_start:
                            resolved_date = dt_start.split("T")[0]
                    # Fallback to date_range (reservations or date-only)
                    elif calendar_booking.get("date_range"):
                        date_range = calendar_booking["date_range"]
                        start_date = date_range.get("start_date") or date_range.get(
                            "start"
                        )
                        if start_date:
                            resolved_date = (
                                start_date.split("T")[0]
                                if "T" in start_date
                                else start_date
                            )

                    # Project based on date_roles (if present)
                    if resolved_date:
                        if has_end_role_single:
                            # Single date with END_DATE role → use end_date slot
                            slots["end_date"] = resolved_date
                            logger.debug(
                                f"[MODIFY_BOOKING] Projected single date with END_DATE role to end_date: {resolved_date}",
                                extra={"request_id": request_id},
                            )
                        elif has_start_role:
                            # Single date with START_DATE role → use start_date slot
                            slots["start_date"] = resolved_date
                            logger.debug(
                                f"[MODIFY_BOOKING] Projected single date with START_DATE role to start_date: {resolved_date}",
                                extra={"request_id": request_id},
                            )
                        else:
                            # No explicit role → use date slot
                            slots["date"] = resolved_date
                            logger.debug(
                                f"[MODIFY_BOOKING] Projected single date to date slot: {resolved_date}",
                                extra={"request_id": request_id},
                            )

                # For date ranges, extract from date_range
                elif date_mode == "range" and calendar_booking.get("date_range"):
                    date_range = calendar_booking["date_range"]
                    start_date = date_range.get("start_date") or date_range.get("start")
                    end_date = date_range.get("end_date") or date_range.get("end")
                    if start_date and end_date:
                        # Normalize dates (remove time portion if present)
                        start_date_clean = (
                            start_date.split("T")[0]
                            if "T" in start_date
                            else start_date
                        )
                        end_date_clean = (
                            end_date.split("T")[0] if "T" in end_date else end_date
                        )

                        # Check if dates have explicit roles
                        if has_start_role and has_end_role:
                            # Both roles present → use start_date and end_date slots
                            slots["start_date"] = start_date_clean
                            slots["end_date"] = end_date_clean
                            logger.debug(
                                f"[MODIFY_BOOKING] Projected date range with roles to start_date/end_date: {start_date_clean}, {end_date_clean}",
                                extra={"request_id": request_id},
                            )
                        elif start_date_clean == end_date_clean:
                            # Single date (start == end) → use date slot
                            slots["date"] = start_date_clean
                            logger.debug(
                                f"[MODIFY_BOOKING] Projected single date from range to date slot: {start_date_clean}",
                                extra={"request_id": request_id},
                            )
                        else:
                            # Date range without explicit roles → use date_range slot
                            slots["date_range"] = {
                                "start": start_date_clean,
                                "end": end_date_clean,
                            }
                            logger.debug(
                                f"[MODIFY_BOOKING] Projected date range to date_range slot: {start_date_clean}-{end_date_clean}",
                                extra={"request_id": request_id},
                            )

            # Fallback: If calendar binding didn't produce dates, try semantic_booking.date_range
            if (
                not slots.get("date")
                and not slots.get("date_range")
                and not slots.get("start_date")
                and not slots.get("end_date")
            ):
                if semantic_booking.get("date_range"):
                    date_range_sem = semantic_booking["date_range"]
                    if isinstance(date_range_sem, dict):
                        start_date = date_range_sem.get(
                            "start_date"
                        ) or date_range_sem.get("start")
                        end_date = date_range_sem.get("end_date") or date_range_sem.get(
                            "end"
                        )
                        if start_date and end_date:
                            # Normalize dates (remove time portion if present)
                            start_date_clean = (
                                start_date.split("T")[0]
                                if "T" in start_date
                                else start_date
                            )
                            end_date_clean = (
                                end_date.split("T")[0] if "T" in end_date else end_date
                            )

                            # Check if dates have explicit roles
                            if has_start_role and has_end_role:
                                # Both roles present → use start_date and end_date slots
                                slots["start_date"] = start_date_clean
                                slots["end_date"] = end_date_clean
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected semantic date_range with roles to start_date/end_date: {start_date_clean}, {end_date_clean}",
                                    extra={"request_id": request_id},
                                )
                            elif start_date_clean == end_date_clean:
                                # Single date (start == end) → use date slot
                                slots["date"] = start_date_clean
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected single date from semantic date_range to date slot: {start_date_clean}",
                                    extra={"request_id": request_id},
                                )
                            else:
                                # Date range → use date_range slot
                                slots["date_range"] = {
                                    "start": start_date_clean,
                                    "end": end_date_clean,
                                }
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected semantic date_range to date_range slot: {start_date_clean}-{end_date_clean}",
                                    extra={"request_id": request_id},
                                )

            # Final fallback: If still no dates projected and we have date_refs, bind them directly
            if (
                not slots.get("date")
                and not slots.get("date_range")
                and not slots.get("start_date")
                and not slots.get("end_date")
            ):
                if date_refs and date_mode == "single_day":
                    # Single date: bind the first date_ref
                    try:
                        tz = get_timezone(timezone)
                        if now.tzinfo is None:
                            now_tz_aware = _localize_datetime(now, tz)
                        else:
                            now_tz_aware = now

                        bound_date = _bind_single_date(date_refs[0], now_tz_aware, tz)
                        if bound_date:
                            resolved_date = bound_date.strftime("%Y-%m-%d")
                            # Project based on date_roles (if present)
                            if has_end_role_single:
                                slots["end_date"] = resolved_date
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected bound date_ref with END_DATE role to end_date: {resolved_date}",
                                    extra={"request_id": request_id},
                                )
                            elif has_start_role:
                                slots["start_date"] = resolved_date
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected bound date_ref with START_DATE role to start_date: {resolved_date}",
                                    extra={"request_id": request_id},
                                )
                            else:
                                # No explicit role → use date slot
                                slots["date"] = resolved_date
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected bound date_ref to date slot: {resolved_date}",
                                    extra={"request_id": request_id},
                                )
                        else:
                            # Binding failed (e.g., ambiguous weekday like "friday") → project raw date_ref as fallback
                            # This ensures extracted dates are surfaced in slots even if binding fails
                            date_ref_raw = date_refs[0]
                            if has_end_role_single:
                                slots["end_date"] = date_ref_raw
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected raw date_ref (binding failed) with END_DATE role to end_date: {date_ref_raw}",
                                    extra={"request_id": request_id},
                                )
                            elif has_start_role:
                                slots["start_date"] = date_ref_raw
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected raw date_ref (binding failed) with START_DATE role to start_date: {date_ref_raw}",
                                    extra={"request_id": request_id},
                                )
                            else:
                                # No explicit role → use date slot
                                slots["date"] = date_ref_raw
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected raw date_ref (binding failed) to date slot: {date_ref_raw}",
                                    extra={"request_id": request_id},
                                )
                    except Exception as e:
                        logger.debug(
                            f"[MODIFY_BOOKING] Failed to bind date_ref: {str(e)}",
                            extra={"request_id": request_id},
                        )
                        # Fallback: project raw date_ref even if binding exception occurred
                        if date_refs and len(date_refs) > 0:
                            date_ref_raw = date_refs[0]
                            if not has_end_role_single and not has_start_role:
                                slots["date"] = date_ref_raw
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected raw date_ref (exception fallback) to date slot: {date_ref_raw}",
                                    extra={"request_id": request_id},
                                )
                elif date_refs and date_mode == "range" and len(date_refs) >= 2:
                    # Date range: bind both date_refs
                    try:
                        tz = get_timezone(timezone)
                        if now.tzinfo is None:
                            now_tz_aware = _localize_datetime(now, tz)
                        else:
                            now_tz_aware = now

                        start_date_dt = _bind_single_date(
                            date_refs[0], now_tz_aware, tz
                        )
                        end_date_dt = _bind_single_date(date_refs[1], now_tz_aware, tz)
                        if start_date_dt and end_date_dt:
                            start_date_clean = start_date_dt.strftime("%Y-%m-%d")
                            end_date_clean = end_date_dt.strftime("%Y-%m-%d")

                            # Check if dates have explicit roles
                            if has_start_role and has_end_role:
                                slots["start_date"] = start_date_clean
                                slots["end_date"] = end_date_clean
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected bound date_refs with roles to start_date/end_date: {start_date_clean}, {end_date_clean}",
                                    extra={"request_id": request_id},
                                )
                            elif start_date_clean == end_date_clean:
                                # Single date (start == end) → use date slot
                                slots["date"] = start_date_clean
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected single date from bound date_refs to date slot: {start_date_clean}",
                                    extra={"request_id": request_id},
                                )
                            else:
                                # Date range → use date_range slot
                                slots["date_range"] = {
                                    "start": start_date_clean,
                                    "end": end_date_clean,
                                }
                                logger.debug(
                                    f"[MODIFY_BOOKING] Projected bound date_refs to date_range slot: {start_date_clean}-{end_date_clean}",
                                    extra={"request_id": request_id},
                                )
                    except Exception as e:
                        logger.debug(
                            f"[MODIFY_BOOKING] Failed to bind date_refs: {str(e)}",
                            extra={"request_id": request_id},
                        )

            # Final normalization guard: MODIFY_BOOKING single date → force date slot
            # This ensures that even if missing_slots contains "end_date", we materialize as "date" slot
            # This guard runs AFTER all projection logic to prevent role leakage
            # CRITICAL: Apply to ALL MODIFY_BOOKING single dates, regardless of semantic date_roles
            if (
                intent_payload_name == "MODIFY_BOOKING"
                and date_refs
                and len(date_refs) == 1
                and date_mode == "single_day"
            ):
                # Single date → prefer date slot over end_date/start_date
                # Check if we incorrectly assigned to end_date or start_date
                if slots.get("end_date") and not slots.get("date"):
                    # Move end_date to date slot
                    resolved_date_value = slots.pop("end_date")
                    slots["date"] = resolved_date_value
                    logger.debug(
                        f"[MODIFY_BOOKING] Post-projection normalization guard: Moved single date from end_date to date slot. "
                        f"date_roles={date_roles}, date_refs={date_refs}",
                        extra={
                            "request_id": request_id,
                            "date_roles": date_roles,
                            "date_refs": date_refs,
                        },
                    )
                elif slots.get("start_date") and not slots.get("date"):
                    # Move start_date to date slot
                    resolved_date_value = slots.pop("start_date")
                    slots["date"] = resolved_date_value
                    logger.debug(
                        f"[MODIFY_BOOKING] Post-projection normalization guard: Moved single date from start_date to date slot. "
                        f"date_roles={date_roles}, date_refs={date_refs}",
                        extra={
                            "request_id": request_id,
                            "date_roles": date_roles,
                            "date_refs": date_refs,
                        },
                    )

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
        # EXTRACTION-ONLY: Removed decision_result check (always None)
        if not is_unknown_intent and intent_payload_name == "MODIFY_BOOKING":
            # Ensure booking_id is always included (should already be set earlier, but verify)
            if (
                extraction_result
                and extraction_result.get("booking_id")
                and not slots.get("booking_id")
            ):
                slots["booking_id"] = extraction_result["booking_id"]
            # Determine booking mode
            semantic_booking = (
                merged_semantic_result.resolved_booking
                if merged_semantic_result
                else {}
            )
            booking_mode = semantic_booking.get("booking_mode", domain)
            is_reservation = booking_mode == "reservation" or domain == "reservation"

            # Check for date_range (reservations) - may come from calendar binding or semantic normalization
            has_date_range = False
            if is_reservation:
                # Check resolved_date_range (from calendar binding or semantic result)
                if resolved_date_range and isinstance(resolved_date_range, dict):
                    start_date = resolved_date_range.get(
                        "start_date"
                    ) or resolved_date_range.get("start")
                    end_date = resolved_date_range.get(
                        "end_date"
                    ) or resolved_date_range.get("end")
                    # Only include date_range if we have both start and end (allow start == end for destination-only moves)
                    if start_date and end_date:
                        # Handle "from X to Y" pattern: collapse to destination only
                        # Detect pattern in original text: "from <date> to <date>"
                        text_lower = text.lower() if text else ""
                        # Match "from <something> to <something>" pattern
                        from_to_pattern = r"\bfrom\s+.*?\s+to\s+.*"
                        if re.search(from_to_pattern, text_lower):
                            # Collapse to destination date only
                            slots["date_range"] = {"start": end_date, "end": end_date}
                        else:
                            slots["date_range"] = {"start": start_date, "end": end_date}
                        # Preserve legacy delta fields for reservations
                        slots["start_date"] = start_date
                        slots["end_date"] = end_date
                        has_date_range = True
                # Also check semantic result for date_range (may have been set by semantic resolver)
                elif semantic_booking.get("date_range") and isinstance(
                    semantic_booking.get("date_range"), dict
                ):
                    date_range_from_semantic = semantic_booking.get("date_range")
                    start_date = date_range_from_semantic.get(
                        "start_date"
                    ) or date_range_from_semantic.get("start")
                    end_date = date_range_from_semantic.get(
                        "end_date"
                    ) or date_range_from_semantic.get("end")
                    if start_date and end_date:
                        # Handle "from X to Y" pattern: collapse to destination only
                        text_lower = text.lower() if text else ""
                        from_to_pattern = r"\bfrom\s+.*?\s+to\s+.*"
                        if re.search(from_to_pattern, text_lower):
                            slots["date_range"] = {"start": end_date, "end": end_date}
                        else:
                            slots["date_range"] = {"start": start_date, "end": end_date}
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

                # STAGE 2: Gate has_datetime on time_constraint.mode == "exact" AND date present
                # Check if exact time constraint exists (mode == "exact")
                has_exact_time_constraint = (
                    time_constraint is not None
                    and isinstance(time_constraint, dict)
                    and time_constraint.get("mode") == "exact"
                )

                # Check if date-related change exists
                has_date_change = (
                    len(date_refs) > 0
                    or (date_mode and date_mode != "none" and date_mode != "flexible")
                    or bool(semantic_booking.get("date_range"))
                )

            # For appointments (service mode), set has_datetime ONLY if:
            # 1. time_constraint.mode == "exact" (exact time, not fuzzy/window)
            # 2. AND date is present
            # This prevents fuzzy times (morning/evening/window constraints) from setting has_datetime
            # For reservations, only date_range is output (no has_datetime)
            if not is_reservation:
                # Appointment mode: exact time constraint + date change → has_datetime = true
                if has_exact_time_constraint and has_date_change:
                    slots["has_datetime"] = True

                # For appointments: ensure datetime_range exists when has_datetime=True
                # This is required by API contract
                if slots.get("has_datetime"):
                    # First, try to get datetime_range from resolved sources
                    if not slots.get("datetime_range"):
                        if resolved_datetime_range:
                            slots["datetime_range"] = resolved_datetime_range
                        elif calendar_booking and calendar_booking.get(
                            "datetime_range"
                        ):
                            slots["datetime_range"] = calendar_booking.get(
                                "datetime_range"
                            )
                        else:
                            # Call build_datetime_range_for_api to construct minimal structure
                            build_datetime_range_for_api(
                                slots,
                                semantic_booking,
                                domain,
                                request_id=request_id,
                                user_id=user_id,
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
                    if (
                        merged_semantic_result
                        and merged_semantic_result.resolved_booking
                    ):
                        resolved_services = merged_semantic_result.resolved_booking.get(
                            "services", []
                        )
                        if resolved_services:
                            # Get resolved_alias from first service (explicit match)
                            primary_service = (
                                resolved_services[0]
                                if isinstance(resolved_services[0], dict)
                                else {}
                            )
                            resolved_alias = primary_service.get("resolved_alias")
                            if resolved_alias and resolved_alias in aliases:
                                # resolved_alias is a valid tenant alias key - use it
                                tenant_alias_key = resolved_alias
                                logger.info(
                                    f"[response] Using resolved_alias from semantic result: '{tenant_alias_key}'",
                                    extra={"request_id": request_id},
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
                                elif (
                                    "." in service_id_value
                                    and service_id_value.endswith(
                                        f".{canonical_family}"
                                    )
                                ):
                                    tenant_alias_key = alias_key
                                    break

                    if tenant_alias_key:
                        # Replace canonical with tenant alias key
                        slots["service_id"] = tenant_alias_key
                        logger.info(
                            f"[response] Normalized service_id from canonical '{service_id_value}' to tenant alias '{tenant_alias_key}'",
                            extra={"request_id": request_id},
                        )
                    else:
                        # service_id is not in aliases and doesn't match any canonical
                        # This violates the invariant - canonical IDs must not appear in responses
                        # Log error and remove service_id to prevent canonical exposure
                        logger.error(
                            f"[response] INVARIANT VIOLATION: service_id '{service_id_value}' is not a tenant alias key and doesn't map to any canonical. Removing from response.",
                            extra={
                                "request_id": request_id,
                                "service_id": service_id_value,
                            },
                        )
                        slots.pop("service_id", None)

        # Initialize slots safely
        slots = slots or {}

        # Build response body from extracted facts and intent only
        # Response is built ONLY from facts + intent - NOT from decision layer
        # Use facts built directly from semantic output (single source of truth)
        response_builder = ResponseBuilder()
        # Remove date_time_pairs from semantic_facts_for_response if present
        if semantic_facts_for_response:
            semantic_facts_for_response.pop("date_time_pairs", None)

        # Fallback to aggregate_extraction_facts only if semantic_facts_for_response is not available
        facts_for_response = semantic_facts_for_response
        if not facts_for_response:
            facts_for_response = aggregate_extraction_facts(
                extraction_result=extraction_result,
                slots=slots,
                intent=intent_payload_name,
            )
            if facts_for_response:
                facts_for_response.pop("date_time_pairs", None)

        response_body = response_builder.build_response_body(
            intent_payload=intent_payload,
            facts=facts_for_response,
            debug_data=results if debug_mode else None,
            operation=intent_resp.get("operation"),
        )

        # Add time_constraint to response payload (shadow output - no behavior change)
        # Extract time_constraint from semantic_result if available
        time_constraint_dict = None
        if (
            semantic_result
            and hasattr(semantic_result, "time_constraint")
            and semantic_result.time_constraint
        ):
            # Normalize to API format: ensure label is set appropriately
            time_constraint_dict = semantic_result.time_constraint.copy()
            # Ensure label is set: "morning|evening|user_exact" or None
            # For user-specified exact times, use None (or could use "user_exact" if needed)
            # For fuzzy times (morning/evening), label is already set from time_constraints.py
            response_body["time_constraint"] = time_constraint_dict
        elif (
            merged_semantic_result
            and hasattr(merged_semantic_result, "time_constraint")
            and merged_semantic_result.time_constraint
        ):
            time_constraint_dict = merged_semantic_result.time_constraint.copy()
            response_body["time_constraint"] = time_constraint_dict

        # STAGE 3: Debug log for time source of truth
        logger.debug(
            "[TIME_SOURCE_OF_TRUTH] time_constraint=%s slots.time=%s facts.times=%s",
            time_constraint_dict,
            slots.get("time") if slots else None,
            facts_for_response.get("times") if facts_for_response else None,
            extra={
                "request_id": request_id,
                "time_constraint": time_constraint_dict,
                "slots_time": slots.get("time") if slots else None,
                "facts_times": (
                    facts_for_response.get("times") if facts_for_response else None
                ),
            },
        )

        # Removed per-stage logging - consolidated trace emitted at end

        return jsonify(response_body)

    except Exception as e:  # noqa: BLE001
        logger.error(
            f"Processing failed: {str(e)}",
            extra={
                "request_id": request_id,
                "error_type": type(e).__name__,
                "text_length": len(text) if "text" in locals() else 0,
            },
            exc_info=True,
        )
        return jsonify({"success": False, "error": f"Processing failed: {str(e)}"}), 500
