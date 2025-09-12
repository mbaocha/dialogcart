"""
Mappers for converting between different service formats
"""
from typing import List
from .models import IntentMeta, Entity, Action
from .confidence import score_to_bucket
import re
import pathlib
import yaml
import sys


def _load_verb_map_from_training() -> dict[str, str]:
    """Load verb synonyms from training YAML and build mapping to canonical actions."""
    # Try multiple possible paths for the training data file
    possible_paths = [
        # Local development path (from core directory)
        pathlib.Path("../trainings/initial_training_data.yml"),
        # Local development path (from intents directory)
        pathlib.Path("trainings/initial_training_data.yml"),
        # Docker container path (copied to core directory)
        pathlib.Path("initial_training_data.yml"),
        # Alternative paths
        pathlib.Path("/app/src/intents/trainings/initial_training_data.yml"),
    ]
    
    training_path = None
    for path in possible_paths:
        if path.exists():
            training_path = path
            break
    
    if not training_path:
        print("ERROR: Training data file not found. Tried paths:")
        for path in possible_paths:
            print(f"  - {path}")
        sys.exit(1)
    
    print(f"✅ Found training data at: {training_path}")
    
    try:
        data = yaml.safe_load(training_path.read_text()) or {}
        mapping: dict[str, str] = {}
        
        # Process synonym groups for add, remove, set
        for item in data.get("nlu", []):
            if item.get("synonym") in ("add", "remove", "set"):
                canonical = item.get("synonym")
                examples = item.get("examples") or ""
                for line in examples.splitlines():
                    ex = line.strip()
                    if ex.startswith("- "):
                        ex = ex[2:].strip()
                    if ex:
                        mapping[ex.lower()] = canonical
        
        # Ensure canonical verbs map to themselves
        for v in ("add", "remove", "set"):
            mapping[v] = v
        
        if not mapping:
            print("ERROR: No verb synonyms found in training data")
            sys.exit(1)
            
        print(f"✅ Loaded {len(mapping)} verb mappings from training data")
        return mapping
        
    except Exception as e:
        print(f"ERROR: Could not load verb mapping from training data: {e}")
        sys.exit(1)

# Load verb mapping once at module import
_VERB_TO_ACTION = _load_verb_map_from_training()

# Load replace synonyms for detection
_REPLACE_SYNONYMS = set()
def _load_replace_synonyms():
    """Load replace synonyms from training data for detection."""
    possible_paths = [
        pathlib.Path("../trainings/initial_training_data.yml"),
        pathlib.Path("trainings/initial_training_data.yml"),
        pathlib.Path("initial_training_data.yml"),
        pathlib.Path("/app/src/intents/trainings/initial_training_data.yml"),
    ]
    
    training_path = None
    for path in possible_paths:
        if path.exists():
            training_path = path
            break
    
    if training_path:
        try:
            data = yaml.safe_load(training_path.read_text(encoding="utf-8")) or {}
            for item in data.get("nlu", []):
                if item.get("synonym") == "replace":
                    examples = item.get("examples") or ""
                    for line in examples.splitlines():
                        ex = line.strip()
                        if ex.startswith("- "):
                            ex = ex[2:].strip()
                        if ex:
                            _REPLACE_SYNONYMS.add(ex.lower())
        except Exception:
            pass

_load_replace_synonyms()

def _is_replace_verb(verb: str) -> bool:
    """Check if verb is a replace synonym."""
    return verb.lower() in _REPLACE_SYNONYMS

def _split_multi_intent(intent_name: str) -> List[str]:
    """Split Rasa multi-intent format (A+B) into individual intents."""
    if "+" in intent_name:
        return [intent.strip().upper() for intent in intent_name.split("+")]
    return [intent_name.upper()]

def _map_verb_to_action(verb: str) -> str:
    """Map verb to canonical action using training data synonyms."""
    v = (verb or "").strip().lower()
    return _VERB_TO_ACTION.get(v, v)


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
        action_name = _map_verb_to_action(verb)
        print(f"DEBUG: verb='{verb}' -> action='{action_name}'")
        
        # Handle replace operations with two products
        if action_name == "replace" and _is_replace_verb(verb) and len(products) >= 2:
            # Single replace action with product_from and product_to
            action_dict = {
                "action": action_name,
                "product_from": products[0],  # First product = what to replace
                "product_to": products[1],    # Second product = what to replace with
                "product": products[1]        # Keep product for backward compatibility
            }
            # Use first available quantity and unit
            if quantities:
                action_dict["quantity"] = quantities[0]
            if units:
                action_dict["unit"] = units[0]
            if containers:
                action_dict["container"] = containers[-1]
            out_actions.append(_create_action_from_dict(action_dict, confidence, confidence_score))
        else:
            # Original logic for other operations
            max_pairs = max(len(products), len(quantities), len(units))
            for i in range(max_pairs):
                if i < len(products):
                    action_dict = {"action": action_name, "product": products[i]}
                    # For remove-like actions, do not carry quantity/unit
                    if action_name != "remove":
                        if i < len(quantities):
                            action_dict["quantity"] = quantities[i]
                        # Distribute unit - if only one unit, use it for all actions
                        if units:
                            if len(units) == 1:
                                action_dict["unit"] = units[0]
                            elif i < len(units):
                                action_dict["unit"] = units[i]
                    if containers:
                        action_dict["container"] = containers[-1]
                    out_actions.append(_create_action_from_dict(action_dict, confidence, confidence_score))
    
    for e in entities_by_pos:
        et = e.get("entity")
        ev = e.get("value")
        if et == "verb":
            # Skip low-confidence verb entities and common words that aren't actions
            entity_confidence = e.get("confidence_entity", 0.0)
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
        product_from=action_dict.get("product_from"),
        product_to=action_dict.get("product_to"),
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


