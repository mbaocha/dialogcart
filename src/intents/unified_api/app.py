"""
Unified Intent API - Single entry point for Rasa + LLM fallback
Always returns a list of intents (single or multiple)
"""
from flask import Flask, request, jsonify
from intents.unified_api.orchestrator import classify
from intents.unified_api.config import PORT, DEBUG
import requests

app = Flask(__name__)

@app.route("/classify", methods=["POST"])
def classify_route():
    """Intent classification endpoint with optional routing"""
    try:
        data = request.get_json(force=True)
        text = (data.get("text") or "").strip()
        sender_id = (data.get("sender_id") or "unified_api").strip() or "unified_api"
        route = data.get("route")  # Optional: "rasa", "llm", or None (fallback)
        
        if not text:
            return jsonify({"error": "text required"}), 400
        
        # Validate route parameter
        if route and route not in ["rasa", "llm"]:
            return jsonify({"error": "route must be 'rasa' or 'llm'"}), 400
        
        result = classify(text, sender_id=sender_id, route=route)
        
        return jsonify({
            "success": True,
            "result": result
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "result": {
                "source": "error",
                "intents": [{
                    "intent": "NONE",
                    "confidence": "low",
                    "entities": []
                }]
            }
        }), 500

@app.route("/debug/session/<sender_id>", methods=["GET"])
def debug_session(sender_id):
    """Debug endpoint to show session state"""
    try:
        # Get session from session service
        response = requests.post(
            "http://session:9200/session/get",
            json={"sender_id": sender_id},
            timeout=5
        )
        response.raise_for_status()
        session_data = response.json().get("session", {})
        
        return jsonify({
            "success": True,
            "sender_id": sender_id,
            "session": session_data
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "sender_id": sender_id
        }), 500

@app.route("/debug/clear/<sender_id>", methods=["POST"])
def clear_session(sender_id):
    """Debug endpoint to clear session state"""
    try:
        # Clear session from session service
        response = requests.post(
            "http://session:9200/session/clear",
            json={"sender_id": sender_id},
            timeout=5
        )
        response.raise_for_status()
        result = response.json()
        
        return jsonify({
            "success": True,
            "sender_id": sender_id,
            "cleared": result.get("cleared", True)
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "sender_id": sender_id
        }), 500

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "unified_api"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)