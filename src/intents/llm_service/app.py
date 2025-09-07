"""
LLM Service REST API - Multi-intent classification
"""
from flask import Flask, request, jsonify
from core.nlu_service import NLUService
from core.conversation_manager import ConversationManager
from core.config import CONFIG

app = Flask(__name__)

def run_multi_intent(text: str, sender_id: str = "anonymous"):
    """
    Run multi-intent classification using LLM directly
    Returns all detected intents and entities
    """
    nlu = NLUService()
    # Direct LLM call without conversation manager
    res = nlu.classify([{"role": "user", "content": text}])
    
    # Convert to list format
    intents = []
    for ib in res.intents:
        intents.append({
            "intent": ib.intent,
            "confidence": ib.confidence,
            "entities": [e.model_dump() for e in ib.entities],
        })
    
    return {
        "intents": intents,
        "sender_id": sender_id
    }

def run_single_turn(text: str):
    """
    Run single-turn classification using existing LLM logic
    Returns the last detected intent and entities
    """
    nlu = NLUService()
    # Single-turn manager: new instance per request; no persistence
    mgr = ConversationManager(nlu, CONFIG)
    lines = mgr.handle(text)
    
    # Extract intent and entities from the last processed intent
    intent = "NONE"
    entities = []
    confidence = "medium"
    
    if mgr.memory:
        last = mgr.memory[-1]
        intent = (last.get("intent") or "NONE").upper()
        entities = [e.model_dump() for e in last.get("entities", [])]
        # Use confidence from the last intent if available
        if hasattr(last, 'confidence'):
            confidence = last.get('confidence', 'medium')
    
    return {
        "intent": intent,
        "confidence": confidence,
        "entities": entities
    }

@app.route("/classify", methods=["POST"])
def classify():
    """Multi-intent classification endpoint"""
    try:
        data = request.get_json(force=True)
        text = (data.get("text") or "").strip()
        sender_id = (data.get("sender_id") or "anonymous").strip() or "anonymous"
        
        if not text:
            return jsonify({"error": "text required"}), 400
        
        result = run_multi_intent(text, sender_id)
        
        return jsonify({
            "success": True,
            "result": result
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "result": {
                "intents": []
            }
        }), 500

@app.route("/classify-single", methods=["POST"])
def classify_single():
    """Single-intent classification endpoint (backwards compatibility)"""
    try:
        data = request.get_json(force=True)
        text = (data.get("text") or "").strip()
        
        if not text:
            return jsonify({"error": "text required"}), 400
        
        result = run_single_turn(text)
        
        return jsonify({
            "success": True,
            "result": result
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "result": {
                "intent": "NONE",
                "confidence": "low",
                "entities": []
            }
        }), 500

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "service": "llm"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9100)
