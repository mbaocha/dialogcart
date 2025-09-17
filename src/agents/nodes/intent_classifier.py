# agents/nodes/intent_classifier.py
from typing import Any, Dict
from agents.state import AgentState
from agents.utils import enforce_agent_state


# agents/nodes/intent_classifier.py
import os
import re
from typing import Dict, Any, Optional
from agents.state import AgentState
from agents.utils import enforce_agent_state
from agents.tools import SAFE_PREV_INTENTS
from agents.config import INTENTS_API_URL

try:
    import httpx  # type: ignore
except Exception:  # pragma: no cover
    httpx = None  # type: ignore
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


def _classify_via_api(user_text: str, sender_id: str) -> Dict[str, Any]:
    """
    Call external intents API and return a normalized dict: {intent, confidence, entities}.
    Tolerates different response shapes; returns NONE/low on failure.
    """
    # Prefer config value if set, else fallback to env, else default
    url = (INTENTS_API_URL or os.getenv("AGENT_INTENTS_URL") or "http://localhost:9000/classify")
    payload = {"text": user_text, "sender_id": sender_id}
    headers = {"Content-Type": "application/json"}

    # Default result
    result: Dict[str, Any] = {"intent": "NONE", "confidence": "low", "entities": []}

    try:
        print(f"[DEBUG] intents_api -> url={url} sender_id={sender_id} text='{(user_text or '')[:120]}'")
        if httpx is None:
            # Fallback to stdlib if httpx not available
            import json, urllib.request  # type: ignore
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=float(os.getenv("AGENT_INTENTS_TIMEOUT", "8.0"))) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
                print(f"[DEBUG] intents_api <- urllib status=200 body={str(data)[:500]}")
        else:
            timeout = float(os.getenv("AGENT_INTENTS_TIMEOUT", "8.0"))
            with httpx.Client(timeout=timeout) as client:
                r = client.post(url, json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
                print(f"[DEBUG] intents_api <- httpx status={r.status_code} body={str(data)[:500]}")

        # Normalize: handle flat, data, or result shapes
        meta = data if isinstance(data, dict) else {}
        if "data" in meta and isinstance(meta["data"], dict):
            meta = meta["data"]
        if "result" in meta and isinstance(meta["result"], dict):
            meta = meta["result"]

        # Extract intent and confidence from various keys
        raw_intent = meta.get("intent") or meta.get("label") or "NONE"
        conf = meta.get("confidence") or meta.get("confidence_score") or meta.get("score") or "low"
        if isinstance(conf, (int, float)):
            conf = "high" if conf >= 0.8 else ("medium" if conf >= 0.5 else "low")

        # Prefer provided entities; else synthesize from actions if present
        entities = meta.get("entities")
        if not isinstance(entities, list):
            entities = []
        actions = meta.get("actions")
        if not entities and isinstance(actions, list) and actions:
            # Use the first action to build entities compatible with tool_args_builder
            a0 = actions[0] or {}
            product = a0.get("product") or a0.get("item")
            qty = a0.get("quantity")
            unit = a0.get("unit")
            if product is not None:
                entities.append({"product": product})
            if qty is not None:
                entities.append({"quantity": qty})
            if unit is not None:
                entities.append({"unit": unit})

            # Map action verb to internal intent if needed
            act = (a0.get("action") or "").lower()
            intent_map = {
                "add": "ADD_TO_CART",
                "remove": "REMOVE_FROM_CART",
                "delete": "REMOVE_FROM_CART",
                "update": "UPDATE_CART_QUANTITY",
            }
            if raw_intent.lower() in {"modify_cart", "cart_modify", "cart_action"} and act in intent_map:
                raw_intent = intent_map[act]

            # Handle view/show cart actions coming as cart_action + action=catalog/view/show
            if raw_intent.lower() in {"cart_action", "cart", "cart_intent"}:
                if act in {"catalog", "view", "show"}:
                    raw_intent = "VIEW_CART"

        # Additional direct mappings
        direct_map = {
            "view_cart": "VIEW_CART",
            "show_products": "SHOW_PRODUCT_LIST",
            "list_products": "SHOW_PRODUCT_LIST",
            "check_product": "CHECK_PRODUCT_EXISTENCE",
        }
        internal_intent = direct_map.get(raw_intent.lower(), raw_intent).upper()

        result.update({"intent": internal_intent, "confidence": str(conf).lower(), "entities": entities})
    except Exception as e:
        # Surface failure to caller so the app can route accordingly (fail fast)
        print(f"[DEBUG] intents_api !! error: {type(e).__name__}: {e}")
        raise

    return result

@enforce_agent_state
def intent_classifier(state: AgentState) -> AgentState:
    user_text = (state.user_input or "").strip()
    if not user_text:
        return state

    prev_intent = (getattr(state, "intent_meta", {}) or {}).get("intent")

    # Classify via external API
    try:
        base = _classify_via_api(user_text, getattr(state, "customer_id", "unknown"))
    except Exception as e:
        # Hard fail on communication issues
        raise RuntimeError(f"Intent API call failed: {type(e).__name__}: {e}")
    intent = (base.get("intent") or "NONE").upper()
    confidence = (base.get("confidence") or "low").lower()
    entities: Dict[str, Any] = base.get("entities") or []

    # If the API is unsure and the text looks like a follow-up, borrow previous label only
    if (intent == "NONE" or CONF_RANK.get(confidence, 0) == 0) and _should_fallback_to_prev(user_text, prev_intent):
        intent = (prev_intent or "NONE").upper()
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
