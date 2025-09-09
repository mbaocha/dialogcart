from typing import List, Dict, Any, Optional
from .models import Entity
import re

def fill_missing_entities(entities: List[Entity], memory: List[Dict[str, Any]]) -> List[Entity]:
    """Fill missing product entities using conversation memory."""
    if not entities or not memory:
        return entities
    
    # Extract recent products from memory
    candidates = []
    for msg in memory[-3:]:  # Last 3 messages
        for ent in msg.get("entities", []):
            if isinstance(ent, dict) and ent.get("product"):
                candidates.append(ent["product"])
            elif hasattr(ent, "product") and ent.product:
                candidates.append(ent.product)
    
    if not candidates:
        return entities
    
    # Check if user used pronouns (it, this, that, etc.)
    pronoun_used = any(word in " ".join([e.raw or "" for e in entities if e.raw]).lower() 
                      for word in ["it", "this", "that", "them", "those"])
    
    ambiguous = False
    filled = []
    for ent in entities:
        d = ent.dict()
        if d.get("product") is None:
            if len(candidates) == 1:
                d["product"] = candidates[0]  # safe
            else:
                if pronoun_used and len(candidates) > 1:
                    ambiguous = True
                # else leave None; we'll prompt
        filled.append(Entity(**d))
    
    if ambiguous:
        # Could add logic here to ask for clarification
        pass
    
    return filled

def fill_missing_units(entities: List[Entity], memory: List[Dict[str, Any]]) -> List[Entity]:
    """Fill missing unit entities using conversation memory and heuristics."""
    if not entities:
        return entities
    
    # Extract recent units from memory
    recent_units = []
    for msg in memory[-3:]:  # Last 3 messages
        for ent in msg.get("entities", []):
            if isinstance(ent, dict) and ent.get("unit"):
                recent_units.append(ent["unit"])
            elif hasattr(ent, "unit") and ent.unit:
                recent_units.append(ent.unit)
    
    # Common unit mappings
    unit_mappings = {
        "rice": "kg",
        "yam": "kg", 
        "beans": "kg",
        "garri": "kg",
        "flour": "kg",
        "sugar": "kg",
        "salt": "kg",
        "oil": "bottles",
        "milk": "bottles",
        "bread": "loaves",
        "eggs": "pieces",
        "tomatoes": "kg",
        "onions": "kg"
    }
    
    filled: List[Entity] = []
    for ent in entities:
        d = ent.dict()
        if d.get("product") and not d.get("unit"):
            prod_key = d["product"].lower()
            if prod_key in unit_mappings:
                d["unit"] = unit_mappings[prod_key]
            elif recent_units:
                d["unit"] = recent_units[-1]  # Use most recent unit
        filled.append(Entity(**d))
    
    return filled

def fill_missing_quantities(entities: List[Entity], memory: List[Dict[str, Any]]) -> List[Entity]:
    """Fill missing quantity entities using conversation memory."""
    if not entities:
        return entities
    
    # Extract recent quantities from memory
    recent_quantities = []
    for msg in memory[-3:]:  # Last 3 messages
        for ent in msg.get("entities", []):
            if isinstance(ent, dict) and ent.get("quantity"):
                recent_quantities.append(ent["quantity"])
            elif hasattr(ent, "quantity") and ent.quantity:
                recent_quantities.append(ent.quantity)
    
    filled: List[Entity] = []
    for ent in entities:
        d = ent.dict()
        if d.get("product"):
            # If quantity is missing, default to 0.0
            if d.get("quantity") is None:
                d["quantity"] = 0.0
        filled.append(Entity(**d))
    
    return filled

def extract_quantities_from_text(text: str) -> List[float]:
    """Extract numeric quantities from text."""
    # Pattern to match numbers (including decimals)
    pattern = r'\b\d+(?:\.\d+)?\b'
    matches = re.findall(pattern, text)
    return [float(match) for match in matches]

def extract_units_from_text(text: str) -> List[str]:
    """Extract unit words from text."""
    # Common unit patterns
    unit_patterns = [
        r'\b(?:kg|kilogram|kilo|kgs)\b',
        r'\b(?:g|gram|grams)\b', 
        r'\b(?:lb|lbs|pound|pounds)\b',
        r'\b(?:oz|ounce|ounces)\b',
        r'\b(?:piece|pieces|pcs?)\b',
        r'\b(?:bottle|bottles)\b',
        r'\b(?:packet|packets|packs?)\b',
        r'\b(?:loaf|loaves)\b',
        r'\b(?:dozen|dozens)\b',
        r'\b(?:carton|cartons|ctn|ctns)\b'
    ]
    
    units = []
    for pattern in unit_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        units.extend(matches)
    
    return units