# agents/nodes/intent_classifier.py
from typing import Any, Dict
from agents.state import AgentState
from agents.utils import enforce_agent_state
from agents.intent import classify_intent  # regex + semantic


# agents/nodes/intent_classifier.py
import re
from typing import Dict, Any, Optional
from agents.state import AgentState
from agents.utils import enforce_agent_state
from agents.intent import classify_intent  # your regex/LLM hybrid
from agents.tools import SAFE_PREV_INTENTS
CONF_RANK = {"low": 0, "medium": 1, "high": 2}

# Looks like a continuation (short, referential, or numeric tweak)
FOLLOWUP_RE = re.compile(
    r"^(yes|no|y(es)?p|nope|same|more|another|again|that|this|it|them|those|"
    r"x?\d+([a-z]+)?|\d+\s*[a-z]+|\d+)$",
    re.I,
)

# If any of these appear, DO NOT borrow the previous intent
HARD_KEYWORDS = {"cart", "basket", "checkout", "address", "order", "payment", "list", "product", "products"}

def _should_fallback_to_prev(user_text: str, prev_intent: Optional[str]) -> bool:
    if not prev_intent or prev_intent not in SAFE_PREV_INTENTS:
        return False
    t = (user_text or "").strip().lower()
    # must look like a continuation AND must not contain hard keywords
    return (len(t.split()) <= 3) and bool(FOLLOWUP_RE.match(t)) and not any(k in t for k in HARD_KEYWORDS)

@enforce_agent_state
def intent_classifier(state: AgentState) -> AgentState:
    user_text = (state.user_input or "").strip()
    if not user_text:
        return state

    prev_intent = (getattr(state, "intent_meta", {}) or {}).get("intent")

    # 1) Always classify fresh with NO hint
    base = classify_intent(user_text)
    intent = (base.get("intent") or "NONE").upper()
    confidence = (base.get("confidence") or "low").lower()
    entities: Dict[str, Any] = base.get("entities") or []

    # 2) If we failed (NONE/low), and it looks like a follow-up, borrow prev intent label
    if (intent == "NONE" or CONF_RANK.get(confidence, 0) == 0) and _should_fallback_to_prev(user_text, prev_intent):
        # Re-run extraction with a hint so we can parse things like "x2", "2kg", etc.
        hinted = classify_intent(user_text, prev_intent)
        intent = prev_intent  # force the label to previous
        # keep hinted entities if any, else fallback to empty list
        entities = hinted.get("entities") or []
        # raise confidence a notch (but not "high")
        confidence = "medium"

    intent_meta = {"intent": intent, "confidence": confidence, "entities": entities}
    print(f"[DEBUG] intent_classifier -> intent_meta: {intent_meta}")

    return state.model_copy(update={
        "intent": intent,
        "intent_confidence": confidence,
        "entities": entities,
        "intent_meta": intent_meta,
    })




SAFE_DIRECT_INTENTS = {
    "SHOW_PRODUCT_LIST",
    "VIEW_CART",
    "ADD_TO_CART",
    "REMOVE_FROM_CART",
    "CLEAR_CART",
    "CHECK_PRODUCT_EXISTENCE",
}

MIN_CONFIDENCE_FOR_DIRECT = {"low": 0, "medium": 1, "high": 2}
DIRECT_THRESHOLD = 1  # >= "medium" goes direct

@enforce_agent_state
def intent_router(state: AgentState) -> str:
    """
    Returns edge label for LangGraph conditional edges:
    - "direct_tool"
    - "ask_llm"
    """
    intent_meta = getattr(state, "intent_meta", {}) or {}
    intent = (intent_meta.get("intent") or "NONE").upper()
    conf = (intent_meta.get("confidence") or "low").lower()

    if intent in SAFE_DIRECT_INTENTS and MIN_CONFIDENCE_FOR_DIRECT.get(conf, 0) >= DIRECT_THRESHOLD:
        return "direct_tool"

    # fallback to LLM for unsure / unsafe
    return "ask_llm"
