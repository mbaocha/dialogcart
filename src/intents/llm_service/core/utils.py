"""
Utility functions for LLM service - extracted from llm.py
"""
import re
from typing import List, Dict, Any, Tuple, Optional
from .config import UNIT_NORMALIZATION, REQUIRED_SLOTS
from .models import Entity, IntentAction

def normalize_unit(u: Optional[str]) -> Optional[str]:
    if not u or not isinstance(u, str):
        return None
    u = u.strip().lower()
    return UNIT_NORMALIZATION.get(u, u)  # leave untouched if unknown (we'll ask later)

def sanitize_entities(intents: List[IntentAction]) -> List[IntentAction]:
    """
    Make sure types are correct; drop/normalize anything suspicious.
    No 'invented' values added here—only cleaning.
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

def compute_missing_per_entity(intent: str, entities: List[Entity], config: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Returns missing slot labels per entity: e.g., ["quantity (rice)", "unit (beans)"].
    """
    required = REQUIRED_SLOTS.get(intent, [])
    # If AUTO_FILL_UNITS is enabled, treat unit as optional for ADD_TO_CART
    if config and config.get("AUTO_FILL_UNITS", False) and intent == "ADD_TO_CART":
        required = [s for s in required if s != "unit"]
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
    Only auto-fill 'product' when there's exactly one plausible recent candidate.
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
                # else leave None; we'll prompt
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
    Returns (updated_entities, notes_to_display)
    """
    notes: List[str] = []
    if not config.get("AUTO_FILL_UNITS", False):
        return entities, notes

    defaults: Dict[str, str] = config.get("DEFAULT_UNITS", {})
    filled: List[Entity] = []
    for ent in entities:
        d = ent.model_dump()
        if d.get("product") and not d.get("unit"):
            prod_key = d["product"].lower()
            default_unit = defaults.get(prod_key)
            if default_unit:
                d["unit"] = default_unit
                notes.append(f"ℹ️  Defaulted unit for {d['product']} → {default_unit}")
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
