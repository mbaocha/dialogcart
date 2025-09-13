"""
Simple in-memory slot storage for immediate testing
This provides slot memory without requiring full Rasa Core setup
"""
from typing import Dict, Any
import threading

class SimpleSlotMemory:
    """Simple in-memory slot storage"""
    
    def __init__(self):
        self._memory = {}  # sender_id -> slots
        self._lock = threading.Lock()
    
    def get_slots(self, sender_id: str) -> Dict[str, Any]:
        """Get slots for a sender"""
        with self._lock:
            return self._memory.get(sender_id, {})
    
    def update_slots(self, sender_id: str, intent: str, entities: list, text: str) -> Dict[str, Any]:
        """Update slots based on message"""
        with self._lock:
            if sender_id not in self._memory:
                self._memory[sender_id] = {}
            
            slots = self._memory[sender_id]
            
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
    
    def _is_contextual_update(self, text: str) -> bool:
        """Check if this is a contextual update"""
        contextual_phrases = ["add it", "make it", "change it", "update it", "modify it", "set it"]
        text_lower = text.lower()
        return any(phrase in text_lower for phrase in contextual_phrases)
    
    def reset_sender(self, sender_id: str):
        """Reset slots for a sender"""
        with self._lock:
            if sender_id in self._memory:
                del self._memory[sender_id]

# Global instance
slot_memory = SimpleSlotMemory()
