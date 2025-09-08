# -*- coding: utf-8 -*-
"""
Local copy of SharedConversationManager used as a fallback when importing
from /app/shared fails inside the container due to encoding/path issues.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

class SharedConversationManager:
    def __init__(self, session_client, sender_id: str):
        self.session_client = session_client
        self.sender_id = sender_id or "anonymous"

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

    def get_slots(self) -> Dict[str, Any]:
        session = self.session_client.get_session(self.sender_id) or {}
        return session.get("slots", {}) or {}

    def update_slots(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        current = self.get_slots()
        current.update({k: v for k, v in (updates or {}).items() if v is not None})
        self.session_client.update_session(self.sender_id, {"slots": current})
        return current

    def process_message(self, text: str, entities: Optional[List[Dict[str, Any]]], intent: Optional[str]) -> Dict[str, Any]:
        entities = entities or []
        logger.info("CM(local): process start sender=%s intent=%s text=%s", self.sender_id, (intent or "NONE"), (text or "")[:200])
        logger.info("CM(local): incoming entities=%s", entities)

        try:
            self.append_user_message(text or "")
        except Exception:
            pass

        # Group individual entities (entity/value) into structured entities
        individual_entities: Dict[str, List[Any]] = {}
        for ent in entities:
            name = ent.get("entity")
            value = ent.get("value")
            if name and value is not None:
                individual_entities.setdefault(name, []).append(value)

        normalized: List[Dict[str, Any]] = []
        if individual_entities:
            max_items = len(individual_entities.get("product", []))
            for i in range(max_items):
                payload: Dict[str, Any] = {}
                for t in ("product", "quantity", "unit"):
                    vals = individual_entities.get(t)
                    if vals is not None and i < len(vals):
                        payload[t] = vals[i]
                if payload:
                    normalized.append(payload)
        else:
            # Direct {product, quantity, unit} payloads
            for ent in entities:
                payload: Dict[str, Any] = {}
                for t in ("product", "quantity", "unit"):
                    v = ent.get(t)
                    if v is not None:
                        payload[t] = v
                if payload:
                    normalized.append(payload)

        logger.info("CM(local): normalized_entities=%s", normalized)

        slot_updates: Dict[str, Any] = {}
        for e in normalized:
            for k in ("product", "quantity", "unit"):
                if e.get(k) is not None:
                    slot_updates[k] = e[k]
        slots = self.update_slots(slot_updates)
        logger.info("CM(local): slot_updates=%s slots_now=%s", slot_updates, slots)

        enhanced_entities = normalized if normalized else (
            [{k: v for k, v in slots.items() if k in ("product", "quantity", "unit") and v is not None}] if slots else []
        )
        logger.info("CM(local): enhanced_entities=%s", enhanced_entities)

        return {"intent": (intent or "NONE").upper(), "entities": enhanced_entities, "slots": slots}
