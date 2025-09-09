from typing import List, Dict, Any, Optional
from .models import Entity, IntentMeta, ClassifyResponse, MultiClassifyResponse
from .utils import fill_missing_entities, fill_missing_units, fill_missing_quantities
import logging

logger = logging.getLogger(__name__)

class ConversationManager:
    def __init__(self):
        self.memory: Dict[str, List[Dict[str, Any]]] = {}
        self.pending: Dict[str, Any] = {}

    def get_memory(self, sender_id: str) -> List[Dict[str, Any]]:
        """Get conversation memory for a sender."""
        return self.memory.get(sender_id, [])

    def clear_memory(self, sender_id: str):
        """Clear conversation memory for a sender."""
        if sender_id in self.memory:
            del self.memory[sender_id]
        if sender_id in self.pending:
            del self.pending[sender_id]

    def process_message(self, text: str, nlu_result: Dict[str, Any], sender_id: str) -> MultiClassifyResponse:
        """Process a message and return classification result."""
        try:
            # Get or create memory for sender
            if sender_id not in self.memory:
                self.memory[sender_id] = []
            
            # Extract intents and entities from NLU result
            intents = nlu_result.get('intents', [])
            if not intents:
                return MultiClassifyResponse(source="llm", intents=[])
            
            # Process each intent
            processed_intents = []
            for intent_data in intents:
                entities = [Entity(**e) for e in intent_data.get('entities', [])]
                
                # Fill missing entities using conversation context
                filled_entities = self._fill_entities_with_context(entities, sender_id)
                
                # Create intent meta
                intent_meta = IntentMeta(
                    intent=intent_data.get('intent', 'NONE'),
                    confidence=intent_data.get('confidence', 'low'),
                    confidence_score=intent_data.get('confidence_score'),
                    entities=filled_entities
                )
                processed_intents.append(intent_meta)
            
            # Store in memory
            self.memory[sender_id].append({
                "intent": processed_intents[0].intent if processed_intents else "NONE",
                "entities": processed_intents[0].entities if processed_intents else [],
                "confidence": processed_intents[0].confidence if processed_intents else "low"
            })
            
            return MultiClassifyResponse(source="llm", intents=processed_intents)
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return MultiClassifyResponse(source="llm", intents=[])

    def _fill_entities_with_context(self, entities: List[Entity], sender_id: str) -> List[Entity]:
        """Fill missing entities using conversation context."""
        if not entities:
            return entities
        
        # Get recent memory for context
        recent_memory = self.memory.get(sender_id, [])[-3:]  # Last 3 messages
        
        # Fill missing entities
        filled = fill_missing_entities(entities, recent_memory)
        filled = fill_missing_units(filled, recent_memory)
        filled = fill_missing_quantities(filled, recent_memory)
        
        return filled

    def _show_entities(self, entities: List[Entity]) -> List[str]:
        lines = ["Entities:"]
        for e in entities:
            lines.append(f" - {e.dict()}")
        return lines

    # --- main handler ---
    def handle(self, user_input: str) -> List[str]:
        out: List[str] = []
        
        # Check if we have pending information to fill
        if self.pending.get("missing"):
            missing = self.pending["missing"]
            if "product" in missing and not any(e.product for e in self.pending["entity"]):
                out.append("What product would you like to add?")
                return out
            elif "quantity" in missing and not any(e.quantity for e in self.pending["entity"]):
                out.append("How much would you like?")
                return out
            elif "unit" in missing and not any(e.unit for e in self.pending["entity"]):
                out.append("What unit? (kg, pieces, etc.)")
                return out

            if not self.pending["missing"]:
                out.append("✅ Filled missing info: " + str(self.pending["entity"].dict()))
                self.memory.append({"intent": self.pending["intent"], "entities": [self.pending["entity"]]})
                self.history.append({"role": "assistant", "content": f"Completed {self.pending['intent']} for {self.pending['entity'].product}."})
                self.pending = {}
                return out

        # Process the input
        try:
            # This would normally call your NLU service
            # For now, we'll simulate a response
            response = self._simulate_nlu_response(user_input)
            
            if response:
                out.extend(self._show_entities(response.get("entities", [])))
                out.append(f"Intent: {response.get('intent', 'unknown')}")
                
                # Store in memory
                self.memory.append({
                    "intent": response.get("intent"),
                    "entities": response.get("entities", [])
                })
            else:
                out.append("I didn't understand that. Could you rephrase?")
                
        except Exception as e:
            logger.error(f"Error processing input: {e}")
            out.append("Sorry, I encountered an error processing your request.")
        
        return out

    def _simulate_nlu_response(self, text: str) -> Optional[Dict[str, Any]]:
        """Simulate NLU response for testing."""
        # This is a placeholder - in real implementation, this would call your NLU service
        return None