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
    POST /book - Process service/reservation booking request
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
from datetime import datetime  # noqa: E402
from flask import Flask, request, jsonify, g  # noqa: E402
from luma.calendar.calendar_binder import bind_calendar  # noqa: E402
from luma.resolution.semantic_resolver import resolve_semantics  # noqa: E402
from luma.grouping.appointment_grouper import group_appointment  # noqa: E402
from luma.structure.interpreter import interpret_structure  # noqa: E402
from luma.grouping.reservation_intent_resolver import ReservationIntentResolver  # noqa: E402
from luma.extraction.matcher import EntityMatcher  # noqa: E402
from luma.clarification import render_clarification  # noqa: E402
from luma.config import config  # noqa: E402
from luma.logging_config import setup_logging, generate_request_id  # noqa: E402

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
    global entity_matcher, intent_resolver  # noqa: PLW0603
    
    logger.info("=" * 60)
    logger.info("Initializing Luma Service/Reservation Booking Pipeline")
    
    try:
        # Initialize intent resolver (lightweight, no file I/O)
        intent_resolver = ReservationIntentResolver()
        
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
            "/book": {
                "method": "POST",
                "description": "Process service/reservation booking request",
                "parameters": {
                    "text": "string (required) - Booking request text",
                    "domain": "string (optional) - 'service' or 'reservation' (default: 'service')",
                    "timezone": "string (optional) - Timezone for calendar binding (default: 'UTC')",
                }
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


@app.route("/book", methods=["POST"])
def book():
    """
    Process service/reservation booking request.
    
    Request body:
    {
        "text": "book haircut tomorrow at 2pm",
        "domain": "service",           // optional, default: "service"
        "timezone": "UTC"              // optional, default: "UTC"
    }
    
    Response:
    {
        "success": true,
        "data": {
            "stages": {
                "extraction": {...},
                "intent": {...},
                "structure": {...},
                "grouping": {...},
                "semantic": {...},
                "calendar": {...}
            },
            "clarification": {
                "needed": true,
                "message": "Do you mean 2am or 2pm?"
            }
        }
    }
    """
    request_id = g.request_id if hasattr(g, 'request_id') else 'unknown'
    
    if intent_resolver is None:
        logger.error("Pipeline not initialized", extra={'request_id': request_id})
        return jsonify({
            "success": False,
            "error": "Pipeline not initialized"
        }), 503
    
    # Parse request
    try:
        data = request.get_json()
        if not data or "text" not in data:
            logger.warning("Missing 'text' parameter", extra={'request_id': request_id})
            return jsonify({
                "success": False,
                "error": "Missing 'text' parameter in request body"
            }), 400
        
        text = data["text"]
        domain = data.get("domain", "service")
        timezone = data.get("timezone", "UTC")
        
        if not text or not isinstance(text, str):
            logger.warning("Invalid text parameter", extra={'request_id': request_id})
            return jsonify({
                "success": False,
                "error": "'text' must be a non-empty string"
            }), 400
        
        # Log request
        logger.info(
            "Processing booking request",
            extra={
                'request_id': request_id,
                'text_length': len(text),
                'domain': domain,
                'timezone': timezone
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
    
    # Process booking request
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
            intent, confidence = intent_resolver.resolve_intent(text, extraction_result)
            results["stages"]["intent"] = {"intent": intent, "confidence": confidence}
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
            semantic_result = resolve_semantics(grouped_result, extraction_result)
            results["stages"]["semantic"] = semantic_result.to_dict()
        except Exception as e:
            results["stages"]["semantic"] = {"error": str(e)}
            return jsonify({"success": False, "data": results}), 500
        
        # Stage 6: Calendar Binding
        try:
            calendar_result = bind_calendar(
                semantic_result,
                now,
                timezone,
                intent=intent,
                entities=extraction_result
            )
            results["stages"]["calendar"] = calendar_result.to_dict()
        except Exception as e:
            results["stages"]["calendar"] = {"error": str(e)}
            return jsonify({"success": False, "data": results}), 500
        
        # Extract clarification message if needed
        clarification = None
        if semantic_result.needs_clarification and semantic_result.clarification:
            try:
                message = render_clarification(semantic_result.clarification)
                clarification = {
                    "needed": True,
                    "reason": semantic_result.clarification.reason.value,
                    "message": message
                }
            except Exception:
                clarification = {
                    "needed": True,
                    "reason": semantic_result.clarification.reason.value
                }
        elif calendar_result.needs_clarification and calendar_result.clarification:
            try:
                message = render_clarification(calendar_result.clarification)
                clarification = {
                    "needed": True,
                    "reason": calendar_result.clarification.reason.value,
                    "message": message
                }
            except Exception:
                clarification = {
                    "needed": True,
                    "reason": calendar_result.clarification.reason.value
                }
        else:
            clarification = {"needed": False}
        
        processing_time = round((time.time() - start_time) * 1000, 2)
        
        # Log successful processing
        if config.LOG_PERFORMANCE_METRICS:
            logger.info(
                "Booking request processed successfully",
                extra={
                    'request_id': request_id,
                    'processing_time_ms': processing_time,
                    'needs_clarification': clarification["needed"],
                    'intent': intent
                }
            )
        
        return jsonify({
            "success": True,
            "data": results,
            "clarification": clarification
        })
    
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


@app.errorhandler(404)
def not_found(error):  # noqa: ARG001, pylint: disable=unused-argument
    """Handle 404 errors."""
    return jsonify({
        "success": False,
        "error": "Endpoint not found",
        "available_endpoints": ["/book", "/health", "/info"]
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
    logger.info(f"Try: curl -X POST http://localhost:{PORT}/book -H 'Content-Type: application/json' -d '{{\"text\": \"book haircut tomorrow at 2pm\"}}'")
    
    # Run Flask
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False
    )


if __name__ == "__main__":
    main()
