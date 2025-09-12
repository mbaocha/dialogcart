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

    def classify(self, text: str, sender_id: str = "intent_classifier", validate = False) -> Dict[str, Any]:
        """Classify intent with optional LLM validation"""
        try:
            # Get Rasa prediction
            rasa_result = self.rasa_service.predict(text, sender_id)
            
            if "error" in rasa_result:
                return {
                    "success": False,
                    "error": rasa_result["error"],
                    "result": {
                        "source": "error",
                        "confidence_score": 0.0,
                        "intents": [{
                            "intent": "NONE",
                            "confidence": "low",
                            "entities": []
                        }]
                    }
                }
            
            nlu = rasa_result.get("nlu", {})
            entities = rasa_result.get("entities", [])
            intent = rasa_result.get("intent")

            # Handle modify_cart intents with action processing
            if intent and intent.upper() in ("MODIFY_CART", "SHOPPING_COMMAND"):
                from .mappers import map_rasa_to_actions
                actions = map_rasa_to_actions({"nlu": nlu})
                actions_dict = [a.dict() if isinstance(a, Action) else a for a in actions]
                
                # Handle validation logic based on validate parameter
                validation_performed = False
                source = "rasa"
                
                if validate == "force":
                    # Always validate regardless of confidence
                    actions_dict = self._validate_actions_with_llm(text, actions_dict)
                    validation_performed = True
                    source = "rasa_force_validated"
                elif validate is True:
                    # Validate only if confidence below threshold
                    original_actions = actions_dict.copy()
                    actions_dict = self._maybe_validate_actions(text, actions_dict, True, nlu)
                    validation_performed = (actions_dict != original_actions)
                # else validate is False - no validation
                
                confidence_score = self._get_confidence_score(nlu)
                logger.info(f"DEBUG - confidence_score: {confidence_score}, nlu: {nlu}")
                
                return {
                    "success": True,
                    "result": {
                        "source": source,
                        "sender_id": sender_id,
                        "intent": "modify_cart",
                        "confidence_score": confidence_score,
                        "actions": actions_dict,
                        "validation_performed": validation_performed,
                        "validation_mode": validate
                    }
                }
            
            # Handle other intents
            return {
                "success": True,
                "result": {
                    "source": "rasa",
                    "sender_id": sender_id,
                    "intent": intent,
                    "confidence_score": self._get_confidence_score(nlu),
                    "entities": entities,
                    "validation_performed": False,
                    "validation_mode": validate
                }
            }
            
        except Exception as e:
            logger.exception("Classification failed")
            return {
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
        except Exception as e:
            logger.exception("LLM validation failed")
            # Return original actions if LLM validation fails
            return actions