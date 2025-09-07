"""
Configuration for LLM service - extracted from llm.py
"""
from typing import Dict, Any, List

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
        "yam": "piece",       # example: yam sold per piece by default
        "egg": "piece",
        "eggs": "piece",
    }
}

# Constants / Intent schema
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
