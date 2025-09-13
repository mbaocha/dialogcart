"""
Intent Orchestrator - Business logic and LLM validation
"""
import logging
from typing import Dict, Any, List

from .prompts import validator_prompt
from .models import ValidationResult, Action
from ..config.settings import RASA_CONFIDENCE_THRESHOLD
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


class IntentOrchestrator:
    """Orchestrator for intent classification with optional LLM validation"""

    def __init__(self, rasa_service):
        self.rasa_service = rasa_service
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0).with_structured_output(ValidationResult)
        # Simple in-memory slot storage
        self._slot_memory = {}

    def _get_confidence_score(self, nlu: dict) -> float:
        """Get confidence score from NLU result"""
        try:
            intent = (nlu or {}).get("intent") or {}
            return float(intent.get("confidence") or 0.0)
        except Exception:
            return 0.0

    def _maybe_validate_actions(self, text: str, actions: list, validate: bool, nlu: dict) -> list:
        """Optionally validate actions with LLM if confidence below threshold."""
        if not validate:
            return actions
        score = self._get_confidence_score(nlu)
        if score >= RASA_CONFIDENCE_THRESHOLD:
            return actions
        try:
            messages = [
                {"role": "system", "content": validator_prompt()},
                {
                    "role": "user",
                    "content": f'User said: "{text}"\nRasa extracted: {actions}\nIs this mapping correct? If not, return corrected actions.'
                },
            ]
            result = self.llm.invoke(messages)
            return [a.dict() for a in result.actions]
        except Exception:
            # Fail-open: keep original actions
            return actions

    def _validate_actions_with_llm(self, text: str, actions: list) -> list:
        """Validate actions with LLM (always performs validation)."""
        try:
            messages = [
                {"role": "system", "content": validator_prompt()},
                {
                    "role": "user",
                    "content": f'User said: "{text}"\nRasa extracted: {actions}\nIs this mapping correct? If not, return corrected actions.'
                },
            ]
            result = self.llm.invoke(messages)
            return [a.dict() for a in result.actions]
        except Exception:
            logger.exception("LLM validation failed")
            return actions

    def classify(self, text: str, sender_id: str = "intent_classifier", validate=False) -> Dict[str, Any]:
        """Classify intent with optional LLM validation"""
        try:
            rasa_result = self.rasa_service.predict(text, sender_id)

            if "error" in rasa_result:
                return {
                    "success": False,
                    "error": rasa_result["error"],
                    "result": {
                        "source": "error",
                        "sender_id": sender_id,
                        "intent": "NONE",
                        "confidence_score": 0.0,
                        "actions": [],
                        "slots": {},
                        "validation_performed": False,
                        "validation_mode": validate
                    }
                }

            nlu = rasa_result.get("nlu", {})
            intent = rasa_result.get("intent")
            confidence_score = self._get_confidence_score(nlu)

            # Update slot memory BEFORE action parsing so action parser can use it
            updated_slots = self._get_updated_slots(sender_id, intent, rasa_result.get("entities", []), text)
            print(f"DEBUG: Orchestrator updated_slots: {updated_slots}")
            
            actions: List[dict] = []

            # Handle modify_cart with mapper (now with slot memory available)
            if intent and intent.upper() in ("MODIFY_CART", "SHOPPING_COMMAND"):
                from .mappers import map_rasa_to_actions
                mapped = map_rasa_to_actions({"nlu": nlu, "slots": updated_slots})
                actions = [a.dict() if isinstance(a, Action) else a for a in mapped]

            # Handle inquire_product (action + product)
            elif intent and intent.upper() == "INQUIRE_PRODUCT":
                entities = rasa_result.get("entities", [])
                products = [e for e in entities if e.get("entity") == "product"]
                actions_ents = [e for e in entities if e.get("entity") == "action"]

                # Deduplicate actions by action value
                seen_actions = set()
                
                # Create actions for each product-action pair
                for act in actions_ents:
                    action_val = act.get("value")
                    
                    # Skip if we've already seen this action
                    if action_val in seen_actions:
                        continue
                    seen_actions.add(action_val)
                    
                    # match with next product if available
                    if products:
                        product = products.pop(0).get("value")
                        action = {
                            "action": action_val,
                            "confidence": "high",
                            "confidence_score": confidence_score,
                            "product": product
                        }
                        actions.append(action)
                    else:
                        # If no explicit product, try to get from slot memory
                        product_from_slots = updated_slots.get("last_mentioned_product") or updated_slots.get("last_inquired_product")
                        if product_from_slots:
                            action = {
                                "action": action_val,
                                "confidence": "high",
                                "confidence_score": confidence_score,
                                "product": product_from_slots
                            }
                            print(f"DEBUG: Using product from slot memory for inquire_product: {product_from_slots}")
                        else:
                            # If no product in slots either, create action without product field
                            action = {
                                "action": action_val,
                                "confidence": "high",
                                "confidence_score": confidence_score
                            }
                            print(f"DEBUG: No product available for inquire_product action")
                        actions.append(action)

            # Handle cart_action (action + container)
            elif intent and intent.upper() == "CART_ACTION":
                entities = rasa_result.get("entities", [])
                containers = [e for e in entities if e.get("entity") == "container"]
                actions_ents = [e for e in entities if e.get("entity") == "action"]

                for act in actions_ents:
                    action_val = act.get("value")
                    # match with container if available
                    container = containers[0].get("value") if containers else None
                    actions.append({
                        "action": action_val,
                        "confidence": "high",
                        "confidence_score": confidence_score,
                        "container": container
                    })

            # Validation logic (only for modify_cart)
            validation_performed = False
            source = "rasa"

            if intent and intent.upper() == "MODIFY_CART":
                if validate == "force":
                    actions = self._validate_actions_with_llm(text, actions)
                    validation_performed = True
                    source = "rasa_force_validated"
                elif validate is True:
                    original_actions = actions.copy()
                    actions = self._maybe_validate_actions(text, actions, True, nlu)
                    validation_performed = (actions != original_actions)

            
            return {
                "success": True,
                "result": {
                    "source": source,
                    "sender_id": sender_id,
                    "intent": intent,
                    "confidence_score": confidence_score,
                    "actions": actions,
                    "slots": updated_slots,  # Use updated slots from simple memory
                    "validation_performed": validation_performed,
                    "validation_mode": validate
                }
            }

        except Exception as e:
            logger.exception("Classification failed")
            # Try to get existing slots even on error
            try:
                import sys
                import os
                sys.path.append(os.path.dirname(os.path.dirname(__file__)))
                from simple_slot_memory import slot_memory
                existing_slots = slot_memory.get_slots(sender_id)
            except:
                existing_slots = {}
            
            return {
                "success": False,
                "error": str(e),
                "result": {
                    "source": "error",
                    "sender_id": sender_id,
                    "intent": "NONE",
                    "confidence_score": 0.0,
                    "actions": [],
                    "slots": existing_slots,
                    "validation_performed": False,
                    "validation_mode": validate
                }
            }

    def handle_rasa_request(self, action: str, **kwargs) -> Dict[str, Any]:
        """Handle Rasa API requests (train, predict)"""
        if action == "train":
            intent = kwargs.get("intent")
            examples = kwargs.get("examples", [])

            if not intent or not examples:
                return {"error": "Both 'intent' and 'examples' are required."}, 400

            result = self.rasa_service.train_intent(intent, examples)
            if "error" in result:
                return result, 500
            return result, 200

        elif action == "predict":
            text = kwargs.get("text", "").strip()
            sender_id = kwargs.get("sender_id", "anonymous")

            if not text:
                return {"error": "'text' is required."}, 400

            result = self.rasa_service.predict(text, sender_id)
            if "error" in result:
                return result, 500
            return result, 200

        return {"error": f"Unknown action '{action}'."}, 404

    def _get_updated_slots(self, sender_id: str, intent: str, entities: list, text: str) -> Dict[str, Any]:
        """Simple inline slot memory implementation"""
        try:
            # Initialize slots for sender if not exists
            if sender_id not in self._slot_memory:
                self._slot_memory[sender_id] = {}
            
            slots = self._slot_memory[sender_id]
            
            # Update conversation turn
            slots["conversation_turn"] = slots.get("conversation_turn", 0) + 1
            slots["last_intent"] = intent
            
            # Extract entities
            products = [e.get("value") for e in entities if e.get("entity") == "product"]
            quantities = [e.get("value") for e in entities if e.get("entity") == "quantity"]
            units = [e.get("value") for e in entities if e.get("entity") == "unit"]
            actions = [e.get("value") for e in entities if e.get("entity") == "action"]
            containers = [e.get("value") for e in entities if e.get("entity") == "container"]
            
            # Universal product memory
            if products:
                slots["last_mentioned_product"] = products[0]
                
                # Intent-specific product memory
                if intent == "modify_cart":
                    slots["last_product_added"] = products[0]
                    # Update shopping list
                    shopping_list = slots.get("shopping_list", [])
                    for product in products:
                        if product not in shopping_list:
                            shopping_list.append(product)
                    slots["shopping_list"] = shopping_list
                
                elif intent == "inquire_product":
                    slots["last_inquired_product"] = products[0]
            
            # Quantity and unit memory
            if quantities and intent in ["modify_cart", "inquire_product"]:
                try:
                    import re
                    qty_str = quantities[0]
                    match = re.search(r'[-+]?\d*\.?\d+', qty_str)
                    if match:
                        slots["last_quantity"] = float(match.group())
                except (ValueError, TypeError):
                    pass
            
            if units and intent in ["modify_cart", "inquire_product"]:
                slots["last_unit"] = units[0]
            
            # Intent-specific slots
            if intent == "inquire_product" and actions:
                slots["last_inquiry_type"] = actions[0]
            
            elif intent == "cart_action":
                if actions:
                    slots["last_cart_action"] = actions[0]
                    # Update cart state
                    action = actions[0].lower()
                    if action in ["clear", "empty", "remove all", "wipe"]:
                        slots["cart_state"] = "empty"
                    elif action in ["show", "view", "display", "check", "list"]:
                        slots["cart_state"] = "has_items"
                
                if containers:
                    slots["last_container"] = containers[0]
            
            # Handle contextual updates
            if intent == "modify_cart" and self._is_contextual_update(text):
                # Use last mentioned product for contextual updates
                if slots.get("last_mentioned_product"):
                    slots["last_product_added"] = slots["last_mentioned_product"]
            
            return slots.copy()
            
        except Exception as e:
            logger.warning(f"Slot memory update failed: {e}")
            return {}
    
    def _is_contextual_update(self, text: str) -> bool:
        """Check if this is a contextual update"""
        contextual_phrases = ["add it", "make it", "change it", "update it", "modify it", "set it"]
        text_lower = text.lower()
        return any(phrase in text_lower for phrase in contextual_phrases)
