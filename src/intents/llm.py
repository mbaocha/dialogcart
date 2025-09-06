"""
Grocery Assistant — Simple, Reliable NLU + Deterministic Dialog
(with configurable default-unit auto-fill and optional default quantity=0)

Features:
- LLM-only extraction (multi-intent + entities) using structured output
- Deterministic validation (no hallucinated slots accepted)
- Per-entity missing-slot detection
- Tiny pending-followup state (only when needed)
- Safe product memory fill (only if 1 clear recent candidate)
- Pronoun ambiguity guard (“add it” with multiple candidates -> ask)
- Configurable default unit auto-fill (per product), with user-facing note
- NEW: Optional default quantity=0; when enabled, also accepts default unit
       regardless of the AUTO_FILL_UNITS flag

Requires:
  pip install langchain-openai pydantic
  export OPENAI_API_KEY=...

Run:
  python app.py
"""

from typing import List, Optional, Dict, Literal, Any, Tuple
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
import re

# =========================
# Config
# =========================

CONFIG = {
    # Toggle default-unit auto-fill
    "AUTO_FILL_UNITS": True,

    # NEW: Toggle default quantity=0 when quantity is missing
    # If True, we also accept/apply DEFAULT_UNITS for that entity
    # even if AUTO_FILL_UNITS is False.
    "AUTO_FILL_QUANTITY_ZERO": True,

    # Per-product default units (lowercase product keys)
    "DEFAULT_UNITS": {
        "rice": "kg",
        "beans": "kg",
        "tomatoes": "kg",
        "onions": "kg",
        "yam": "piece",       # example: yam sold per piece by default
        "egg": "piece",
        "eggs": "piece",
    }
}

# =========================
# Constants / Intent schema
# =========================

INTENTS = [
    "SHOW_PRODUCT_LIST",
    "VIEW_CART",
    "ADD_TO_CART",
    "REMOVE_FROM_CART",
    "CLEAR_CART",
    "CHECK_PRODUCT_EXISTENCE",
    "RESTORE_CART",
    "UPDATE_CART_QUANTITY",
    "NONE"
]

# Required slots PER INTENT (per entity)
REQUIRED_SLOTS: Dict[str, List[str]] = {
    "ADD_TO_CART": ["product", "quantity", "unit"],
    "REMOVE_FROM_CART": ["product"],
    "CHECK_PRODUCT_EXISTENCE": ["product"],
    "UPDATE_CART_QUANTITY": ["product", "quantity"],
}

# Optional: whitelist of units for minimal normalization (keep tiny & safe)
UNIT_NORMALIZATION = {
    "kg": "kg", "kgs": "kg",
    "g": "g", "grams": "g",
    "lb": "lb", "lbs": "lb",
    "piece": "piece", "pieces": "piece", "pc": "piece", "pcs": "piece",
    "bag": "bag", "bags": "bag",
    "box": "box", "boxes": "box",
}


# ==========
# Data models
# ==========

class Entity(BaseModel):
    product: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    raw: Optional[str] = None

class IntentAction(BaseModel):
    intent: Literal[
        "SHOW_PRODUCT_LIST", "VIEW_CART", "ADD_TO_CART", "REMOVE_FROM_CART",
        "CLEAR_CART", "CHECK_PRODUCT_EXISTENCE", "RESTORE_CART",
        "UPDATE_CART_QUANTITY", "NONE"
    ]
    confidence: Literal["high", "medium", "low"]
    reasoning: Optional[str] = None
    entities: List[Entity] = Field(default_factory=list)

class MultiIntentResult(BaseModel):
    intents: List[IntentAction]

# Follow-up narrow extraction (latest message only)
class FollowUpSlots(BaseModel):
    product: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None


# =================
# Prompt builders
# =================

def system_prompt() -> str:
    intents_list = "\n- " + "\n- ".join(INTENTS)
    return f"""
You are an intent classifier and slot extractor for a grocery shopping assistant.

Identify ALL intents present in the user's message (from this list):{intents_list}

For each intent, extract ONLY explicitly mentioned entities: product, quantity, unit.
❌ Do NOT guess or infer missing info from context or prior turns.
✅ If a field isn't clearly present, set it to null.

Return ONLY valid JSON in this exact shape:
{{
  "intents": [
    {{
      "intent": "ADD_TO_CART",
      "confidence": "high",
      "reasoning": "...",
      "entities": [
        {{
          "product": "rice",
          "quantity": 2,
          "unit": "kg",
          "raw": "2kg rice"
        }}
      ]
    }}
  ]
}}

If the user message is unclear, respond with exactly one intent: "NONE" with "low" confidence.
"""

def followup_prompt(target_product: str, missing: List[str]) -> str:
    needs = ", ".join(missing)
    return f"""
You are extracting ONLY the missing slot values from the user's LATEST message.

Current target product (may be empty when 'product' is missing): "{target_product}"
Missing fields now: {needs}

Rules:
- Consider ONLY the latest user message (ignore earlier turns).
- Do NOT guess. If a field isn't explicitly present, return null.
- product should be the exact product phrase mentioned (short noun phrase).
- quantity must be numeric if present.
- unit must be a short unit like "kg", "g", "lb", "piece", "bag", "box".

Respond ONLY in JSON:
{{
  "product": <string or null>,
  "quantity": <number or null>,
  "unit": <string or null>
}}
"""


# ==============
# NLU service
# ==============

class NLUService:
    def __init__(self, model: str = "gpt-4o"):
        self.model = model

    def classify(self, history: List[Dict[str, str]]) -> MultiIntentResult:
        """History-aware multi-intent + entities extraction (LLM-only)."""
        llm = ChatOpenAI(model=self.model, temperature=0).with_structured_output(MultiIntentResult)
        messages = [{"role": "system", "content": system_prompt()}] + history
        return llm.invoke(messages)

    def extract_followup(self, latest_user_msg: str, product: str, missing: List[str]) -> FollowUpSlots:
        """Narrow extractor for the latest message only—no history used."""
        llm = ChatOpenAI(model=self.model, temperature=0).with_structured_output(FollowUpSlots)
        messages = [
            {"role": "system", "content": followup_prompt(product, missing)},
            {"role": "user", "content": latest_user_msg}
        ]
        return llm.invoke(messages)


# ======================
# Deterministic utilities
# ======================

def normalize_unit(u: Optional[str]) -> Optional[str]:
    if not u or not isinstance(u, str):
        return None
    u = u.strip().lower()
    return UNIT_NORMALIZATION.get(u, u)  # leave untouched if unknown (we’ll ask later)

def sanitize_entities(intents: List[IntentAction]) -> List[IntentAction]:
    """
    Make sure types are correct; drop/normalize anything suspicious.
    No ‘invented’ values added here—only cleaning.
    """
    for ib in intents:
        cleaned = []
        for e in ib.entities:
            prod = e.product if isinstance(e.product, str) else None
            qty = e.quantity if isinstance(e.quantity, (int, float)) else None
            unit = normalize_unit(e.unit) if isinstance(e.unit, str) else None
            cleaned.append(Entity(product=prod, quantity=qty, unit=unit, raw=e.raw))
        ib.entities = cleaned
    return intents

def compute_missing_per_entity(intent: str, entities: List[Entity]) -> List[str]:
    """
    Returns missing slot labels per entity: e.g., ["quantity (rice)", "unit (beans)"].
    """
    required = REQUIRED_SLOTS.get(intent, [])
    if not required or not entities:
        return []
    out: List[str] = []
    for ent in entities:
        name = ent.product or "item"
        for slot in required:
            if getattr(ent, slot, None) in (None, "", []):
                out.append(f"{slot} ({name})")
    return out

def looks_like_followup(msg: str) -> bool:
    """Heuristic: short, fragmentary answers likely responding to a prompt."""
    m = msg.strip().lower()
    if not m:
        return True
    if m in {"yes", "no", "same", "it", "the same"}:
        return True
    # Pure numeric or "num unit" (e.g., "4", "4kg", "4 kg")
    if re.fullmatch(r"\d+(\.\d+)?(\s*[a-zA-Z]+)?", m):
        return True
    # Short fragments (<= 3 tokens) without obvious action verbs
    tokens = m.split()
    if len(tokens) <= 3 and not any(v in m for v in ["add", "remove", "sell", "have", "check", "clear", "update", "cart"]):
        return True
    return False

def recent_product_candidates(memory: List[Dict[str, Any]], window: int = 6) -> List[str]:
    """Most recent distinct product names seen in memory (bounded window)."""
    seen: List[str] = []
    for prev in reversed(memory):
        for ent in prev.get("entities", []):
            p = getattr(ent, "product", None)
            if p and p not in seen:
                seen.append(p)
            if len(seen) >= window:
                break
        if len(seen) >= window:
            break
    return seen

def maybe_fill_product_from_memory(
    entities: List[Entity],
    memory: List[Dict[str, Any]],
    user_input: str
) -> Tuple[List[Entity], bool]:
    """
    Only auto-fill 'product' when there’s exactly one plausible recent candidate.
    If user used a pronoun ('it', 'that', 'same') and there are multiple candidates, flag ambiguity.
    """
    if not entities:
        return [], False

    ambiguous = False
    candidates = recent_product_candidates(memory)
    lower = f" {user_input.lower()} "
    pronoun_used = any(token in lower for token in [" it ", " that ", " same "])

    filled = []
    for ent in entities:
        d = ent.model_dump()
        if d.get("product") is None:
            if len(candidates) == 1:
                d["product"] = candidates[0]  # safe
            else:
                if pronoun_used and len(candidates) > 1:
                    ambiguous = True
                # else leave None; we’ll prompt
        filled.append(Entity(**d))

    return filled, ambiguous

def maybe_fill_default_unit(
    entities: List[Entity],
    config: Dict[str, Any]
) -> Tuple[List[Entity], List[str]]:
    """
    Auto-fill unit per entity if:
      - AUTO_FILL_UNITS=True
      - entity.product present
      - entity.unit missing
      - product has a configured default unit

    If AUTO_FILL_UNITS=True but no default is found,
    force unit=None to avoid stale or hallucinated values.
    """
    notes: List[str] = []
    if not config.get("AUTO_FILL_UNITS", False):
        return entities, notes

    defaults: Dict[str, str] = config.get("DEFAULT_UNITS", {})
    filled: List[Entity] = []

    for ent in entities:
        d = ent.model_dump()
        if d.get("product") and not d.get("unit"):
            key = canonical_default_key(d["product"], defaults)
            if key:
                default_unit = defaults.get(key)
                if default_unit:
                    d["unit"] = default_unit
                    notes.append(f"ℹ️  Defaulted unit for {d['product']} → {default_unit}")
                else:
                    d["unit"] = None  # explicit None if no default found
                    notes.append(f"ℹ️  No default unit for {d['product']} → left as None")
            else:
                d["unit"] = None  # explicit None if product not in defaults
                notes.append(f"ℹ️  No default unit for {d['product']} → left as None")
        filled.append(Entity(**d))

    return filled, notes


# NEW: default quantity=0 (and accept default unit regardless of AUTO_FILL_UNITS)
def maybe_fill_default_qty0_and_unit(
    entities: List[Entity],
    config: Dict[str, Any]
) -> Tuple[List[Entity], List[str]]:
    """
    If AUTO_FILL_QUANTITY_ZERO is True:
      - For any entity with missing quantity, set quantity to 0.0
      - If unit is missing and DEFAULT_UNITS has a mapping for the product, set that unit
        (this applies even if AUTO_FILL_UNITS is False)
    Returns (updated_entities, notes_to_display)
    """
    notes: List[str] = []
    if not config.get("AUTO_FILL_QUANTITY_ZERO", False):
        return entities, notes

    defaults: Dict[str, str] = config.get("DEFAULT_UNITS", {})
    filled: List[Entity] = []
    for ent in entities:
        d = ent.model_dump()
        if d.get("product"):
            # If quantity is missing, default to 0.0
            if d.get("quantity") is None:
                d["quantity"] = 0.0
                notes.append(f"ℹ️  Defaulted quantity for {d['product']} → 0")
                # Accept/apply default unit regardless of AUTO_FILL_UNITS
                if not d.get("unit"):
                    default_unit = defaults.get(d["product"].lower())
                    if default_unit:
                        d["unit"] = default_unit
                        notes.append(f"ℹ️  Applied default unit for {d['product']} → {default_unit}")
        filled.append(Entity(**d))
    return filled, notes


# ======================
# Conversation management
# ======================

class ConversationManager:
    """
    Small, focused dialog manager:
      - Keeps memory of resolved intents/entities (for safe product recall)
      - Maintains LLM history (for NLU only)
      - Tracks one pending follow-up (slots still needed for a single entity)
    """
    def __init__(self, nlu: NLUService, config: Dict[str, Any]):
        self.nlu = nlu
        self.config = config
        self.history: List[Dict[str, str]] = []
        self.memory: List[Dict[str, Any]] = []
        self.pending: Optional[Dict[str, Any]] = None  # {intent, product, entity, missing}

    # --- rendering helpers (CLI) ---
    def _hdr(self, i: int, ib: IntentAction) -> List[str]:
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

        # If we’re waiting for follow-up, try to complete missing slots first
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
                out.append("⚠️  I’m not sure which product you meant by 'it/that'.")
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

            # Compute missing per-entity (deterministic)
            missing_labels = compute_missing_per_entity(ib.intent, ib.entities)

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


# ============
# Minimal CLI
# ============

def main():
    print("🛒 Grocery Assistant — Simple NLU + Deterministic Dialog")
    print("Type 'exit' to quit.\n")

    manager = ConversationManager(NLUService(), CONFIG)

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Goodbye!")
            break

        if user_input.lower() in {"exit", "quit"}:
            print("👋 Goodbye!")
            break

        lines = manager.handle(user_input)
        for line in lines:
            print(line)
        print("\n" + "-" * 40 + "\n")


if __name__ == "__main__":
    main()
