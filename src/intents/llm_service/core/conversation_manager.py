"""
Conversation Manager - extracted from llm.py
"""
from typing import List, Dict, Any, Optional
from .nlu_service import NLUService
from .models import Entity
from .utils import (
    sanitize_entities, compute_missing_per_entity, looks_like_followup,
    maybe_fill_product_from_memory, maybe_fill_default_unit, maybe_fill_default_qty0_and_unit,
    recent_product_candidates
)
from .config import REQUIRED_SLOTS

class ConversationManager:
    """
    Small, focused dialog manager:
      - Keeps memory of resolved intents/entities (for safe product recall)
      - Maintains LLM history (for NLU only)
      - Tracks one pending follow-up (slots still needed for a single entity)
    """
    def __init__(self, nlu: NLUService, config: Dict[str, Any], user_id: str = None):
        self.user_id = user_id
        self.nlu = nlu
        self.config = config
        self.history: List[Dict[str, str]] = []
        self.memory: List[Dict[str, Any]] = []
        self.pending: Optional[Dict[str, Any]] = None

    # --- rendering helpers (CLI) ---
    def _hdr(self, i: int, ib) -> List[str]:
        lines = [f"\n🤖 Intent #{i}: {ib.intent} ({ib.confidence})"]
        if ib.reasoning:
            lines.append(f"Reasoning: {ib.reasoning}")
        return lines

    def _show_entities(self, entities: List[Entity]) -> List[str]:
        lines = ["Entities:"]
        for e in entities:
            lines.append(f" - {e.model_dump()}")
        return lines

    # --- main handler ---
    def handle(self, user_input: str) -> List[str]:
        out: List[str] = []

        # If we're waiting for follow-up, try to complete missing slots first
        if self.pending and looks_like_followup(user_input):
            fu = self.nlu.extract_followup(user_input, self.pending["product"], self.pending["missing"])

            if "product" in self.pending["missing"] and fu.product:
                self.pending["entity"].product = fu.product
                self.pending["product"] = fu.product
                self.pending["missing"].remove("product")

            if "quantity" in self.pending["missing"] and fu.quantity is not None:
                self.pending["entity"].quantity = fu.quantity
                self.pending["missing"].remove("quantity")

            if "unit" in self.pending["missing"] and fu.unit:
                self.pending["entity"].unit = fu.unit
                self.pending["missing"].remove("unit")

            if not self.pending["missing"]:
                out.append("✅ Filled missing info: " + str(self.pending["entity"].model_dump()))
                self.memory.append({"intent": self.pending["intent"], "entities": [self.pending["entity"]]})
                self.history.append({"role": "assistant", "content": f"Completed {self.pending['intent']} for {self.pending['entity'].product}."})
                self.pending = None
            else:
                out.append(f"⚠️ Still missing: {', '.join(self.pending['missing'])}")
                out.append(f"💬 Follow-up: What is the {', '.join(self.pending['missing'])}?")
            return out

        # Normal classification path (LLM sees history)
        self.history.append({"role": "user", "content": user_input})
        result = self.nlu.classify(self.history)

        # Cleanse types/units (no hallucinated values added)
        result.intents = sanitize_entities(result.intents)

        # Process each intent
        for i, ib in enumerate(result.intents, start=1):
            out += self._hdr(i, ib)

            # Try safe product recall from memory, detect pronoun ambiguity
            ib.entities, ambiguous = maybe_fill_product_from_memory(ib.entities, self.memory, user_input)
            if ambiguous:
                options = recent_product_candidates(self.memory)
                hint = ", ".join(options[:5]) if options else "?"
                out.append("⚠️  I'm not sure which product you meant by 'it/that'.")
                out.append(f"💬 Follow-up: Which product? (e.g., {hint})")
                # Set a pending that asks ONLY for product for this entity
                target = ib.entities[0] if ib.entities else Entity()
                self.pending = {
                    "intent": ib.intent,
                    "product": "",
                    "entity": target,
                    "missing": ["product"]
                }
                continue

            # NEW: If quantity=0 default is enabled, apply it first (and apply default unit regardless of AUTO_FILL_UNITS)
            ib.entities, qty_notes = maybe_fill_default_qty0_and_unit(ib.entities, self.config)
            out += qty_notes  # display any defaulting notes

            # Then apply normal default-unit auto-fill (if enabled) for any remaining items
            ib.entities, unit_notes = maybe_fill_default_unit(ib.entities, self.config)
            out += unit_notes  # display any defaulting notes

            out += self._show_entities(ib.entities)

            # Compute missing per-entity (deterministic, with config-aware unit handling)
            missing_labels = compute_missing_per_entity(ib.intent, ib.entities, self.config)

            if missing_labels:
                # Build clearer follow-up (first incomplete entity for MVP)
                first = missing_labels[0]
                slot, rest = first.split(" (", 1)
                product_name = rest[:-1]  # drop trailing ')'

                questions = []
                for m in missing_labels:
                    s, r = m.split(" (", 1)
                    p = r[:-1]
                    questions.append(f"{s} for {p}?")
                out.append(f"⚠️  Missing slots: {', '.join(missing_labels)}")
                out.append("💬 Follow-up: " + " ".join(questions))

                # Target the first incomplete entity for follow-up
                target = next((e for e in ib.entities if (e.product or "item") == product_name), Entity())
                # Extract only the slots missing for THIS target entity
                missing_for_target = []
                for s in REQUIRED_SLOTS.get(ib.intent, []):
                    if getattr(target, s, None) in (None, "", []):
                        missing_for_target.append(s)

                self.pending = {
                    "intent": ib.intent,
                    "product": target.product or "",
                    "entity": target,
                    "missing": missing_for_target or ["product"]  # ensure at least one
                }
            else:
                out.append("✅ All required information received. You can now proceed with the action.")
                self.memory.append({"intent": ib.intent, "entities": ib.entities})

        # Summarize to history (helps next LLM turn)
        summary = "; ".join(f"{i.intent} ({i.confidence})" for i in result.intents)
        self.history.append({"role": "assistant", "content": f"Detected: {summary}"})
        return out
