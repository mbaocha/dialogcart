"""
LLM Service REST API - Multi-intent classification
"""
from flask import Flask, request, jsonify
from core.nlu_service import NLUService
from core.conversation_manager import ConversationManager
from core.config import CONFIG
import os, sys, re

# Prefer absolute imports via PYTHONPATH=/app/src
from intents.shared.session_client import SessionClient

def _import_shared_cm():
    try:
        from intents.shared.conversation_manager import SharedConversationManager as _CM
        return _CM
    except Exception:
        try:
            from shared.conversation_manager import SharedConversationManager as _CM
            return _CM
        except Exception as e2:
            # Fallback minimal implementation to allow service to boot
            class _FallbackCM:
                def __init__(self, session_client, sender_id: str):
                    self.session_client = session_client
                    self.sender_id = sender_id or "anonymous"

                def get_conversation_history(self):
                    try:
                        session = self.session_client.get_session(self.sender_id) or {}
                        return session.get("history", []) or []
                    except Exception:
                        return []

                def process_message(self, text, entities, intent):
                    entities = entities or []
                    slot_map = {"product": "product", "quantity": "quantity", "unit": "unit"}
                    updates = {}
                    # Accept both {entity,value} and direct {product,quantity,unit}
                    for ent in entities:
                        n = ent.get("entity"); v = ent.get("value")
                        if n and v is not None and n in slot_map:
                            updates[slot_map[n]] = v
                            continue
                        for k in ("product", "quantity", "unit"):
                            if ent.get(k) is not None:
                                updates[k] = ent.get(k)
                    try:
                        session = self.session_client.get_session(self.sender_id) or {}
                        slots = session.get("slots", {})
                        slots.update(updates)
                        self.session_client.update_session(self.sender_id, {"slots": slots})
                    except Exception:
                        slots = updates
                    # Return combined entity dict
                    combined = {}
                    for k in ("product", "quantity", "unit"):
                        if k in slots and slots[k] is not None:
                            combined[k] = slots[k]
                    enhanced = [combined] if combined else []
                    return {"intent": (intent or "NONE").upper(), "entities": enhanced, "slots": slots}

            return _FallbackCM

app = Flask(__name__)
SESSION_URL = os.getenv("SESSION_URL", "http://session:9200")
session_client = SessionClient(SESSION_URL)

def run_multi_intent(text: str, sender_id: str = "anonymous"):
    """
    Run multi-intent classification using shared conversation manager
    Returns all detected intents and entities
    """
    nlu = NLUService()
    
    # Use shared conversation manager
    SharedConversationManager = _import_shared_cm()
    conv_mgr = SharedConversationManager(session_client, sender_id)
    
    # Get conversation history for LLM context
    history = conv_mgr.get_conversation_history()
    messages = history + [{"role": "user", "content": text}]
    
    # Classify with LLM
    res = nlu.classify(messages)
    
    # Process with conversation manager
    if res.intents:
        # Use the first intent (most confident)
        first_intent = res.intents[0]
        entities = [e.model_dump() for e in first_intent.entities]
        intent_name = first_intent.intent
        
        # Process message with memory
        enhanced_result = conv_mgr.process_message(text, entities, intent_name)

        # Fallback extraction if no entities made it through
        if not enhanced_result.get("entities"):
            qty = None
            unit = None
            prod = None

            # Prefer model-provided fields if present
            if first_intent.entities:
                it = first_intent.entities[0]
                prod = getattr(it, "product", None) or prod
                qty = getattr(it, "quantity", None) or qty
                unit = getattr(it, "unit", None) or unit

            # Regex fallback from text
            if qty is None or unit is None:
                m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|lb|piece|pieces|bag|bags|box|boxes|pc|pcs|unit|units)?", text, re.I)
                if m:
                    try:
                        qty = qty if qty is not None else float(m.group(1))
                    except Exception:
                        qty = qty
                    if unit is None:
                        unit = (m.group(2) or "").lower() or None

            if prod is None:
                # Heuristic: take alpha words excluding common verbs/stopwords
                tokens = re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", text)
                stop = {"add", "to", "cart", "make", "it", "please", "and", "put", "i", "want"}
                for t in tokens:
                    if t.lower() not in stop:
                        prod = t
                        break

            fallback_entities = []
            fb = {}
            if prod is not None:
                fb["product"] = prod
            if qty is not None:
                fb["quantity"] = qty
            if unit is not None:
                fb["unit"] = unit
            if fb:
                fallback_entities = [fb]

            if fallback_entities:
                enhanced_result = conv_mgr.process_message(text, fallback_entities, intent_name)
        
        return {
            "intents": [{
                "intent": enhanced_result["intent"],
                "confidence": first_intent.confidence,
                "entities": enhanced_result["entities"],
            }],
            "sender_id": sender_id
        }
    else:
        return {
            "intents": [{
                "intent": "NONE",
                "confidence": "low",
                "entities": [],
            }],
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
