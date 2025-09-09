from typing import List
from .models import IntentMeta, Entity
from .confidence import score_to_bucket
import re


def _split_multi_intent(intent_name: str) -> List[str]:
    """Split Rasa multi-intent format (A+B) into individual intents."""
    if "+" in intent_name:
        return [intent.strip().upper() for intent in intent_name.split("+")]
    return [intent_name.upper()]


def _group_raw_rasa_entities(raw_entities):
    """Group flat Rasa entities into combined items.
    
    Expects: [{"entity": "quantity", "value": "3"}, {"entity": "unit", "value": "kg"}, {"entity": "product", "value": "rice"}, ...]
    Returns: [{"product": "rice", "quantity": 3, "unit": "kg"}, {"product": "beans", "quantity": 5, "unit": "kg"}]
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
            qty_raw = buckets["quantity"][i]
            try:
                if isinstance(qty_raw, str):
                    m = re.search(r"[-+]?\d*\.?\d+", qty_raw)
                    if m:
                        item["quantity"] = float(m.group())
                else:
                    item["quantity"] = float(qty_raw)
            except Exception:
                pass
        if i < len(buckets["unit"]):
            item["unit"] = buckets["unit"][i]
        if item:
            grouped.append(item)
    return grouped


def map_rasa_to_intent_meta(rasa_json) -> IntentMeta:
    """Map Rasa response to unified format.
    
    Prefers processed entities/intent from conversation manager if available;
    otherwise falls back to raw Rasa NLU fields.
    """
    nlu = rasa_json.get("nlu") or rasa_json
    
    # Confidence from raw Rasa intent
    raw_intent = (nlu.get("intent") or {})
    conf_score = raw_intent.get("confidence", 0.0)
    confidence = score_to_bucket(conf_score)
    
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
    entities: List[Entity] = []
    if processed_entities:
        for e in processed_entities:
            entities.append(Entity(
                product=e.get("product"),
                quantity=e.get("quantity"),
                unit=e.get("unit"),
                raw=e.get("product")  # Use product as raw for now
            ))
    else:
        # Fallback: group raw Rasa entities
        grouped = _group_raw_rasa_entities(nlu.get("entities") or [])
        for e in grouped:
            entities.append(Entity(
                product=e.get("product"),
                quantity=e.get("quantity"),
                unit=e.get("unit"),
                raw=e.get("product")  # Use product as raw for now
            ))
    
    return IntentMeta(
        intent=intent_name,
        confidence=confidence,
        confidence_score=conf_score,
        entities=entities,
    )


def map_rasa_to_intent_metas(rasa_json) -> List[IntentMeta]:
    """Map Rasa response to multiple IntentMeta objects for multi-intent support."""
    single_meta = map_rasa_to_intent_meta(rasa_json)
    
    # Check if this is a multi-intent (contains +)
    intent_name = single_meta.intent
    if "+" in intent_name:
        # Split multi-intent into individual intents
        individual_intents = _split_multi_intent(intent_name)
        
        # Create separate IntentMeta for each intent with same entities
        metas = []
        for intent in individual_intents:
            meta = IntentMeta(
                intent=intent,
                confidence=single_meta.confidence,
                confidence_score=single_meta.confidence_score,
                entities=single_meta.entities.copy()  # Share entities across intents
            )
            metas.append(meta)
        return metas
    else:
        # Single intent
        return [single_meta]


def map_llm_to_intent_meta(llm_json) -> IntentMeta:
    res = llm_json.get("result") or llm_json
    intent = (res.get("intent") or "NONE").upper()
    confidence = (res.get("confidence") or "low").lower()
    ents = [Entity(**e) for e in (res.get("entities") or [])]
    return IntentMeta(intent=intent, confidence=confidence, entities=ents)


def map_llm_multi_to_intent_metas(llm_json) -> List[IntentMeta]:
    """Map LLM multi-intent response to list of IntentMeta"""
    res = llm_json.get("result") or llm_json
    intents = res.get("intents", [])
    
    intent_metas = []
    for intent_data in intents:
        intent_metas.append(IntentMeta(
            intent=intent_data.get("intent", "NONE").upper(),
            confidence=intent_data.get("confidence", "low").lower(),
            entities=[Entity(**e) for e in intent_data.get("entities", [])]
        ))
    
    return intent_metas


