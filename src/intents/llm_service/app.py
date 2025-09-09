from flask import Flask, request, jsonify
import requests
import json
from typing import Dict, Any, List, Optional
import logging

from intents.llm_service.core.nlu_service import NLUService
from intents.llm_service.core.conversation_manager import ConversationManager
from intents.shared.mappers import map_llm_to_actions

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Initialize services
nlu_service = NLUService()
conversation_manager = ConversationManager()

@app.route('/', methods=['POST'])
@app.route('/classify', methods=['POST'])
def classify():
    """Main classification endpoint."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        action = data.get('action') or 'predict'
        text = data.get('text', '').strip()
        sender_id = data.get('sender_id', 'default')
        
        if not text:
            return jsonify({"error": "No text provided"}), 400
        
        if action == 'predict':
            return handle_predict(text, sender_id)
        else:
            return jsonify({"error": f"Unknown action: {action}"}), 400
            
    except Exception as e:
        logger.error(f"Error in classify: {e}")
        return jsonify({"error": str(e)}), 500

def handle_predict(text: str, sender_id: str) -> Dict[str, Any]:
    """Handle prediction request."""
    try:
        # Get NLU result
        nlu_model = nlu_service.classify([{"role": "user", "content": text}])
        nlu_result = nlu_model.dict()
        # Attach original text for downstream heuristics
        nlu_result["text"] = text
        
        if not nlu_result or not nlu_result.get('intents'):
            return jsonify({
                "result": {
                    "intents": [],
                    "sender_id": sender_id,
                    "source": "llm"
                },
                "success": True
            })
        
        # Check if we have cart modification intents that should use action-based processing
        intents = nlu_result.get('intents', [])
        modify_cart_intents = [
            intent for intent in intents 
            if intent.get("intent", "").upper() in ("MODIFY_CART", "SHOPPING_COMMAND")
        ]
        
        if modify_cart_intents:
            # Use action-based processing for cart modifications
            actions = map_llm_to_actions({"result": nlu_result, "text": text})
            if actions:
                return jsonify({
                    "result": {
                        "source": "llm",
                        "sender_id": sender_id,
                        "intent": "modify_cart",
                        "actions": [action.dict() for action in actions]
                    },
                    "success": True
                })
        
        # Fallback to traditional intent-based processing for other intents
        res = conversation_manager.process_message(text, nlu_result, sender_id)
        
        if res.intents:
            # Use the first intent (most confident)
            first_intent = res.intents[0]
            entities = [e.dict() for e in first_intent.entities]
            intent_name = first_intent.intent
            
            return jsonify({
                "result": {
                    "intents": [{
                        "intent": intent_name,
                        "confidence": first_intent.confidence,
                        "confidence_score": first_intent.confidence_score,
                        "entities": entities
                    }],
                    "sender_id": sender_id,
                    "source": "llm"
                },
                "success": True
            })
        else:
            return jsonify({
                "result": {
                    "intents": [],
                    "sender_id": sender_id,
                    "source": "llm"
                },
                "success": True
            })
            
    except Exception as e:
        logger.error(f"Error in handle_predict: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "service": "llm"})

@app.route('/debug/memory/<sender_id>', methods=['GET'])
def debug_memory(sender_id: str):
    """Debug endpoint to view conversation memory."""
    try:
        memory = conversation_manager.get_memory(sender_id)
        return jsonify({
            "sender_id": sender_id,
            "memory": memory,
            "success": True
        })
    except Exception as e:
        logger.error(f"Error in debug_memory: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/debug/clear/<sender_id>', methods=['POST'])
def clear_memory(sender_id: str):
    """Clear conversation memory for a sender."""
    try:
        conversation_manager.clear_memory(sender_id)
        return jsonify({
            "message": f"Memory cleared for sender {sender_id}",
            "success": True
        })
    except Exception as e:
        logger.error(f"Error in clear_memory: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9100, debug=True)