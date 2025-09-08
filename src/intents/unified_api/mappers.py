"""
Response mappers for Unified API
"""
from intents.shared.models import IntentMeta, Entity


def _group_raw_rasa_entities(raw_entities):
    """Group flat Rasa entities into combined items.

    Expects a list of dicts like:
      [{"entity": "quantity", "value": "3"}, {"entity": "unit", "value": "kg"}, {"entity": "product", "value": "rice"}, ...]

    Returns a list of dicts like:
      [{"product": "rice", "quantity": 3, "unit": "kg"}, {"product": "beans", "quantity": 5, "unit": "kg"}]
    """
    if not raw_entities:
        return []

    buckets = {"product": [], "quantity": [], "unit": []}
    for e in raw_entities:
        name = e.get("entity")
        value = e.get("value")
        if name in buckets and value is not None:
            buckets[name].append(value)

    max_len = max((len(v) for v in buckets.values()), default=0)
    grouped = []
    for i in range(max_len):
        item = {}
        if i < len(buckets["product"]):
            item["product"] = buckets["product"][i]
        if i < len(buckets["quantity"]):
            item["quantity"] = buckets["quantity"][i]
        if i < len(buckets["unit"]):
            item["unit"] = buckets["unit"][i]
        if item:
            grouped.append(item)
    return grouped

def map_rasa_to_intent_meta(rasa_json) -> IntentMeta:
    """Map Rasa response to unified format.

    Prefers the conversation-managed fields (top-level 'entities'/'intent')
    if present; otherwise falls back to raw Rasa NLU fields.
    """
    nlu = rasa_json.get("nlu") or rasa_json  # support direct or wrapped

    # Confidence from raw Rasa intent
    raw_intent = (nlu.get("intent") or {})
    conf_score = raw_intent.get("confidence", 0.0)
    confidence = "high" if conf_score >= 0.7 else "medium" if conf_score >= 0.4 else "low"

    # Prefer processed intent from CM if available
    processed_intent = rasa_json.get("intent")
    if isinstance(processed_intent, str) and processed_intent:
        intent_name = processed_intent.upper()
    elif isinstance(processed_intent, dict):
        intent_name = (processed_intent.get("name") or "NONE").upper()
    else:
        intent_name = (raw_intent.get("name") or "NONE").upper()

    # Prefer processed entities from CM if available
    processed_entities = rasa_json.get("entities") or []
    ents = []
    if processed_entities:
        for e in processed_entities:
            ents.append(Entity(
                product=e.get("product"),
                quantity=e.get("quantity"),
                unit=e.get("unit")
            ))
    else:
        # Fallback: group raw Rasa entities
        grouped = _group_raw_rasa_entities(nlu.get("entities") or [])
        for e in grouped:
            ents.append(Entity(
                product=e.get("product"),
                quantity=e.get("quantity"),
                unit=e.get("unit")
            ))

    meta = IntentMeta(intent=intent_name, confidence=confidence, entities=ents)
    # Preserve numeric score when available (optional field)
    meta.confidence_score = conf_score
    return meta

def map_llm_to_intent_meta(llm_json) -> IntentMeta:
    """Map LLM response to unified format"""
    res = llm_json.get("result") or llm_json
    intent = (res.get("intent") or "NONE").upper()
    confidence = (res.get("confidence") or "low").lower()
    ents = [Entity(**e) for e in (res.get("entities") or [])]
    
    return IntentMeta(intent=intent, confidence=confidence, entities=ents)
