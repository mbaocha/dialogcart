"""
Custom actions for slot management and memory
"""
from typing import Any, Text, Dict, List
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, AllSlotsReset, SessionStarted, ActionExecuted
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class ActionUpdateCartCount(Action):
    """Update cart item count based on actions"""
    
    def name(self) -> Text:
        return "action_update_cart_count"
    
    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        current_count = tracker.get_slot('total_cart_items') or 0.0
        intent = tracker.get_intent_of_latest_message()
        
        # Update count based on intent and entities
        entities = tracker.latest_message.get('entities', [])
        products = [e for e in entities if e.get('entity') == 'product']
        
        if intent == 'modify_cart':
            for entity in entities:
                if entity.get('entity') == 'verb':
                    verb = entity.get('value', '').lower()
                    if verb in ['add', 'put', 'include', 'buy']:
                        current_count += len(products) if products else 1
                    elif verb in ['remove', 'delete', 'take out']:
                        current_count = max(0, current_count - (len(products) if products else 1))
        
        return [SlotSet("total_cart_items", float(current_count))]

class ActionRememberProduct(Action):
    """Remember the last product mentioned and update shopping list"""
    
    def name(self) -> Text:
        return "action_remember_product"
    
    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        entities = tracker.latest_message.get('entities', [])
        products = [e.get('value') for e in entities if e.get('entity') == 'product']
        
        events = []
        if products:
            # Update last product added
            events.append(SlotSet("last_product_added", products[0]))
            
            # Update shopping list
            current_list = tracker.get_slot('shopping_list') or []
            for product in products:
                if product not in current_list:
                    current_list.append(product)
            events.append(SlotSet("shopping_list", current_list))
        
        return events

class ActionResetCart(Action):
    """Reset cart-related slots"""
    
    def name(self) -> Text:
        return "action_reset_cart"
    
    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        return [
            SlotSet("total_cart_items", 0.0),
            SlotSet("shopping_list", []),
            SlotSet("last_product_added", None),
            SlotSet("last_quantity", None),
            SlotSet("last_unit", None),
            SlotSet("cart_state", "empty")
        ]

class ActionUpdateCartState(Action):
    """Update cart state based on actions"""
    
    def name(self) -> Text:
        return "action_update_cart_state"
    
    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        intent = tracker.get_intent_of_latest_message()
        entities = tracker.latest_message.get('entities', [])
        
        events = []
        
        if intent == "cart_action":
            actions = [e.get("value") for e in entities if e.get("entity") == "action"]
            if actions:
                action = actions[0].lower()
                if action in ["clear", "empty", "remove all", "wipe"]:
                    events.append(SlotSet("cart_state", "empty"))
                elif action in ["show", "view", "display", "check", "list"]:
                    events.append(SlotSet("cart_state", "has_items"))
        
        return events

class ActionTrackInquiry(Action):
    """Track product inquiries and update inquiry-specific slots"""
    
    def name(self) -> Text:
        return "action_track_inquiry"
    
    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        entities = tracker.latest_message.get('entities', [])
        products = [e.get("value") for e in entities if e.get("entity") == "product"]
        actions = [e.get("value") for e in entities if e.get("entity") == "action"]
        
        events = []
        
        if products:
            events.append(SlotSet("last_inquired_product", products[0]))
            # Also update universal product memory
            events.append(SlotSet("last_mentioned_product", products[0]))
        
        if actions:
            events.append(SlotSet("last_inquiry_type", actions[0]))
        
        return events

class ActionUpdateOrderStatus(Action):
    """Update order tracking slots"""
    
    def name(self) -> Text:
        return "action_update_order_status"
    
    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        intent = tracker.get_intent_of_latest_message()
        
        events = []
        
        if intent == "track_order":
            # Extract order ID from text (this would need more sophisticated parsing)
            text = tracker.latest_message.get('text', '').lower()
            
            # Simple pattern matching for order IDs
            import re
            order_patterns = [
                r'order\s*#?(\w+)',
                r'track\s*(\w+)',
                r'status\s*of\s*(\w+)'
            ]
            
            for pattern in order_patterns:
                match = re.search(pattern, text)
                if match:
                    events.append(SlotSet("last_order_id", match.group(1)))
                    break
        
        return events
