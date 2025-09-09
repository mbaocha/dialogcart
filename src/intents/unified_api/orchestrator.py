"""
Orchestrator for Rasa + LLM fallback
Always returns a list of intents (single or multiple)
"""
import requests
from .config import RASA_URL, LLM_URL, FALLBACK_CONF, FALLBACK_THRESHOLD, RASA_CONFIDENCE_THRESHOLD
from intents.shared.mappers import map_rasa_to_intent_meta, map_rasa_to_intent_metas, map_rasa_to_actions, map_llm_to_intent_meta, map_llm_multi_to_intent_metas, map_llm_to_actions

def is_sufficient(conf: str) -> bool:
    """Check if confidence is sufficient to avoid fallback"""
    return FALLBACK_CONF.get(conf.lower(), 0) >= FALLBACK_THRESHOLD


def classify(text: str, sender_id: str | None = None, route: str | None = None):
    """
    Classify intent with configurable routing
    
    Args:
        text: Input text to classify
        sender_id: Session identifier
        route: Routing option - "rasa", "llm", or None (fallback)
    
    Returns:
        Classification result with source and actions/intents
    """
    print(f"DEBUG: classify called with route='{route}', text='{text[:50]}...'")
    try:
        # Route directly to Rasa if specified
        if route == "rasa":
            print("DEBUG: Routing to Rasa only")
            return _classify_rasa_only(text, sender_id)
        
        # Route directly to LLM if specified  
        elif route == "llm":
            print("DEBUG: Routing to LLM only")
            return _classify_llm_only(text, sender_id)
        
        # Default: Rasa → LLM fallback
        else:
            print("DEBUG: Using fallback routing")
            return _classify_with_fallback(text, sender_id)
            
    except Exception as e:
        print(f"DEBUG: Main classify function failed: {e}")
        return {
            "source": "fallback",
            "sender_id": sender_id,
            "actions": [{
                "action": "unknown",
                "product": None,
                "quantity": None,
                "unit": None,
                "container": None,
                "confidence": "low"
            }]
        }

def _classify_rasa_only(text: str, sender_id: str | None = None):
    """Route directly to Rasa only"""
    try:
        rasa_response = requests.post(
            f"{RASA_URL}/",
            json={"action": "predict", "text": text, "sender_id": (sender_id or "unified_api")},
            timeout=5,
        )
        rasa_response.raise_for_status()
        rasa_json = rasa_response.json()
        # If Rasa already produced actions (from Rasa app), pass-through
        if isinstance(rasa_json, dict) and rasa_json.get("actions"):
            return {
                "source": "rasa", 
                "sender_id": sender_id, 
                "intent": rasa_json.get("intent"),
                "actions": rasa_json.get("actions", [])
            }
        
        # Check if it's a modify_cart and parse actions
        actions = map_rasa_to_actions(rasa_json)
        print(f"DEBUG: unified_api - rasa_json={rasa_json}")
        print(f"DEBUG: unified_api - parsed actions={actions}")
        if actions:
            return {
                "source": "rasa", 
                "sender_id": sender_id, 
                "intent": "modify_cart",
                "actions": [action.dict() for action in actions]
            }
        
        # Check if this is a modify_cart intent and ensure action-based format
        nlu = rasa_json.get("nlu") or rasa_json
        intent_name = (nlu.get("intent") or {}).get("name", "").upper()
        
        if intent_name in ("MODIFY_CART", "SHOPPING_COMMAND"):
            # For modify_cart intents, always return action-based format even if parsing failed
            return {
                "source": "rasa", 
                "sender_id": sender_id, 
                "intent": "modify_cart",
                "actions": [{
                    "action": "unknown",
                    "product": None,
                    "quantity": None,
                    "unit": None,
                    "confidence": "low",
                    "confidence_score": 0.0
                }]
            }
        
        # Fallback to intent-based approach for other intents
        rasa_metas = map_rasa_to_intent_metas(rasa_json)
        return {"source": "rasa", "sender_id": sender_id, "intents": [meta.dict() for meta in rasa_metas]}
        
    except Exception as e:
        print(f"DEBUG: Rasa-only routing failed: {e}")
        # Return error response instead of falling back
        return {
            "source": "rasa_error",
            "sender_id": sender_id,
            "actions": [{
                "action": "unknown",
                "product": None,
                "quantity": None,
                "unit": None,
                "container": None,
                "confidence": "low"
            }],
            "error": str(e)
        }

def _classify_llm_only(text: str, sender_id: str | None = None):
    """Route directly to LLM only"""
    try:
        llm_response = requests.post(
            f"{LLM_URL}/classify",
            json={"text": text, "sender_id": (sender_id or "unified_api")},
            timeout=20
        )
        llm_response.raise_for_status()
        llm_json = llm_response.json()
        
        # Check if LLM returned actions (action-based processing)
        if "actions" in llm_json.get("result", {}):
            return llm_json.get("result", {})
        
        # Check if this is a modify_cart intent and ensure action-based format
        result = llm_json.get("result") or llm_json
        intents = result.get("intents", [])
        modify_cart_intents = [
            intent for intent in intents 
            if intent.get("intent", "").upper() in ("MODIFY_CART", "SHOPPING_COMMAND")
        ]
        
        if modify_cart_intents:
            # For modify_cart intents, always return action-based format
            return {
                "source": "llm", 
                "sender_id": sender_id,
                "intent": "modify_cart",
                "actions": [{
                    "action": "unknown",
                    "product": None,
                    "quantity": None,
                    "unit": None,
                    "confidence": "low",
                    "confidence_score": 0.0
                }]
            }
        
        # Fallback to traditional intent-based processing for other intents
        llm_metas = map_llm_multi_to_intent_metas(llm_json)
        return {
            "source": "llm", 
            "sender_id": sender_id,
            "intents": [meta.dict() for meta in llm_metas]
        }
    except Exception as e:
        print(f"DEBUG: LLM-only routing failed: {e}")
        # Return error response instead of falling back
        return {
            "source": "llm_error",
            "sender_id": sender_id,
            "intents": [{
                "intent": "NONE",
                "confidence": "low",
                "entities": []
            }],
            "error": str(e)
        }

def _classify_with_fallback(text: str, sender_id: str | None = None):
    """Original Rasa → LLM fallback behavior"""
    try:
        # Step 1: Try Rasa first
        rasa_response = requests.post(
            f"{RASA_URL}/",
            json={"action": "predict", "text": text, "sender_id": (sender_id or "unified_api")},
            timeout=5,
        )
        rasa_response.raise_for_status()
        rasa_json = rasa_response.json()
        # If Rasa already produced actions (from Rasa app), pass-through
        if isinstance(rasa_json, dict) and rasa_json.get("actions"):
            return {
                "source": "rasa", 
                "sender_id": sender_id, 
                "intent": rasa_json.get("intent"),
                "actions": rasa_json.get("actions", [])
            }
        
        # Check if it's a modify_cart and parse actions
        actions = map_rasa_to_actions(rasa_json)
        if actions:
            # For actions, we don't need confidence checking - just return them
            return {
                "source": "rasa", 
                "sender_id": sender_id, 
                "intent": "modify_cart",
                "actions": [action.dict() for action in actions]
            }
        
        # Check if this is a modify_cart intent and ensure action-based format
        nlu = rasa_json.get("nlu") or rasa_json
        intent_name = (nlu.get("intent") or {}).get("name", "").upper()
        
        if intent_name in ("MODIFY_CART", "SHOPPING_COMMAND"):
            # For modify_cart intents, always return action-based format even if parsing failed
            return {
                "source": "rasa", 
                "sender_id": sender_id, 
                "intent": "modify_cart",
                "actions": [{
                    "action": "unknown",
                    "product": None,
                    "quantity": None,
                    "unit": None,
                    "confidence": "low",
                    "confidence_score": 0.0
                }]
            }
        
        # Fallback to intent-based approach for other intents
        rasa_metas = map_rasa_to_intent_metas(rasa_json)
        
        # Prefer numeric confidence score when available; fallback to bucket
        # Use first intent for confidence check (they should all have same confidence)
        first_meta = rasa_metas[0] if rasa_metas else None
        if first_meta:
            score = getattr(first_meta, "confidence_score", None)
            if score is not None:
                if score >= RASA_CONFIDENCE_THRESHOLD:
                    return {"source": "rasa", "sender_id": sender_id, "intents": [meta.dict() for meta in rasa_metas]}
            else:
                if is_sufficient(first_meta.confidence):
                    return {"source": "rasa", "sender_id": sender_id, "intents": [meta.dict() for meta in rasa_metas]}

        # Step 2: Fallback to LLM
        llm_response = requests.post(
            f"{LLM_URL}/classify",
            json={"text": text, "sender_id": (sender_id or "unified_api")},
            timeout=20
        )
        llm_response.raise_for_status()
        llm_json = llm_response.json()
        
        # Check if LLM returned actions (action-based processing)
        if "actions" in llm_json.get("result", {}):
            return llm_json.get("result", {})
        
        # Check if this is a modify_cart intent and ensure action-based format
        result = llm_json.get("result") or llm_json
        intents = result.get("intents", [])
        modify_cart_intents = [
            intent for intent in intents 
            if intent.get("intent", "").upper() in ("MODIFY_CART", "SHOPPING_COMMAND")
        ]
        
        if modify_cart_intents:
            # For modify_cart intents, always return action-based format
            return {
                "source": "llm", 
                "sender_id": sender_id,
                "intent": "modify_cart",
                "actions": [{
                    "action": "unknown",
                    "product": None,
                    "quantity": None,
                    "unit": None,
                    "confidence": "low",
                    "confidence_score": 0.0
                }]
            }
        
        # Fallback to traditional intent-based processing for other intents
        llm_metas = map_llm_multi_to_intent_metas(llm_json)
        return {
            "source": "llm", 
            "sender_id": sender_id,
            "intents": [meta.dict() for meta in llm_metas]
        }
        
    except Exception as e:
        # Final fallback
        return {
            "source": "fallback",
            "sender_id": sender_id,
            "actions": [{
                "action": "unknown",
                "product": None,
                "quantity": None,
                "unit": None,
                "container": None,
                "confidence": "low"
            }]
        }

