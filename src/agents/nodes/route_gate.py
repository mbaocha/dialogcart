# agents/nodes/route_gate.py
from typing import Any, Dict, List
from ..tools import REQUIRED_SLOTS  # centralized configuration

# Intents that should NEVER trigger a tool call
NO_TOOL_INTENTS = {
    "NONE",                # no intent detected
    "GREET_USER",          # greetings
    "GOODBYE",             # farewells
    "SMALL_TALK",          # casual conversation
    "THANK_YOU",           # thanks
    # add more here if needed
}

# Confidence labels only (match what intent_parser emits)
CONF_ORDER = ["low", "medium", "high"]
THRESHOLD_LABEL = "medium"  # require at least this label to run tools

def _norm_entities(entities: Any) -> Dict[str, Any]:
    if not entities:
        return {}
    if isinstance(entities, dict):
        return entities
    if isinstance(entities, list) and entities and isinstance(entities[0], dict):
        return entities[0]
    return {}

def _confidence_meets_threshold(conf: Any) -> bool:
    """
    Compare label confidence without converting to numbers.
    Unknown/empty values are treated as 'low'.
    """
    label = str(conf or "").strip().lower()
    if label not in CONF_ORDER:
        label = "low"
    return CONF_ORDER.index(label) >= CONF_ORDER.index(THRESHOLD_LABEL)

def _missing_slots(intent: str, ent: Dict[str, Any]) -> List[str]:
    """
    Checks if the entities dict is missing any required slots for this intent.
    Uses REQUIRED_SLOTS mapping from tools.py.
    """
    need = REQUIRED_SLOTS.get(intent, [])
    missing: List[str] = []
    for slot in need:
        val = ent.get(slot) or ent.get(f"{slot}_name")
        if val is None or str(val).strip() == "":
            missing.append(slot)
    return missing

def needs_tool(intent_meta: Dict[str, Any]) -> bool:
    intent = (intent_meta.get("intent") or "NONE").upper()

    # Never route tools for blacklisted intents
    if intent in NO_TOOL_INTENTS:
        return False

    # Use label-only confidence comparison
    if not _confidence_meets_threshold(intent_meta.get("confidence")):
        return False

    ent = _norm_entities(intent_meta.get("entities"))
    return len(_missing_slots(intent, ent)) == 0
