"""
Entity extraction, parameterization, and canonicalization.

Handles:
- Entity extraction from spaCy documents
- Sentence parameterization (replacing entities with tokens)
- Entity canonicalization (mapping to canonical forms)
- Ambiguity classification (productbrands, units, variants, brands)
"""
from typing import List, Dict, Any, Set

# Import config and normalization functions
from luma.config import debug_print
from .normalization import post_normalize_parameterized_text


def _remove_entity_occurrence(entity_list: List, position: int, length: int) -> None:
    """
    Remove an entity from a list by its position if it overlaps the same span.
    
    Modifies entity_list in-place.
    
    Args:
        entity_list: List of entities (dicts with "position" key)
        position: Start position of span to remove
        length: Length of span
        
    NOTE: Matches semantics/nlp_processor.py lines 59-68 exactly
    """
    if not entity_list:
        return
    start, end = position, position + length
    entity_list[:] = [
        e for e in entity_list
        if not (isinstance(e, dict)
                and start <= e.get("position", -1) < end)
    ]


def add_entity_with_tracking(
    result: Dict[str, List],
    entity_counts: Dict[str, Dict[str, int]],
    entity_type: str,
    text: str,
    position: int,
    debug_units: bool = False,
    length: int = 1
) -> None:
    """
    Add entity to result with tracking.
    
    Always adds as dict with position (+ span length for parameterization).
    
    Args:
        result: Result dictionary to modify
        entity_counts: Count dictionary to track occurrences
        entity_type: Type of entity (brands, products, etc.)
        text: Entity text
        position: Token position
        debug_units: Enable debug logging
        length: Span length in spaCy tokens
        
    NOTE: Matches semantics/nlp_processor.py lines 289-306 exactly
    """
    # Always dict with position (+ span length for parameterization)
    if entity_type not in entity_counts:
        entity_counts[entity_type] = {}
    
    current_count = entity_counts[entity_type].get(text.lower(), 0) + 1
    entity_counts[entity_type][text.lower()] = current_count
    
    entity_obj = {
        "text": text,
        "occurrence": current_count,
        "position": position,
        "length": length  # ✅ span length in spaCy tokens
    }
    result[entity_type].append(entity_obj)
    
    if debug_units:
        debug_print(f"[DEBUG] Added {entity_type}: {entity_obj}")


def extract_entities_from_doc(
    nlp,
    text: str,
    entities: List[Dict[str, Any]],
    debug_units: bool = False
) -> Dict[str, List]:
    """
    Extract entities using spaCy with entity ruler and token-level fallback.
    
    Steps:
    1. EntityRuler matches (BRAND, PRODUCT, UNIT, VARIANT, PRODUCTBRAND)
    2. Token-level fallback for unmatched tokens
    3. Context classification for ambiguous entities
    
    Args:
        nlp: spaCy language model
        text: Input text (already normalized)
        entities: List of entity dictionaries
        debug_units: Enable debug logging
        
    Returns:
        Dictionary with extracted entities by type
        
    NOTE: Matches semantics/nlp_processor.py lines 309-467 exactly
    """
    # Import support maps builder here to avoid circular imports
    from .entity_loading import build_support_maps
    
    doc = nlp(text)
    
    (
        unit_map, variant_map, product_map,
        brand_map, noise_set,
        unambiguous_units, ambiguous_units,
        unambiguous_variants, ambiguous_variants, ambiguous_brands
    ) = build_support_maps(entities)
    
    result = {
        "brands": [],
        "likely_brands": [],
        "products": [],
        "likely_products": [],
        "variants": [],
        "likely_variants": [],
        "productbrands": [],     # ✅ new group
        "quantities": [],
        "units": []
    }
    
    entity_counts = {k: {} for k in result.keys()}
    
    # Track ambiguous candidates for later context classification
    candidate_ambiguous_units = []
    candidate_ambiguous_variants = []
    candidate_ambiguous_brands = []
    
    # === Step 1: EntityRuler matches
    for ent in doc.ents:
        lemma = ent.text.lower()
        span_len = ent.end - ent.start
        
        # ✅ Skip "item" if followed by a digit (ordinal pattern: "item 1", "item 2")
        if lemma in {"item", "items"}:
            next_token = doc[ent.end] if ent.end < len(doc) else None
            if next_token and (next_token.text.isdigit() or next_token.like_num):
                if debug_units:
                    debug_print(f"[DEBUG] Skipping '{ent.text}' - ordinal pattern detected (followed by '{next_token.text}')")
                continue
        
        if ent.label_ == "PRODUCTBRAND":
            add_entity_with_tracking(result, entity_counts, "productbrands",
                                    ent.text, ent.start, debug_units, length=span_len)
        
        elif ent.label_ == "BRAND":
            add_entity_with_tracking(result, entity_counts, "brands", ent.text, ent.start, debug_units, length=span_len)
        
        elif ent.label_ == "PRODUCT":
            if lemma not in noise_set:
                add_entity_with_tracking(result, entity_counts, "products", ent.text, ent.start, debug_units, length=span_len)
        
        elif ent.label_ == "UNIT":
            if lemma not in unit_map:
                continue
            if lemma in ambiguous_units:
                candidate_ambiguous_units.append((lemma, ent.start))
            else:
                add_entity_with_tracking(result, entity_counts, "units", ent.text, ent.start, debug_units, length=span_len)
        
        elif ent.label_ == "VARIANT":
            if lemma not in variant_map:
                continue
            if lemma in ambiguous_variants:
                candidate_ambiguous_variants.append((lemma, ent.start))
            else:
                add_entity_with_tracking(result, entity_counts, "variants", ent.text, ent.start, debug_units, length=span_len)
    
    # === Step 1b: Classify and promote PRODUCTBRAND spans ===
    if result["productbrands"]:
        product_lex = set(product_map.keys())
        unit_lex = set(unit_map.keys())
        pb_decisions = classify_productbrands(doc, result["productbrands"], product_lex, unit_lex, debug=debug_units)
        for d in pb_decisions:
            if d["label"] == "brand":
                add_entity_with_tracking(result, entity_counts, "brands", d["text"], d["position"], debug_units, length=d["length"])
                _remove_entity_occurrence(result["products"], position=d["position"], length=d["length"])
            else:
                add_entity_with_tracking(result, entity_counts, "products", d["text"], d["position"], debug_units, length=d["length"])
                _remove_entity_occurrence(result["brands"], position=d["position"], length=d["length"])
    
    # === Step 2: Token-level fallback (same as before)
    for i, token in enumerate(doc):
        if token.ent_type_ in {"BRAND", "PRODUCT", "VARIANT", "UNIT", "PRODUCTBRAND"}:
            continue
        
        clean_text = token.text.rstrip('.,!?;:')
        lemma = clean_text.lower()
        if not lemma.isalpha():
            continue
        
        # ✅ Skip "item" if followed by a digit (ordinal pattern: "item 1")
        if lemma in {"item", "items"}:
            next_token = doc[i + 1] if i + 1 < len(doc) else None
            if next_token and (next_token.text.isdigit() or next_token.like_num):
                continue
        
        if lemma in brand_map:
            add_entity_with_tracking(result, entity_counts, "brands", clean_text, i, debug_units)
        elif lemma in product_map and lemma not in noise_set:
            add_entity_with_tracking(result, entity_counts, "products", clean_text, i, debug_units)
        elif lemma in unambiguous_units:
            add_entity_with_tracking(result, entity_counts, "units", clean_text, i, debug_units)
            if i > 0 and doc[i - 1].pos_ == "NUM":
                add_entity_with_tracking(result, entity_counts, "quantities", doc[i - 1].text, i-1, debug_units)
        elif lemma in ambiguous_units:
            candidate_ambiguous_units.append((lemma, i))
        elif lemma in unambiguous_variants:
            add_entity_with_tracking(result, entity_counts, "variants", clean_text, i, debug_units)
        elif lemma in ambiguous_variants:
            candidate_ambiguous_variants.append((lemma, i))
        elif lemma in ambiguous_brands:
            candidate_ambiguous_brands.append((lemma, i))
        elif lemma in noise_set:
            # ✅ Keep noise tokens for context, just mark them as noise
            if "noise_tokens" not in result:
                result["noise_tokens"] = []
            result["noise_tokens"].append({"text": token.text, "position": i})
            # do NOT 'continue' — preserve them in doc flow
        elif token.pos_ == "PROPN":
            add_entity_with_tracking(result, entity_counts, "likely_brands", token.text, i, debug_units)
        elif token.pos_ == "NOUN":
            add_entity_with_tracking(result, entity_counts, "likely_products", token.text, i, debug_units)
        elif token.pos_ in {"ADJ", "X"}:
            add_entity_with_tracking(result, entity_counts, "likely_variants", token.text, i, debug_units)
        elif token.pos_ == "NUM":
            if not (i + 1 < len(doc) and doc[i + 1].lemma_.lower() in unit_map):
                add_entity_with_tracking(result, entity_counts, "variants", token.text, i, debug_units)
    
    # === Step 3: Context classification
    # NOTE: Uses stub implementations - can be enhanced in Chunk 8 if needed
    if candidate_ambiguous_units:
        entity_texts = [lemma for lemma, _ in candidate_ambiguous_units]
        classification_result = classify_ambiguous_units(text, entity_texts, ambiguous_units, entities, debug=debug_units)
        for lemma, pos in candidate_ambiguous_units:
            if any(u["entity"] == lemma and u["position"] == pos for u in classification_result["units"]):
                add_entity_with_tracking(result, entity_counts, "units", lemma, pos, debug_units)
            elif any(p["entity"] == lemma and p["position"] == pos for p in classification_result["products"]):
                add_entity_with_tracking(result, entity_counts, "products", lemma, pos, debug_units)
    
    if candidate_ambiguous_variants:
        entity_texts = [lemma for lemma, _ in candidate_ambiguous_variants]
        classification_result = classify_ambiguous_variants(text, entity_texts, ambiguous_variants, entities, debug=debug_units)
        for lemma, pos in candidate_ambiguous_variants:
            if any(v["entity"] == lemma and v["position"] == pos for v in classification_result["variants"]):
                add_entity_with_tracking(result, entity_counts, "variants", lemma, pos, debug_units)
            elif any(p["entity"] == lemma and p["position"] == pos for p in classification_result["products"]):
                add_entity_with_tracking(result, entity_counts, "products", lemma, pos, debug_units)
    
    if candidate_ambiguous_brands:
        entity_texts = [lemma for lemma, _ in candidate_ambiguous_brands]
        classification_result = classify_ambiguous_brands(doc, entity_texts, ambiguous_brands, entities, debug=debug_units)
        for lemma, pos in candidate_ambiguous_brands:
            if any(b["entity"] == lemma and b["position"] == pos for b in classification_result["brands"]):
                add_entity_with_tracking(result, entity_counts, "brands", lemma, pos, debug_units)
            elif any(p["entity"] == lemma and p["position"] == pos for p in classification_result["products"]):
                add_entity_with_tracking(result, entity_counts, "products", lemma, pos, debug_units)
    
    return result


def build_parameterized_sentence_from_doc(doc, result: Dict[str, List]) -> str:
    """
    Build parameterized sentence from spaCy doc and extracted entities.
    
    Replaces entity positions with placeholder tokens (brandtoken, producttoken, etc).
    
    Args:
        doc: spaCy Doc object
        result: Extraction result with entities containing positions
        
    Returns:
        Parameterized sentence string
        
    NOTE: Matches semantics/nlp_processor.py lines 617-650 exactly
    """
    tokens = [t.text.lower() for t in doc]
    parameterized_tokens = tokens[:]
    
    # ✅ productbrands intentionally excluded
    placeholders = {
        "brands": "brandtoken",
        "products": "producttoken",
        "units": "unittoken",
        "variants": "varianttoken"
    }
    
    replacements = []
    for entity_type, entities in result.items():
        if entity_type in placeholders:
            placeholder = placeholders[entity_type]
            for ent in entities:
                if isinstance(ent, dict) and "position" in ent:
                    start = ent["position"]
                    length = ent.get("length", 1)
                    end = start + max(1, length)
                    replacements.append((start, end, placeholder))
    
    # Replace longer spans first (so multi-word entities stay intact)
    replacements.sort(key=lambda x: (x[0], -(x[1] - x[0])), reverse=True)
    for start, end, placeholder in replacements:
        parameterized_tokens[start:end] = [placeholder]
    
    debug_print("[DEBUG] Tokens before replacement:", tokens)
    debug_print("[DEBUG] Replacements:", replacements)
    debug_print("[DEBUG] Parameterized tokens after replacement:", parameterized_tokens)
    
    return " ".join(parameterized_tokens)


def simplify_result(result: Dict[str, List], doc) -> Dict[str, Any]:
    """
    Simplify result by removing positions and adding parameterized sentence,
    using the *same spaCy doc* that was used to generate entity positions.
    
    Keeps productbrands in final output (for debugging or analytics),
    but ensures psentence reflects promoted tokens.
    
    Args:
        result: Raw extraction result with position dicts
        doc: spaCy Doc object
        
    Returns:
        Simplified result with text values and parameterized sentence
        
    NOTE: Matches semantics/nlp_processor.py lines 686-730 exactly
    """
    simplified = {
        "brands": [],
        "likely_brands": [],
        "products": [],
        "likely_products": [],
        "variants": [],
        "quantities": [],
        "units": [],
        "productbrands": [],   # ✅ keep this in final output
        "osentence": doc.text,
        "psentence": ""
    }
    
    # ✅ Copy over all entity texts cleanly
    for entity_list_name in [
        "brands",
        "products",
        "units",
        "variants",
        "likely_brands",
        "likely_products",
        "productbrands"   # include new group
    ]:
        for entity_obj in result.get(entity_list_name, []):
            if isinstance(entity_obj, dict):
                simplified[entity_list_name].append(entity_obj["text"])
            else:
                simplified[entity_list_name].append(entity_obj)
    
    # Quantities remain as-is
    simplified["quantities"] = result.get("quantities", [])
    
    # ✅ Parameterize based on all entity positions (including productbrands)
    psentence_raw = build_parameterized_sentence_from_doc(doc, result)
    simplified["psentence"] = post_normalize_parameterized_text(psentence_raw)
    
    return simplified


def canonicalize_entities(
    result: Dict[str, List],
    unit_map: Dict[str, str],
    variant_map: Dict[str, str],
    product_map: Dict[str, str],
    brand_map: Dict[str, str],
    debug: bool = False
) -> Dict[str, List]:
    """
    Canonicalize all entities in the result using the provided maps.
    
    Args:
        result: Extraction result with entities
        unit_map: Unit canonical mapping
        variant_map: Variant canonical mapping
        product_map: Product canonical mapping
        brand_map: Brand canonical mapping
        debug: Enable debug logging
        
    Returns:
        Result with canonicalized entity texts
        
    NOTE: Matches semantics/nlp_processor.py lines 801-844 exactly
    """
    if debug:
        debug_print("[DEBUG] Canonicalizing entities")
    
    # Canonicalize brands
    for brand_obj in result["brands"]:
        if isinstance(brand_obj, dict):
            original_text = brand_obj["text"]
            canonical_text = brand_map.get(original_text.lower(), original_text)
            brand_obj["text"] = canonical_text
            if debug:
                debug_print(f"[DEBUG] Canonicalized brand: '{original_text}' → '{canonical_text}'")
    
    # Canonicalize products
    for product_obj in result["products"]:
        if isinstance(product_obj, dict):
            original_text = product_obj["text"]
            canonical_text = product_map.get(original_text.lower(), original_text)
            product_obj["text"] = canonical_text
            if debug:
                debug_print(f"[DEBUG] Canonicalized product: '{original_text}' → '{canonical_text}'")
    
    # Canonicalize units
    for unit_obj in result["units"]:
        if isinstance(unit_obj, dict):
            original_text = unit_obj["text"]
            canonical_text = unit_map.get(original_text.lower(), original_text)
            unit_obj["text"] = canonical_text
            if debug:
                debug_print(f"[DEBUG] Canonicalized unit: '{original_text}' → '{canonical_text}'")
    
    # Canonicalize variants
    for variant_obj in result["variants"]:
        if isinstance(variant_obj, dict):
            original_text = variant_obj["text"]
            canonical_text = variant_map.get(original_text.lower(), original_text)
            variant_obj["text"] = canonical_text
            if debug:
                debug_print(f"[DEBUG] Canonicalized variant: '{original_text}' → '{canonical_text}'")
    
    return result


# ===============================================================================
# Context-Based Entity Classification
# ===============================================================================
# Ported from semantics/nlp_processor.py lines 847-1111
# Refactored into EntityClassifier class for better performance and organization

from .entity_classifier import EntityClassifier


# Backward compatibility: Standalone function wrappers
def classify_productbrands(doc, productbrands: List[Dict], product_lex: Set[str], unit_lex: Set[str], debug: bool = False) -> List[Dict]:
    """
    Promote each productbrand to BRAND or PRODUCT.
    
    Wrapper for backward compatibility. For better performance, use EntityClassifier directly.
    
    NOTE: Matches semantics/nlp_processor.py lines 1071-1111 exactly
    """
    # Note: This wrapper doesn't need entities parameter since classify_productbrands
    # doesn't use cached lookups (it receives product_lex/unit_lex directly)
    classifier = EntityClassifier([])  # Empty entities list is fine here
    return classifier.classify_productbrands(doc, productbrands, product_lex, unit_lex, debug)


def classify_ambiguous_units(sentence: str, entity_list: List[str], ambiguous_units: Set[str], entities: List[Dict], debug: bool = False) -> Dict:
    """
    Classify ambiguous entities as units or products.
    
    Examples:
        - "Add 2 **bags** of rice" → bag is UNIT (preceded by number)
        - "Add 1 Gucci **bag**" → bag is PRODUCT (preceded by brand)
    
    Returns two lists:
      - units: entities classified as units
      - products: entities classified as products
      
    NOTE: Matches semantics/nlp_processor.py lines 847-921 exactly
    """
    classifier = EntityClassifier(entities)
    return classifier.classify_units(sentence, entity_list, ambiguous_units, debug)


def classify_ambiguous_variants(sentence: str, entity_list: List[str], ambiguous_variants: Set[str], entities: List[Dict], debug: bool = False) -> Dict:
    """
    Classify ambiguous entities as variants or products based on context.
    
    Examples:
        - "red **rice**" → rice is PRODUCT (variant precedes it)
        - "nike air **force**" → force is VARIANT (follows product)
    
    Returns two lists:
      - variants: entities classified as variants
      - products: entities classified as products
      
    NOTE: Matches semantics/nlp_processor.py lines 924-1003 exactly
    """
    classifier = EntityClassifier(entities)
    return classifier.classify_variants(sentence, entity_list, ambiguous_variants, debug)


def classify_ambiguous_brands(doc, entity_list: List[str], ambiguous_brands: Set[str], entities: List[Dict], debug: bool = False) -> Dict:
    """
    Classify ambiguous entities as BRAND or PRODUCT based on context.
    Uses spaCy POS tagging and already-tokenized entities.
    
    Examples:
        - "**dove** soap" → dove is BRAND (followed by product)
        - "add **dove**" → dove is PRODUCT (standalone)
    
    Args:
        doc: spaCy Doc object (for POS tagging)
        entity_list: List of ambiguous entity strings
        ambiguous_brands: Set of known ambiguous brand terms
        entities: Full entity catalog
        debug: Enable debug logging
    
    Returns:
        Dict with "brands" and "products" lists
        
    NOTE: Matches semantics/nlp_processor.py lines 1005-1068 exactly
    """
    classifier = EntityClassifier(entities)
    return classifier.classify_brands(doc, entity_list, ambiguous_brands, debug)

