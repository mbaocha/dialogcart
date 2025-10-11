"""
Entity loading and pattern building for entity extraction.

Handles:
- Loading entities from JSON files
- Building spaCy patterns
- Creating support maps (unit, variant, product, brand)
- Initializing spaCy with custom tokenizer and entity ruler
"""
import os
import json
import re
from typing import List, Dict, Any, Optional, Tuple, Set
from pathlib import Path

# Lazy imports for optional dependencies
try:
    import spacy
    from spacy.tokenizer import Tokenizer
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False
    spacy = None
    Tokenizer = None


# ===== LOGGING CONFIGURATION =====
DEBUG_ENABLED = os.environ.get("DEBUG_NLP", "0") == "1"


def debug_print(*args, **kwargs):
    """Print debug message only if DEBUG_ENABLED is True."""
    if DEBUG_ENABLED:
        print(*args, **kwargs)


def load_global_entities(entity_file: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Load global entities from local file.
    
    Args:
        entity_file: Optional custom path to entity JSON file.
                    If None, uses default luma/store/merged_v9.json
    
    Returns:
        List of entity dictionaries with structure:
        {
            "canonical": str,
            "type": list,
            "synonyms": list,
            "example": dict (optional)
        }
    
    NOTE: Matches semantics/nlp_processor.py lines 36-56 exactly
    """
    if entity_file is None:
        # Default to luma store
        current_dir = Path(__file__).resolve().parent.parent
        json_path = current_dir / "store" / "merged_v9.json"
    else:
        json_path = Path(entity_file)
    
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    entities = []
    for item in data:
        canonical = item.get("canonical")
        entity_type = item.get("type", [])
        synonyms = item.get("synonyms", [])
        if canonical and entity_type:
            entities.append({
                "canonical": canonical,
                "type": entity_type,   # ✅ type is now always a list
                "synonyms": synonyms,
                "example": item.get("example", {})  # ✅ keep example if present
            })
    return entities


def build_global_synonym_map(entities: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Build a synonym normalization map ONLY from entities explicitly marked
    with type=["global_synonym"] (and no other types).
    
    Args:
        entities: List of entity dictionaries
        
    Returns:
        Dictionary mapping synonyms to canonical forms
        
    NOTE: Matches semantics/nlp_processor.py lines 267-287 exactly
    """
    synonym_map = {}
    
    for ent in entities:
        types = ent.get("type", [])
        canonical = ent.get("canonical", "").lower().strip()
        synonyms = [s.lower().strip() for s in ent.get("synonyms", [])]
        
        # ✅ only include if type is exactly ["global_synonym"]
        if len(types) == 1 and types[0] == "global_synonym" and canonical:
            for syn in synonyms:
                synonym_map[syn] = canonical
            # include self mapping
            synonym_map[canonical] = canonical
    
    return synonym_map


def build_entity_patterns(entities: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Convert entities into spaCy ruler patterns for BRAND, PRODUCT, UNIT, VARIANT, NOISE, PRODUCTBRAND.
    - Single-type → direct label (BRAND, PRODUCT, etc.)
    - Multi-type brand+product → PRODUCTBRAND
    - All patterns sorted longest-first.
    
    Args:
        entities: List of entity dictionaries
        
    Returns:
        List of spaCy entity ruler patterns
        
    NOTE: Matches semantics/nlp_processor.py lines 174-203 exactly
    """
    patterns = []
    
    for ent in entities:
        types = ent.get("type", [])
        names = sorted(ent.get("synonyms", []), key=lambda x: -len(x.split()))
        
        # ✅ Case 1: brand+product combo → PRODUCTBRAND
        if "brand" in types and "product" in types:
            for name in names:
                patterns.append({"label": "PRODUCTBRAND", "pattern": name})
            continue  # don't double-add as BRAND/PRODUCT below
        
        # ✅ Case 2: skip other multi-type for context classification
        if len(types) > 1:
            continue
        
        # ✅ Case 3: regular single-type entity
        t = types[0]
        label = t.upper()  # brand → BRAND, etc.
        for name in names:
            patterns.append({"label": label, "pattern": name})
    
    return patterns


def build_support_maps(entities: List[Dict[str, Any]]) -> Tuple[
    Dict[str, str],  # unit_map
    Dict[str, str],  # variant_map
    Dict[str, str],  # product_map
    Dict[str, str],  # brand_map
    Set[str],        # noise_set
    Set[str],        # unambiguous_units
    Set[str],        # ambiguous_units
    Set[str],        # unambiguous_variants
    Set[str],        # ambiguous_variants
    Set[str]         # ambiguous_brands
]:
    """
    Build lookup maps for units, variants, products, and brands.
    Rule:
      - Single-type entities → go into canonical maps.
      - Multi-type entities → go only into ambiguous sets.
    
    Args:
        entities: List of entity dictionaries
        
    Returns:
        Tuple of (unit_map, variant_map, product_map, brand_map, noise_set,
                  unambiguous_units, ambiguous_units, unambiguous_variants, 
                  ambiguous_variants, ambiguous_brands)
        
    NOTE: Matches semantics/nlp_processor.py lines 205-264 exactly
    """
    unit_map, variant_map, product_map, brand_map, noise_set = {}, {}, {}, {}, set()
    unambiguous_units, ambiguous_units = set(), set()
    unambiguous_variants, ambiguous_variants = set(), set()
    ambiguous_brands = set()
    
    for ent in entities:
        canon = ent["canonical"].lower()
        synonyms = [s.lower() for s in ent.get("synonyms", [])]
        all_terms = [canon] + synonyms
        types = ent.get("type", [])
        
        # --- Single-type entities ---
        if len(types) == 1:
            t = types[0]
            if t == "unit":
                for term in all_terms:
                    unit_map[term] = canon
                unambiguous_units.update(all_terms)
            
            elif t == "variant":
                for term in all_terms:
                    variant_map[term] = canon
                unambiguous_variants.update(all_terms)
            
            elif t == "product":
                for term in all_terms:
                    product_map[term] = canon
            
            elif t == "brand":
                for term in all_terms:
                    brand_map[term] = canon
            
            elif t == "noise":
                noise_set.update(all_terms)
        
        # --- Multi-type entities (ambiguous) ---
        else:
            if "unit" in types:
                ambiguous_units.update(all_terms)
            if "variant" in types:
                ambiguous_variants.update(all_terms)
            if "brand" in types:
                ambiguous_brands.update(all_terms)
            # Note: products/brands in ambiguity don't get maps
            # they must be resolved by context classification only
    
    return (
        unit_map, variant_map, product_map, brand_map, noise_set,
        unambiguous_units, ambiguous_units,
        unambiguous_variants, ambiguous_variants,
        ambiguous_brands   # ✅ new
    )


def customize_tokenizer(nlp):
    """
    Custom tokenizer that:
      - ✅ Preserves internal hyphens (e.g., 'coca-cola' stays one token)
      - ✅ Still splits between digits and letters (e.g., '5kg' → '5', 'kg')
    
    Args:
        nlp: spaCy language model
        
    Returns:
        Custom Tokenizer instance
        
    NOTE: Matches semantics/nlp_processor.py lines 71-94 exactly
    """
    if not SPACY_AVAILABLE:
        raise ImportError("spaCy is required for custom tokenizer. Install with: pip install spacy")
    
    # Remove "-" from infix pattern so words like "coca-cola" are preserved
    infix_re = re.compile(r'''(?<=\d)(?=[a-zA-Z])|(?<=[a-zA-Z])(?=\d)''')
    
    # Keep the same prefix/suffix rules
    prefix_re = re.compile(r'''^[\[\("']''')
    suffix_re = re.compile(r'''[\]\)"']$''')
    
    return Tokenizer(
        nlp.vocab,
        rules=nlp.Defaults.tokenizer_exceptions,
        prefix_search=prefix_re.search,
        suffix_search=suffix_re.search,
        infix_finditer=infix_re.finditer,
        token_match=None
    )


def init_nlp_with_entities(entity_file: Optional[str] = None) -> Tuple[Any, List[Dict[str, Any]]]:
    """
    Initialize spaCy model with entity ruler.
    
    Args:
        entity_file: Optional custom path to entity JSON file
        
    Returns:
        Tuple of (nlp model, entities list)
        
    NOTE: Matches semantics/nlp_processor.py lines 98-107 exactly
    """
    if not SPACY_AVAILABLE:
        raise ImportError(
            "spaCy is required for NLP initialization. "
            "Install with: pip install spacy && python -m spacy download en_core_web_sm"
        )
    
    nlp = spacy.load("en_core_web_sm")
    nlp.tokenizer = customize_tokenizer(nlp)
    entities = load_global_entities(entity_file)
    patterns = build_entity_patterns(entities)
    
    ruler = nlp.add_pipe("entity_ruler", before="ner", config={"overwrite_ents": True})
    ruler.add_patterns(patterns)
    
    return nlp, entities

