from typing import List
from .models import IntentMeta, Entity, Action
from .confidence import score_to_bucket
import re


def _split_multi_intent(intent_name: str) -> List[str]:
    """Split Rasa multi-intent format (A+B) into individual intents."""
    if "+" in intent_name:
        return [intent.strip().upper() for intent in intent_name.split("+")]
    return [intent_name.upper()]

def _map_verb_to_action(verb: str) -> str:
    v = (verb or "").strip().lower()
    if v in ("add", "put", "insert", "include", "throw in", "add in", "add on"):
        return "add"
    if v in ("remove", "delete", "drop", "take", "take out", "takeoff", "takeoff", "cancel"):
        return "remove"
    if v in ("increase", "increment", "raise", "bump", "more", "add more"):
        return "increase"
    if v in ("decrease", "reduce", "lower", "less", "subtract"):
        return "decrease"
    if v in ("set", "make", "update", "change", "switch to"):
        return "set"
    if v in ("check", "find", "do you sell", "do you have", "exist", "is available", "in stock"):
        return "check"
    return v


def _parse_shopping_command(entities: List[dict], confidence: str, confidence_score: float) -> List[Action]:
    """Parse modify_cart entities into multiple actions using verb-segmented FIFO queues."""
    if not entities:
        return []
    
    # Debug logging
    print(f"DEBUG: _parse_shopping_command entities: {entities}")
    
    # Group entities by position
    entities_by_pos = sorted(entities, key=lambda x: x.get("start", 0))
    print(f"DEBUG: entities_by_pos: {entities_by_pos}")
    
    # Build segments split by verb
    segments = []
    current = {"verb": None, "products": [], "quantities": [], "units": [], "containers": []}
    
    def finalize_segment(seg, out_actions):
        verb = seg.get("verb")
        if not verb:
            return
        products = seg.get("products", [])
        quantities = seg.get("quantities", [])
        units = seg.get("units", [])
        containers = seg.get("containers", [])
        max_pairs = max(len(products), len(quantities), len(units))
        action_name = _map_verb_to_action(verb)
        for i in range(max_pairs):
            if i < len(products):
                action_dict = {"action": action_name, "product": products[i]}
                if i < len(quantities):
                    action_dict["quantity"] = quantities[i]
                # Fix: Distribute unit - if only one unit, use it for all actions
                if units:
                    if len(units) == 1:
                        action_dict["unit"] = units[0]  # Use single unit for all actions
                    elif i < len(units):
                        action_dict["unit"] = units[i]  # Use specific unit for this action
                if containers:
                    action_dict["container"] = containers[-1]
                out_actions.append(_create_action_from_dict(action_dict, confidence, confidence_score))
    
    for e in entities_by_pos:
        et = e.get("entity")
        ev = e.get("value")
        if et == "verb":
            # Skip low-confidence verb entities and common words that aren't actions
            entity_confidence = e.get("confidence_entity", 0.0)  # Fix: Use different variable name
            if entity_confidence < 0.5 or ev.lower() in ["and", "or", "with", "to", "from"]:
                continue
                
            # finalize previous segment (if any)
            if current["verb"] or current["products"] or current["quantities"] or current["units"] or current["containers"]:
                segments.append(current)
                current = {"verb": None, "products": [], "quantities": [], "units": [], "containers": []}
            current["verb"] = ev
        elif et == "product":
            current["products"].append(ev)
        elif et == "quantity":
            current["quantities"].append(ev)
        elif et == "unit":
            current["units"].append(ev)
        elif et == "container":
            current["containers"].append(ev)
    
    # push last segment
    if current["verb"] or current["products"] or current["quantities"] or current["units"] or current["containers"]:
        segments.append(current)
    
    print(f"DEBUG: verb segments: {segments}")
    
    # Build actions from segments
    actions: List[Action] = []
    for seg in segments:
        finalize_segment(seg, actions)
    
    print(f"DEBUG: final actions: {[action.dict() for action in actions]}")
    return actions


def _create_action_from_dict(action_dict: dict, confidence: str, confidence_score: float) -> Action:
    """Create Action object from parsed dictionary."""
    # Convert quantity to float if present
    quantity = action_dict.get("quantity")
    if quantity is not None:
        try:
            if isinstance(quantity, str):
                # Extract number from string like "2kg" -> 2.0
                m = re.search(r'[-+]?\d*\.?\d+', quantity)
                if m:
                    quantity = float(m.group())
                else:
                    quantity = None
            else:
                quantity = float(quantity)
        except (ValueError, TypeError):
            quantity = None
    
    return Action(
        action=action_dict.get("action", "unknown"),
        product=action_dict.get("product"),
        quantity=quantity,
        unit=action_dict.get("unit"),
        container=action_dict.get("container"),
        confidence=confidence,
        confidence_score=confidence_score
    )


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
            except (ValueError, TypeError):
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


def map_rasa_to_actions(rasa_json) -> List[Action]:
    """Map Rasa response to actions for modify_cart approach (backward compatible)."""
    nlu = rasa_json.get("nlu") or rasa_json
    
    # Get intent name
    raw_intent = (nlu.get("intent") or {})
    intent_name = raw_intent.get("name", "NONE")
    
    # Only parse actions for modify_cart (case insensitive). Back-compat: accept SHOPPING_COMMAND
    if intent_name.upper() not in ("MODIFY_CART", "SHOPPING_COMMAND"):
        return []
    
    # Get confidence - handle both direct and nested structures
    conf_score = raw_intent.get("confidence", 0.0)
    if conf_score is None:
        conf_score = 0.0
    
    confidence = score_to_bucket(conf_score)
    
    # Get entities: always use raw Rasa entities to retain 'verb' for action parsing
    raw_entities = nlu.get("entities") or []
    entities = raw_entities
    
    # If no verb entity is found but this is a modify_cart intent, add a default verb
    # This handles follow-up commands like "make it 9kg" that don't have explicit verbs
    has_verb = any(e.get("entity") == "verb" for e in entities)
    if not has_verb and intent_name.upper() in ("MODIFY_CART", "SHOPPING_COMMAND"):
        # Check if this looks like a follow-up command (has quantity but no product/verb)
        has_quantity = any(e.get("entity") == "quantity" for e in entities)
        has_product = any(e.get("entity") == "product" for e in entities)
        
        if has_quantity and not has_product:
            # This is likely a follow-up command like "make it 9kg"
            entities.append({
                "entity": "verb",
                "value": "set",  # Default to "set" for follow-up commands
                "start": 0,
                "end": 0,
                "confidence_entity": 0.8
            })
        elif has_quantity and has_product:
            # This has both product and quantity, default to "add"
            entities.append({
                "entity": "verb", 
                "value": "add",
                "start": 0,
                "end": 0,
                "confidence_entity": 0.8
            })
    
    # Parse shopping command into actions
    return _parse_shopping_command(entities, confidence, conf_score)


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


def map_llm_to_actions(llm_json) -> List[Action]:
    """Map LLM response to actions for modify_cart approach (backward compatible).
    If LLM misses some product mentions (common when products and quantities are split
    across sentences), we use a light heuristic on the original text to recover them.
    """
    result = llm_json.get("result") or llm_json
    source_text = (result.get("text") or llm_json.get("text") or "").strip()
    
    # Get intents from LLM response
    intents = result.get("intents", [])
    if not intents:
        return []
    
    # Check if any intent is modify_cart (or legacy shopping_command)
    modify_cart_intents = [
        intent for intent in intents 
        if intent.get("intent", "").upper() in ("MODIFY_CART", "SHOPPING_COMMAND")
    ]
    
    if not modify_cart_intents:
        return []
    
    # Use the first modify_cart intent (most confident)
    intent_data = modify_cart_intents[0]
    
    # Get confidence
    confidence = intent_data.get("confidence", "low").lower()
    confidence_score = intent_data.get("confidence_score", 0.0)
    
    # If LLM already grouped each product with its own quantity/unit, map directly
    llm_entities = intent_data.get("entities", [])
    if llm_entities:
        direct_actions: List[Action] = []
        for e in llm_entities:
            prod = e.get("product")
            qty = e.get("quantity")
            unit = e.get("unit")
            verb_val = e.get("verb") or "add"
            action_name = _map_verb_to_action(verb_val)
            if prod or qty is not None:
                direct_actions.append(Action(
                    action=action_name,
                    product=prod,
                    quantity=qty,
                    unit=unit,
                    confidence=confidence,
                    confidence_score=confidence_score,
                ))
        if direct_actions:
            return direct_actions

    # Convert LLM entities to Rasa-like format for parsing
    entities = []
    for entity in llm_entities:
        # Add product entity if present
        if entity.get("product"):
            entities.append({
                "entity": "product",
                "value": entity.get("product"),
                "start": 0,  # Placeholder - LLM doesn't provide position
                "end": 0,
                "confidence_entity": 0.9  # Default confidence
            })
        
        # Add quantity entity if present
        if entity.get("quantity") is not None:
            entities.append({
                "entity": "quantity",
                "value": str(entity.get("quantity")),
                "start": 0,
                "end": 0,
                "confidence_entity": 0.9
            })
        
        # Add unit entity if present
        if entity.get("unit"):
            entities.append({
                "entity": "unit",
                "value": entity.get("unit"),
                "start": 0,
                "end": 0,
                "confidence_entity": 0.9
            })
        
        # Add verb entity if present
        if entity.get("verb"):
            entities.append({
                "entity": "verb",
                "value": entity.get("verb"),
                "start": 0,
                "end": 0,
                "confidence_entity": 0.9
            })
    
    # Heuristic recovery: if text suggests multiple products but LLM omitted some,
    # try to extract product names from the first clause.
    try:
        if source_text:
            # Count currently present product names
            present_products = {e.get("value") for e in entities if e.get("entity") == "product" and e.get("value")}
            # Only attempt when fewer products than quantities, or none found
            num_products = len(present_products)
            num_quantities = sum(1 for e in entities if e.get("entity") == "quantity")
            if num_products < max(1, num_quantities):
                text_clause = source_text.split(".")[0]
                lower_clause = text_clause.lower()
                # Trim leading verb
                for v in ("add ", "remove ", "increase ", "decrease ", "set "):
                    if lower_clause.startswith(v):
                        text_clause = text_clause[len(v):]
                        break
                # Cut trailing "to cart" or similar
                for tail in (" to cart", " into cart", " in cart"):
                    if text_clause.lower().endswith(tail):
                        text_clause = text_clause[: -len(tail)]
                        break
                # Split by and/commas
                candidates = text_clause.replace(" and ", ",").split(",")
                stop_words = {"cart", "kg", "g", "lb", "piece", "pieces", "bag", "box"}
                recovered = []
                for cand in candidates:
                    name = cand.strip().strip(" -:")
                    if not name or any(ch.isdigit() for ch in name):
                        continue
                    if name.lower() in stop_words:
                        continue
                    if name not in present_products:
                        entities.append({
                            "entity": "product",
                            "value": name,
                            "start": 0,
                            "end": 0,
                            "confidence_entity": 0.6,
                        })
                        recovered.append(name)
                # Refresh present_products
                if recovered:
                    present_products.update(recovered)
    except Exception:
        # Best-effort only; ignore heuristic errors
        pass

    # If no verb was extracted, add a default one for MODIFY_CART
    intent_name = intent_data.get("intent", "").upper()
    if intent_name in ("MODIFY_CART", "SHOPPING_COMMAND") and not any(e.get("entity") == "verb" for e in entities):
        entities.append({
            "entity": "verb",
            "value": "add",  # Default verb if none extracted
            "start": 0,
            "end": 0,
            "confidence_entity": 0.9
        })
    
    # Parse using the same logic as Rasa
    return _parse_shopping_command(entities, confidence, confidence_score)
