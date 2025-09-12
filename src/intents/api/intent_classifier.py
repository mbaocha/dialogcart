import logging
from flask import Flask, request, jsonify

# Import normalizer from trainings directory
import sys
from pathlib import Path
# Ensure local trainings package is importable with highest precedence
sys.path.insert(0, str(Path(__file__).parent.parent / "trainings"))
import normalization.normalizer  # 👈 Ensures it's registered
print("✅ normalization.normalizer imported from intent_classifier")

# Import our modular services
from .rasa_service import RasaService
from ..core.orchestrator import IntentOrchestrator


# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Initialize services
rasa_service = RasaService()
orchestrator = IntentOrchestrator(rasa_service)


def initialize_app():
    """Initialize the app - services are already initialized."""
    logger.info("🌐 Server running on http://0.0.0.0:9000")
    logger.info("📡 Awaiting requests...")
    logger.info("-" * 50)


# All Rasa functionality moved to RasaService module
# All LLM validation moved to IntentOrchestrator module


@app.route("/classify", methods=["POST"])
def classify_route():
    """Intent classification with optional LLM validation."""
    try:
        data = request.get_json(force=True)
        text = (data.get("text") or "").strip()
        sender_id = (data.get("sender_id") or "intent_classifier").strip() or "intent_classifier"
        validate = data.get("validate", False)
        
        # Normalize validate parameter: accept both boolean and string values
        if isinstance(validate, str):
            if validate.lower() == "true":
                validate = True
            elif validate.lower() == "false":
                validate = False
            elif validate.lower() != "force":
                return jsonify({"error": "validate must be false, true, force, or their string equivalents"}), 400
        elif validate not in [False, True, "force"]:
            return jsonify({"error": "validate must be false, true, force, or their string equivalents"}), 400
        
        if not text:
            return jsonify({"error": "text required"}), 400
        
        # Use orchestrator for classification
        result = orchestrator.classify(text, sender_id, validate)
        
        if result["success"]:
            return jsonify(result)
        else:
            return jsonify(result), 500
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "result": {
                "source": "error",
                "confidence_score": 0.0,
                "intents": [{
                    "intent": "NONE",
                    "confidence": "low",
                    "entities": []
                }]
            }
        }), 500


@app.route("/", methods=["POST"])
def rasa_api():
    """Main Rasa API entrypoint."""
    body = request.get_json(force=True)
    action = body.get("action")

    # Use orchestrator to handle Rasa requests
    result, status_code = orchestrator.handle_rasa_request(action, **body)
    return _resp(status_code, result)


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "intent_classifier"
    })


def _resp(code, body):
    return jsonify(body), code


# Initialize the app after all functions are defined
initialize_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000)