#!/usr/bin/env python3
"""
Luma Entity Extraction REST API

A Flask-based REST API for entity extraction with support for:
- Rule-based extraction
- LLM fallback (optional)
- Fuzzy matching (optional)
- Intent mapping
- Ordinal references
- Structural validation

Usage:
    python luma/api.py
    
    or
    
    gunicorn -w 4 -b 0.0.0.0:9001 luma.api:app

Endpoints:
    POST /extract - Extract entities from text
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
from flask import Flask, request, jsonify, g  # noqa: E402
from luma.core.pipeline import EntityExtractionPipeline  # noqa: E402
from luma.config import config  # noqa: E402
from luma.logging_config import setup_logging, generate_request_id  # noqa: E402

# Optional features
try:
    from luma.extraction import FUZZY_AVAILABLE
except ImportError:
    FUZZY_AVAILABLE = False

# Apply config settings
ENABLE_LLM_FALLBACK = config.ENABLE_LLM_FALLBACK
ENABLE_FUZZY_MATCHING = config.ENABLE_FUZZY_MATCHING and FUZZY_AVAILABLE
ENABLE_INTENT_MAPPER = config.ENABLE_INTENT_MAPPER
LLM_MODEL = config.LLM_MODEL
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

# Global pipeline instance
pipeline = None


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


def init_pipeline():
    """Initialize the extraction pipeline with configuration."""
    global pipeline  # noqa: PLW0603
    
    logger.info("=" * 60)
    logger.info("Initializing Luma Extraction Pipeline", extra={
        'llm_fallback': ENABLE_LLM_FALLBACK,
        'fuzzy_matching': ENABLE_FUZZY_MATCHING,
        'intent_mapping': ENABLE_INTENT_MAPPER,
        'llm_model': LLM_MODEL if ENABLE_LLM_FALLBACK else None
    })
    
    try:
        pipeline = EntityExtractionPipeline(
            use_luma=True,
            enable_llm_fallback=ENABLE_LLM_FALLBACK,
            llm_model=LLM_MODEL if ENABLE_LLM_FALLBACK else None,
        )
        logger.info("Pipeline initialized successfully")
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to initialize pipeline: {e}", exc_info=True)
        return False


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    if pipeline is None:
        return jsonify({
            "status": "unhealthy",
            "message": "Pipeline not initialized"
        }), 503
    
    return jsonify({
        "status": "healthy",
        "features": {
            "llm_fallback": ENABLE_LLM_FALLBACK,
            "fuzzy_matching": ENABLE_FUZZY_MATCHING,
            "intent_mapping": ENABLE_INTENT_MAPPER,
        }
    })


@app.route("/info", methods=["GET"])
def info():
    """API information endpoint."""
    return jsonify({
        "name": "Luma Entity Extraction API",
        "version": "1.0.0",
        "description": "Entity extraction with NER, grouping, and intent mapping",
        "endpoints": {
            "/extract": {
                "method": "POST",
                "description": "Extract entities from text",
                "parameters": {
                    "text": "string (required) - Input text to analyze",
                    "force_llm": "boolean (optional) - Force LLM extraction",
                    "enable_fuzzy": "boolean (optional) - Enable fuzzy matching",
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
        "features": {
            "llm_fallback": ENABLE_LLM_FALLBACK,
            "fuzzy_matching": ENABLE_FUZZY_MATCHING,
            "intent_mapping": ENABLE_INTENT_MAPPER,
        },
        "configuration": {
            "llm_model": LLM_MODEL if ENABLE_LLM_FALLBACK else None,
            "port": PORT,
        }
    })


@app.route("/extract", methods=["POST"])
def extract():
    """
    Extract entities from input text.
    
    Request body:
    {
        "text": "add 2 kg rice",
        "force_llm": false,           // optional
        "enable_fuzzy": false         // optional
    }
    
    Response:
    {
        "success": true,
        "data": {
            "status": "success",
            "original_sentence": "add 2 kg rice",
            "parameterized_sentence": "add 2 kg producttoken",
            "groups": [{
                "action": "add",
                "intent": "add",
                "intent_confidence": 0.98,
                "products": ["rice"],
                "quantities": ["2"],
                "units": ["kg"],
                "brands": [],
                "variants": [],
                "ordinal_ref": null
            }],
            "grouping_result": {
                "status": "ok",
                "reason": null,
                "route": "rule"
            },
            "notes": "Luma pipeline (route=rule)"
        }
    }
    """
    request_id = g.request_id if hasattr(g, 'request_id') else 'unknown'
    
    if pipeline is None:
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
        force_llm = data.get("force_llm", False)
        _enable_fuzzy = data.get("enable_fuzzy", False) and ENABLE_FUZZY_MATCHING  # noqa: F841
        
        if not text or not isinstance(text, str):
            logger.warning("Invalid text parameter", extra={'request_id': request_id})
            return jsonify({
                "success": False,
                "error": "'text' must be a non-empty string"
            }), 400
        
        # Log extraction start
        logger.info(
            "Processing extraction request",
            extra={
                'request_id': request_id,
                'text_length': len(text),
                'force_llm': force_llm
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
    
    # Process extraction
    try:
        start_time = time.time()
        
        # Extract entities and get dict format
        result_dict = pipeline.extract_dict(text, force_llm=force_llm)
        
        processing_time = round((time.time() - start_time) * 1000, 2)
        
        # Log successful extraction
        if config.LOG_PERFORMANCE_METRICS:
            logger.info(
                "Extraction completed successfully",
                extra={
                    'request_id': request_id,
                    'processing_time_ms': processing_time,
                    'groups_count': len(result_dict.get('groups', [])),
                    'route': result_dict.get('grouping_result', {}).get('route'),
                    'text_length': len(text)
                }
            )
        
        # Note: Fuzzy matching can be added here if needed
        # if _enable_fuzzy and FUZZY_AVAILABLE:
        #     from luma.extraction import FuzzyEntityMatcher
        #     fuzzy = FuzzyEntityMatcher(pipeline.entities, threshold=88)
        #     ... merge fuzzy results ...
        
        return jsonify({
            "success": True,
            "data": result_dict
        })
    
    except Exception as e:  # noqa: BLE001
        logger.error(
            f"Extraction failed: {str(e)}",
            extra={
                'request_id': request_id,
                'error_type': type(e).__name__,
                'text_length': len(text)
            },
            exc_info=True
        )
        return jsonify({
            "success": False,
            "error": f"Extraction failed: {str(e)}"
        }), 500


@app.errorhandler(404)
def not_found(error):  # noqa: ARG001, pylint: disable=unused-argument
    """Handle 404 errors."""
    return jsonify({
        "success": False,
        "error": "Endpoint not found",
        "available_endpoints": ["/extract", "/health", "/info"]
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
    logger.info("Luma Entity Extraction API")
    logger.info(f"Starting server on http://localhost:{PORT}")
    logger.info("=" * 60)
    
    # Initialize pipeline
    if not init_pipeline():
        logger.error("Failed to start API - pipeline initialization failed")
        sys.exit(1)
    
    logger.info(f"API ready! Listening on port {PORT}")
    logger.info(f"Try: curl -X POST http://localhost:{PORT}/extract -H 'Content-Type: application/json' -d '{{\"text\": \"add 2 kg rice\"}}'")
    
    # Run Flask
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False
    )


if __name__ == "__main__":
    main()

