"""
Post-NLU Processor for converting Rasa NLU results into structured actions.

This module processes the output from Rasa NLU (entities, intents, confidence)
and converts it into structured actions with business logic applied.
"""
from typing import List
from .models import Action
from .confidence import score_to_bucket
from .entity_processor import process_entities
import re


def _load_verb_map_from_training() -> dict[str, str]:
    """Load verb synonyms from training YAML and build mapping to canonical actions."""
    from .training_data_loader import training_data_loader
    return training_data_loader.get_verb_synonyms()

# Load verb mapping once at module import
_VERB_TO_ACTION = _load_verb_map_from_training()

# Note: Replace synonyms are now merged into "set" synonym group
# Operation type is determined by entity count (2 products = replace, 1 product = update)

def _map_verb_to_action(verb: str) -> str:
    """Map verb to canonical action using training data synonyms."""
    v = (verb or "").strip().lower()
    
    # First try exact match
    if v in _VERB_TO_ACTION:
        return _VERB_TO_ACTION[v]
    
    # Try substring matching - check if any known verb is contained in the input
    for known_verb, action in _VERB_TO_ACTION.items():
        if known_verb in v:
            print(f"DEBUG: Found substring match '{known_verb}' in '{v}' -> '{action}'")
            return action
    
    # Try reverse substring matching - check if input is contained in any known verb
    for known_verb, action in _VERB_TO_ACTION.items():
        if v in known_verb:
            print(f"DEBUG: Found reverse substring match '{v}' in '{known_verb}' -> '{action}'")
            return action
    
    # If no match found, try to extract common verb patterns
    # Handle cases like "add ancarton" -> "add"
    for known_verb, action in _VERB_TO_ACTION.items():
        if v.startswith(known_verb + " "):
            print(f"DEBUG: Found prefix match '{known_verb}' in '{v}' -> '{action}'")
            return action
    
    # Last resort: return the original verb (this preserves existing behavior for truly unknown verbs)
    print(f"DEBUG: No verb mapping found for '{v}', returning as-is")
    return v



def _parse_shopping_command(entities: List[dict], confidence: str, confidence_score: float, slots: dict = None, source_text: str = "") -> List[Action]:
    """Parse modify_cart entities into multiple actions using verb-segmented FIFO queues."""
    # Debug logging
    print(f"DEBUG: _parse_shopping_command entities: {entities}")
    print(f"DEBUG: _parse_shopping_command slots: {slots}")

    # Only use slot memory to fill product when the user used a pronoun
    text_lower = (source_text or "").lower()
    pronouns = {"it", "this", "that", "them", "these", "those"}
    is_pronoun_ref = any(p in text_lower for p in pronouns)

    # Check if we need to add missing product from slot memory (pronoun-only)
    has_product_entity = any(e.get("entity") == "product" for e in entities)
    if not has_product_entity and slots and is_pronoun_ref:
        product_from_slots = slots.get("last_mentioned_product") or slots.get("last_product_added")
        if product_from_slots:
            earliest_start = min([e.get("start", 0) for e in entities if e.get("start", 0) >= 0], default=0)
            entities.append({
                "entity": "product",
                "value": product_from_slots,
                "start": earliest_start,
                "end": earliest_start,
                "confidence": 0.8
            })
            print(f"DEBUG: Added product from slot memory: {product_from_slots} at position {earliest_start}")

    # Check for ambiguous product references and set to null if no context
    ambiguous_products = {"it", "this", "that", "them", "these", "those"}
    for entity in entities:
        if entity.get("entity") == "product" and str(entity.get("value", "")).lower() in ambiguous_products:
            has_context = slots and (slots.get("last_mentioned_product") or slots.get("last_product_added"))
            if not has_context:
                entity["value"] = None
                print("DEBUG: Set ambiguous product to null (no context)")

    # Process entities: deduplicate and expand single-token products
    entities = process_entities(entities, source_text)

    # Sort entities by start position
    entities_by_pos = sorted(entities, key=lambda x: x.get("start", 0))

    # Words that should never be treated as verbs
    FILLER_WORDS = {"and", "or", "with", "to", "from", "the", "a", "an", 
                    "please", "cart", "into", "in", "my", "on"}

    def finalize_segment(seg, out_actions):
        verb = seg.get("verb")
        products = seg.get("products", [])
        quantities = seg.get("quantities", [])
        units = seg.get("units", [])
        containers = seg.get("containers", [])
        variants = seg.get("variants", [])
        sizes = seg.get("sizes", [])
        colors = seg.get("colors", [])
        fits = seg.get("fits", [])
        flavors = seg.get("flavors", [])
        diets = seg.get("diets", [])
        roasts = seg.get("roasts", [])

        # Default verb → add
        if not verb:
            if products:
                verb = "add"
                action_name = "add"
                print(f"DEBUG: No verb found, defaulting to 'add' for {products}")
            else:
                return

        # Map verb to canonical action
        action_name = _map_verb_to_action(verb)
        if action_name in ("set", "replace"):
            action_name = "set"

        print(f"DEBUG: verb='{verb}' -> action='{action_name}'")

        if action_name == "set":
            ...
        else:
            # --- Add / Remove ---
            print(f"DEBUG: finalize_segment - products: {products}, quantities: {quantities}, units: {units}")
            max_pairs = max(
                len(products), len(quantities), len(units), len(variants),
                len(sizes), len(colors), len(fits), len(flavors), len(diets), len(roasts)
            )
            print(f"DEBUG: max_pairs: {max_pairs}")

            if max_pairs == 0:
                # No explicit product → emit action with product=None (e.g. 'add to cart')
                action_dict = {"action": action_name}
                if containers:
                    action_dict["container"] = containers[-1]
                print(f"DEBUG: Final action_dict (no product): {action_dict}")
                out_actions.append(_create_action_from_dict(action_dict, confidence, confidence_score))
                return

            for i in range(max_pairs):
                if i < len(products):
                    action_dict = {"action": action_name, "product": products[i]}
                    if i < len(quantities):
                        action_dict["quantity"] = quantities[i]
                    if units:
                        action_dict["unit"] = units[0] if len(units) == 1 else units[i]
                    # Build attributes map from optional lists
                    attrs: dict[str, str] = {}
                    def pick(lst):
                        return lst[0] if len(lst) == 1 else lst[i]
                    if variants:
                        attrs["variant"] = pick(variants)
                    if sizes:
                        attrs["size"] = pick(sizes)
                    if colors:
                        attrs["color"] = pick(colors)
                    if fits:
                        attrs["fit"] = pick(fits)
                    if flavors:
                        attrs["flavor"] = pick(flavors)
                    if diets:
                        attrs["diet"] = pick(diets)
                    if roasts:
                        attrs["roast"] = pick(roasts)
                    if attrs:
                        action_dict["attributes"] = attrs
                    if containers:
                        action_dict["container"] = containers[-1]
                    # Attributes already attached above
                    print(f"DEBUG: Final action_dict: {action_dict}")
                    out_actions.append(_create_action_from_dict(action_dict, confidence, confidence_score))
    # --- Segment builder ---
    segments = []
    current = {"verb": None, "products": [], "quantities": [], "units": [], "containers": [], "variants": [],
               "sizes": [], "colors": [], "fits": [], "flavors": [], "diets": [], "roasts": []}

    for e in entities_by_pos:
        et, ev = e.get("entity"), e.get("value")
        if et == "verb":
            entity_conf = e.get("confidence_entity", 0.0)
            if entity_conf < 0.3 or (ev and ev.lower() in FILLER_WORDS):
                print(f"DEBUG: skipping filler verb '{ev}' conf={entity_conf}")
                continue
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
        elif et == "variant":
            current["variants"].append(ev)
        elif et == "size":
            current["sizes"].append(ev)
        elif et == "color":
            current["colors"].append(ev)
        elif et == "fit":
            current["fits"].append(ev)
        elif et == "flavor":
            current["flavors"].append(ev)
        elif et == "diet":
            current["diets"].append(ev)
        elif et == "roast":
            current["roasts"].append(ev)

    if current["verb"] or current["products"] or current["quantities"] or current["units"] or current["containers"]:
        segments.append(current)

    print(f"DEBUG: verb segments {segments}")

    # Build final actions
    actions: List[Action] = []
    for seg in segments:
        finalize_segment(seg, actions)

    print(f"DEBUG: final actions {[a.dict() for a in actions]}")
    return _deduplicate_actions(actions)


def _deduplicate_actions(actions: List[Action]) -> List[Action]:
    """Remove duplicate/overlapping actions, keep richer ones (with qty/unit)."""
    unique: dict[tuple, Action] = {}
    for act in actions:
        key = (act.action, act.product)

        if key not in unique:
            unique[key] = act
        else:
            existing = unique[key]

            # Prefer the one with quantity
            if existing.quantity is None and act.quantity is not None:
                unique[key] = act
            # Allow both if quantities differ
            elif existing.quantity is not None and act.quantity is not None and existing.quantity != act.quantity:
                key2 = (act.action, act.product, act.quantity)
                unique[key2] = act
            # Otherwise, drop duplicate

    return list(unique.values())


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
        product_from=action_dict.get("product_from"),
        product_to=action_dict.get("product_to"),
        quantity=quantity,
        unit=action_dict.get("unit"),
        container=action_dict.get("container"),
        confidence=confidence,
        confidence_score=confidence_score
    )




def map_rasa_to_actions(rasa_json) -> List[Action]:
    """Map Rasa response to actions for modify_cart approach (backward compatible)."""
    nlu = rasa_json.get("nlu") or rasa_json
    source_text = (rasa_json.get("text") or nlu.get("text") or "").strip()
    
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
    entities = list(raw_entities)

    # Parse shopping command into actions (with slot memory support)
    slots = rasa_json.get("slots", {})
    actions =  _parse_shopping_command(entities, confidence, conf_score, slots, source_text)
    actions = _deduplicate_actions(actions)

    return actions

