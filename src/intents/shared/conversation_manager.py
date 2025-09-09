# -*- coding: utf-8 -*-
"""
SharedConversationManager

A lightweight conversation manager shared by Rasa and LLM services.
Responsibilities:
- Manage per-user conversation history via SessionService
- Maintain and update simple slots (product, quantity, unit)
- Provide helpers to process a new message and return enhanced entities/slots
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging
from .models import Action

logger = logging.getLogger(__name__)


class SharedConversationManager:
    def __init__(self, session_client, sender_id: str):
        self.session_client = session_client
        self.sender_id = sender_id or "anonymous"

    # ------------------------------------------------------------
    # History helpers
    # ------------------------------------------------------------
    def get_conversation_history(self) -> List[Dict[str, Any]]:
        session = self.session_client.get_session(self.sender_id) or {}
        return session.get("history", []) or []

    def append_user_message(self, content: str) -> bool:
        if not content:
            return False
        return self.session_client.append_history(self.sender_id, "user", content)

    def append_assistant_message(self, content: str) -> bool:
        if not content:
            return False
        return self.session_client.append_history(self.sender_id, "assistant", content)

    # ------------------------------------------------------------
    # Slots helpers
    # ------------------------------------------------------------
    def get_slots(self) -> Dict[str, Any]:
        session = self.session_client.get_session(self.sender_id) or {}
        return session.get("slots", {}) or {}

    def update_slots(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        current = self.get_slots()
        # Allow None values to clear slots, but filter out missing keys
        for k, v in (updates or {}).items():
            if k in current or v is not None:  # Update if key exists or value is not None
                current[k] = v
        self.session_client.update_session(self.sender_id, {"slots": current})
        return current

    # ------------------------------------------------------------
    # Core processing
    # ------------------------------------------------------------
    def process_message(self, text: str, entities: Optional[List[Dict[str, Any]]], intent: Optional[str]) -> Dict[str, Any]:
        """
        Update slots based on entities and append user message to history.

        Returns a dict with enhanced entities (preserving multiple entities) and current slots.
        """
        entities = entities or []
        current_intent = (intent or "NONE").upper()
        logger.info("CM: process start sender=%s intent=%s text=%s", self.sender_id, current_intent, (text or "")[:200])
        logger.info("CM: incoming entities=%s", entities)

        # Check if intent changed and clear slots if so
        session = self.session_client.get_session(self.sender_id) or {}
        metadata = session.get("metadata", {})
        last_intent = metadata.get("last_intent")
        intent_changed = last_intent and last_intent != current_intent

        if intent_changed:
            logger.info("CM: intent changed from %s to %s, clearing slots", last_intent, current_intent)
            # Clear tracked slots
            self.session_client.update_session(self.sender_id, {
                "slots": {},
                "metadata": {"last_intent": current_intent}
            })
        else:
            # Update metadata with current intent
            self.session_client.update_session(self.sender_id, {
                "metadata": {"last_intent": current_intent}
            })

        # Append user message to history (best-effort)
        try:
            self.append_user_message(text or "")
        except Exception:
            pass

        # Group individual entities into complete entities
        # Rasa returns: [{"entity": "quantity", "value": "3"}, {"entity": "unit", "value": "kg"}, {"entity": "product", "value": "rice"}, ...]
        # We need to group them into: [{"product": "rice", "quantity": "3", "unit": "kg"}, {"product": "beans", "quantity": "2", "unit": "kg"}]

        # First, collect all individual entities
        individual_entities: Dict[str, List[Any]] = {}
        for ent in entities:
            name = ent.get("entity")
            value = ent.get("value")
            if name and value is not None:
                if name not in individual_entities:
                    individual_entities[name] = []
                individual_entities[name].append(value)
        logger.info("CM: individual_entities=%s", individual_entities)

        # Group entities by position/order
        normalized: List[Dict[str, Any]] = []

        # If we have individual entities, try to group them
        if individual_entities:
            # Use the longest list length among present entity types
            max_items = max((len(v) for v in individual_entities.values()), default=0)

            # Fallback: if there is no explicit product but there are other entities,
            # we will still build at least one item and fill product from current slots if available
            if max_items == 0 and any(len(v) > 0 for v in individual_entities.values()):
                max_items = 1

            # Get current slots AFTER potential clearing
            current_slots = self.get_slots() or {}

            for i in range(max_items):
                payload: Dict[str, Any] = {}
                for entity_type in ["product", "quantity", "unit"]:
                    values = individual_entities.get(entity_type) or []
                    if i < len(values):
                        payload[entity_type] = values[i]

                # If product is missing but we have one in slots, inherit it (only if intent didn't change)
                if payload.get("product") is None and current_slots.get("product") is not None and not intent_changed:
                    payload["product"] = current_slots.get("product")

                if payload:
                    normalized.append(payload)
        else:
            # Fallback: handle direct format entities
            for ent in entities:
                product = ent.get("product")
                quantity = ent.get("quantity")
                unit = ent.get("unit")

                payload: Dict[str, Any] = {}
                if product is not None:
                    payload["product"] = product
                if quantity is not None:
                    payload["quantity"] = quantity
                if unit is not None:
                    payload["unit"] = unit

                if payload:
                    normalized.append(payload)

        logger.info("CM: normalized_entities=%s", normalized)

        # Persist slots from the union of incoming entities
        slot_updates: Dict[str, Any] = {}
        for e in normalized:
            for k in ("product", "quantity", "unit"):
                if e.get(k) is not None:
                    slot_updates[k] = e[k]

        # If nothing was normalized but we did get some individual entities (e.g., only quantity/unit),
        # update slots directly from those (using the first seen values), and include product from current slots
        if not slot_updates and individual_entities:
            # Get current slots AFTER potential clearing
            current_slots = self.get_slots() or {}
            first_payload: Dict[str, Any] = {}
            for key in ("product", "quantity", "unit"):
                values = individual_entities.get(key) or []
                if values:
                    first_payload[key] = values[0]
            # Only inherit product from slots if intent didn't change
            if first_payload.get("product") is None and current_slots.get("product") is not None and not intent_changed:
                first_payload["product"] = current_slots.get("product")
            slot_updates.update({k: v for k, v in first_payload.items() if v is not None})

        slots = self.update_slots(slot_updates)
        logger.info("CM: slot_updates=%s slots_now=%s", slot_updates, slots)

        # If entities were provided, return them as-is (normalized)
        if normalized:
            enhanced_entities = normalized
        else:
            # Fallback to single combined entity from slots
            combined = {k: v for k, v in slots.items() if k in ("product", "quantity", "unit") and v is not None}
            enhanced_entities = [combined] if combined else []

        logger.info("CM: enhanced_entities=%s", enhanced_entities)

        return {
            "intent": current_intent,
            "entities": enhanced_entities,
            "slots": slots,
        }

    def process_actions(self, text: str, actions: List[Action]) -> Dict[str, Any]:
        """
        Process shopping actions and update slots accordingly.

        Args:
            text: User input text
            actions: List of parsed actions from modify_cart

        Returns:
            Dict with processed actions and updated slots
        """
        logger.info("CM: process_actions start sender=%s actions=%s", self.sender_id, [a.action for a in actions])

        # Append user message to history
        try:
            self.append_user_message(text or "")
        except Exception:
            pass

        # Process each action and update slots
        processed_actions: List[Action] = []
        slot_updates: Dict[str, Any] = {}

        for action in actions:
            # Inherit product from current slots if missing
            if not action.product:
                current = self.get_slots() or {}
                if current.get("product"):
                    action.product = current.get("product")

            processed_action = self._process_single_action(action)
            processed_actions.append(processed_action)

            # Update slots based on action type
            if action.action in ["add", "increase", "decrease", "set"]:
                current_slots = self.get_slots() or {}
                current_quantity_raw = current_slots.get("quantity")
                try:
                    current_quantity = float(current_quantity_raw) if current_quantity_raw is not None else 0.0
                except (ValueError, TypeError):
                    current_quantity = 0.0

                new_quantity: Optional[float] = None
                if action.action == "add":
                    # For add: set to provided quantity if present, else keep existing
                    if action.quantity is not None:
                        new_quantity = float(action.quantity)
                    else:
                        new_quantity = current_quantity
                elif action.action == "increase":
                    amount = float(action.quantity) if action.quantity is not None else 0.0
                    new_quantity = current_quantity + amount
                elif action.action == "decrease":
                    amount = float(action.quantity) if action.quantity is not None else 0.0
                    new_quantity = max(0.0, current_quantity - amount)
                elif action.action == "set":
                    if action.quantity is not None:
                        new_quantity = float(action.quantity)
                    else:
                        new_quantity = current_quantity

                if action.product is not None:
                    slot_updates["product"] = action.product
                if new_quantity is not None:
                    slot_updates["quantity"] = new_quantity
                if action.unit is not None:
                    slot_updates["unit"] = action.unit
            elif action.action == "remove":
                # Clear slots when removing
                if action.product and action.product == self.get_slots().get("product"):
                    slot_updates["product"] = None
                    slot_updates["quantity"] = None
                    slot_updates["unit"] = None
            elif action.action == "check":
                # For check actions, don't update slots, just return the action
                pass

        # Update slots
        if slot_updates:
            self.update_slots(slot_updates)

        current_slots = self.get_slots()
        logger.info("CM: processed_actions=%s slots=%s", [a.action for a in processed_actions], current_slots)

        return {
            "actions": processed_actions,
            "slots": current_slots,
        }

    def _process_single_action(self, action: Action) -> Action:
        """
        Process a single action, potentially enriching it with context.
        Currently a no-op that returns the action unchanged.
        """
        return action 
