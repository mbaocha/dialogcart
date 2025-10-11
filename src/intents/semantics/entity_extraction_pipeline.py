#!/usr/bin/env python3
"""
Entity extraction pipeline that combines:
1. NLP processing and parameterization (nlp_processor.py)
2. NER inference (ner_inference.py) 
3. Entity grouping processing (entity_grouping.py)

Returns structured extracted entities from natural language sentences.
"""

import os
import sys
from typing import Dict, List, Any


# ===== LOGGING CONFIGURATION =====
# Set DEBUG_NLP=1 in environment to enable debug logs
DEBUG_ENABLED = os.environ.get("DEBUG_NLP", "0") == "1"

def debug_print(*args, **kwargs):
    """Print debug message only if DEBUG_ENABLED is True"""
    if DEBUG_ENABLED:
        print(*args, **kwargs)

# Add the current directory to the path to import local modules
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

from nlp_processor import (
    init_nlp_with_entities, 
    extract_entities_with_parameterization
)
from ner_inference import process_text
from entity_grouping import decide_processing_path, index_parameterized_tokens
from llm_extractor import extract_cart_and_check_entities


# Global model cache
_nlp_model = None
_entities_data = None


def get_cached_models(force_reload=False):
    """Get cached NLP model and entities data, loading them if not already cached."""
    global _nlp_model, _entities_data
    
    if force_reload or _nlp_model is None or _entities_data is None:
        debug_print("[INFO] Loading NLP models (first time only)...")
        _nlp_model, _entities_data = init_nlp_with_entities()
        debug_print("[INFO] NLP models loaded and cached!")
    
    return _nlp_model, _entities_data


def warmup_models():
    """Preload models to avoid first-request delay."""
    debug_print("[INFO] Warming up models...")
    get_cached_models()
    # Also warm up HR inference by processing a dummy sentence
    process_text("add producttoken to cart")
    debug_print("[INFO] Models warmed up successfully!")


def map_tokens_to_original_values0(grouped_result: Dict[str, Any], nlp_result: Dict[str, Any], index_map: Dict[str, str] = None) -> Dict[str, Any]:
    """Map parameterized tokens back to their original values with LLM routing for edge cases."""
    
    # Get original entity lists
    nlp_products = nlp_result.get("products", [])
    nlp_brands = nlp_result.get("brands", [])
    nlp_units = nlp_result.get("units", [])
    nlp_variants = nlp_result.get("variants", [])
    
    # Create entity pools for mapping
    product_pool = nlp_products.copy()
    brand_pool = nlp_brands.copy()
    unit_pool = nlp_units.copy()
    variant_pool = nlp_variants.copy()
    
    # Check for edge cases that should route to LLM
    llm_routing_notes = []
    
    # Check for entity pool depletion
    # Count only parameterized placeholders (e.g., producttoken_1, producttoken_2)
    total_products_needed = len({
        token
        for group in grouped_result.get("groups", [])
        for token in group.get("products", [])
        if isinstance(token, str) and token.startswith("producttoken_")
    })


    total_brands_needed = len({
        token
        for group in grouped_result.get("groups", [])
        for token in group.get("brands", [])
        if isinstance(token, str) and token.startswith("brandtoken_")
    })

    total_units_needed = len({
        token
        for group in grouped_result.get("groups", [])
        for token in group.get("units", [])
        if isinstance(token, str) and token.startswith("unittoken_")
    })


    
    if total_products_needed > len(nlp_products):
        llm_routing_notes.append(f"More product tokens ({total_products_needed}) than extracted products ({len(nlp_products)})")
    if total_brands_needed > len(nlp_brands):
        llm_routing_notes.append(f"More brand tokens ({total_brands_needed}) than extracted brands ({len(nlp_brands)})")
    if total_units_needed > len(nlp_units):
        llm_routing_notes.append(f"More unit tokens ({total_units_needed}) than extracted units ({len(nlp_units)})")
    
    # Check for index gaps
    if index_map:
        indexed_tokens = [token for token in index_map.keys() if '_' in token]
        for base_type in ["producttoken", "brandtoken", "unittoken", "varianttoken"]:
            type_tokens = [t for t in indexed_tokens if t.startswith(base_type)]
            if type_tokens:
                indices = []
                for token in type_tokens:
                    try:
                        idx = int(token.split('_')[-1])
                        indices.append(idx)
                    except ValueError:
                        continue
                if indices:
                    indices.sort()
                    # Check for gaps (e.g., [1, 3] has gap at 2)
                    for gap_idx in range(1, len(indices)):
                        if indices[gap_idx] - indices[gap_idx-1] > 1:
                            llm_routing_notes.append(f"Index gap detected in {base_type}: {indices}")
                            break
    
    # Check for mixed token types in groups
    for group_idx, entity_group in enumerate(grouped_result.get("groups", [])):
        has_indexed = False
        has_non_indexed = False
        
        for token_list in [entity_group.get("products", []), entity_group.get("brands", []), entity_group.get("units", []), entity_group.get("variants", [])]:
            for token in token_list:
                if '_' in token and token in (index_map or {}):
                    has_indexed = True
                elif token in ["producttoken", "brandtoken", "unittoken", "varianttoken"]:
                    has_non_indexed = True
        
        if has_indexed and has_non_indexed:
            llm_routing_notes.append(f"Group {group_idx+1} has mixed indexed and non-indexed tokens")
    
    # Apply mapping to all groups
    if grouped_result.get("groups"):
        for entity_group in grouped_result["groups"]:
            # Map products
            if "products" in entity_group:
                entity_group["products"] = [
                    _map_indexed_token(token, "producttoken", product_pool, index_map)
                    for token in entity_group["products"]
                ]
            
            # Map brands
            if "brands" in entity_group:
                entity_group["brands"] = [
                    _map_indexed_token(token, "brandtoken", brand_pool, index_map)
                    for token in entity_group["brands"]
                ]
            
            # Map units
            if "units" in entity_group:
                entity_group["units"] = [
                    _map_indexed_token(token, "unittoken", unit_pool, index_map)
                    for token in entity_group["units"]
                ]
            
            # Map variants (variants are stored in "variants" field, not "tokens")
            if "variants" in entity_group:
                entity_group["variants"] = [
                    _map_indexed_token(token, "varianttoken", variant_pool, index_map)
                    for token in entity_group["variants"]
                ]
    
    # Update status if LLM routing is needed
    if llm_routing_notes:
        grouped_result["status"] = "needs_llm_fix"
        grouped_result["notes"] = grouped_result.get("notes", []) + llm_routing_notes
    
    return grouped_result


def _map_indexed_token0(token: str, base_type: str, entity_pool: List[str], index_map: Dict[str, str] = None) -> str:
    """Map an indexed token back to its original value using proper index-based mapping."""
    # Handle indexed tokens (e.g., producttoken_1, producttoken_2)
    if index_map and token in index_map and index_map[token] == base_type:
        # Extract the index number from the token (e.g., "producttoken_2" -> 2)
        try:
            index = int(token.split('_')[-1]) - 1  # Convert to 0-based index
            if 0 <= index < len(entity_pool):
                return entity_pool[index]  # Don't pop, just return the value
        except (ValueError, IndexError):
            pass
        # Fallback to first item if index extraction fails
        if entity_pool:
            return entity_pool[0]  # Don't pop, just return the first item
    
    # Handle regular tokens (e.g., producttoken) - fallback for non-indexed tokens
    if token == base_type and entity_pool:
        return entity_pool[0]  # Don't pop, just return the first item
    
    # Return token as-is if no mapping found
    return token

def map_tokens_to_original_values(grouped_result: Dict[str, Any], nlp_result: Dict[str, Any], index_map: Dict[str, str] = None) -> Dict[str, Any]:
    """Map parameterized tokens back to their original values with LLM routing for edge cases."""
    import json

    def dbg(msg, data=None):
        debug_print(f"[DEBUG] {msg}")
        if data is not None:
            try:
                debug_print(json.dumps(data, indent=2))
            except TypeError:
                debug_print(data)
        debug_print("-" * 60)

    # --- Step 1: Extract raw NLP results ---
    nlp_products = nlp_result.get("products", [])
    nlp_brands = nlp_result.get("brands", [])
    nlp_units = nlp_result.get("units", [])
    nlp_variants = nlp_result.get("variants", [])

    # Create entity pools (copy ensures safe reads)
    product_pool = nlp_products.copy()
    brand_pool = nlp_brands.copy()
    unit_pool = nlp_units.copy()
    variant_pool = nlp_variants.copy()

    dbg("map_tokens_to_original_values INPUT", {
        "nlp_products": nlp_products,
        "nlp_brands": nlp_brands,
        "nlp_units": nlp_units,
        "nlp_variants": nlp_variants
    })

    # --- Step 2: Iterate over groups ---
    if grouped_result.get("groups"):
        for gi, entity_group in enumerate(grouped_result["groups"]):
            dbg(f"Mapping group {gi+1} (before)", entity_group)

            # Map products
            if "products" in entity_group:
                entity_group["products"] = [
                    _map_indexed_token(token, "producttoken", product_pool, index_map)
                    for token in entity_group["products"]
                ]

            # Map brands
            if "brands" in entity_group:
                entity_group["brands"] = [
                    _map_indexed_token(token, "brandtoken", brand_pool, index_map)
                    for token in entity_group["brands"]
                ]

            # Map units
            if "units" in entity_group:
                entity_group["units"] = [
                    _map_indexed_token(token, "unittoken", unit_pool, index_map)
                    for token in entity_group["units"]
                ]

            # Map variants
            if "variants" in entity_group:
                entity_group["variants"] = [
                    _map_indexed_token(token, "varianttoken", variant_pool, index_map)
                    for token in entity_group["variants"]
                ]

            dbg(f"Mapping group {gi+1} (after)", entity_group)

    return grouped_result


def _map_indexed_token(token: str, base_type: str, entity_pool: List[str], index_map: Dict[str, str] = None) -> str:
    """
    Map an indexed token back to its original value using index-based mapping.
    Fix: ensure suffix '_n' actually selects nth item from entity_pool if available.
    """
    import re

    # Handle indexed tokens (e.g., 'producttoken_2')
    m = re.match(rf"^{base_type}_(\d+)$", token)
    if m:
        idx = int(m.group(1)) - 1  # Convert 1-based -> 0-based
        if 0 <= idx < len(entity_pool):
            debug_print(f"[DEBUG] _map_indexed_token: {token} → {entity_pool[idx]}")
            return entity_pool[idx]
        elif entity_pool:
            debug_print(f"[WARN] _map_indexed_token: {token} index out of range, fallback to {entity_pool[0]}")
            return entity_pool[0]

    # Handle non-indexed base token (e.g., 'producttoken')
    if token == base_type and entity_pool:
        debug_print(f"[DEBUG] _map_indexed_token: {token} (non-indexed) → {entity_pool[0]}")
        return entity_pool[0]

    # Return as-is if no mapping found
    debug_print(f"[DEBUG] _map_indexed_token: {token} no match, return as-is")
    return token


def extract_entities(sentence: str) -> Dict[str, Any]:
    """Main entity extraction function."""
    
    extraction_result = {
        "original_sentence": sentence,
        "parameterized_sentence": "",
        "nlp_entities": {},
        "hr_entities": {},
        "grouped_entities": {},
        "status": "ok",
        "notes": []
    }
    memory_state = {
    "last_products": [],
    "last_brands": [],
    "last_action": None
    }

    
    try:
        # Step 1: Get cached NLP processor (loads only on first call)
        nlp, entities = get_cached_models()
        
        # Step 2: Extract entities and create parameterized sentence
        nlp_result = extract_entities_with_parameterization(nlp, sentence, entities)
        extraction_result["nlp_entities"] = nlp_result
        extraction_result["parameterized_sentence"] = nlp_result.get("psentence", "")
        
        if not extraction_result["parameterized_sentence"]:
            extraction_result["status"] = "error"
            extraction_result["notes"].append("No parameterized sentence generated")
            return extraction_result
        
        # Step 3: Process through HR inference
        hr_result = process_text(extraction_result["parameterized_sentence"])
        extraction_result["hr_entities"] = hr_result
        
        if not hr_result.get("tokens") or not hr_result.get("labels"):
            extraction_result["status"] = "error"
            extraction_result["notes"].append("No tokens or labels from HR inference")
            return extraction_result
        
        # Step 4: Index parameterized tokens before grouping
        indexed_tokens, index_map = index_parameterized_tokens(hr_result["tokens"])
        extraction_result["indexed_tokens"] = indexed_tokens
        extraction_result["index_map"] = index_map
        
        # Step 5: Process through grouping layer with indexed tokens
        grouped_result = decide_processing_path(indexed_tokens, hr_result["labels"], memory_state)
        # grouped_result = decide_processing_path(hr_result["tokens"], hr_result["labels"], memory_state)



        
        # Step 6: Map indexed tokens back to original values
        grouped_result = map_tokens_to_original_values(grouped_result, nlp_result, index_map)
        extraction_result["grouped_entities"] = grouped_result
        
        # Step 7: Determine final status
        if grouped_result.get("status") == "needs_llm_fix":
            extraction_result["status"] = "needs_llm_fix"
            extraction_result["notes"].extend(grouped_result.get("notes", []))
        elif grouped_result.get("groups"):
            extraction_result["status"] = "success"
        else:
            extraction_result["status"] = "no_entities_found"
            extraction_result["notes"].append("No entities could be extracted")
        
    except (ValueError, KeyError, ImportError, FileNotFoundError) as e:
        extraction_result["status"] = "error"
        extraction_result["notes"].append(f"Processing error: {str(e)}")
    
    return extraction_result


def extract_entities_simple(sentence: str) -> List[Dict[str, Any]]:
    """Simplified version that returns just the grouped entities."""
    extraction_result = extract_entities(sentence)
    
    if extraction_result["status"] == "success" and extraction_result["grouped_entities"].get("groups"):
        return extraction_result["grouped_entities"]["groups"]
    else:
        return []


def extract_entities_with_confidence(sentence: str) -> Dict[str, Any]:
    """Extract entities with confidence scores and processing details."""
    extraction_result = extract_entities(sentence)
    
    # Add confidence information
    if extraction_result["hr_entities"].get("scores"):
        extraction_result["confidence_scores"] = extraction_result["hr_entities"]["scores"]
    
    # Add processing summary
    extraction_result["summary"] = {
        "total_groups": len(extraction_result["grouped_entities"].get("groups", [])),
        "nlp_entities_found": len(extraction_result["nlp_entities"].get("products", [])) + 
                             len(extraction_result["nlp_entities"].get("brands", [])),
        "hr_tokens_processed": len(extraction_result["hr_entities"].get("tokens", [])),
        "processing_status": extraction_result["status"]
    }
    
    return extraction_result


def interactive_main():
    """Interactive mode for testing entity extraction."""
    print("Entity Extraction Pipeline - Interactive Mode")
    print("=" * 50)
    
    # Warm up models at startup
    warmup_models()
    
    print("Enter sentences to test entity extraction (type 'quit' to exit)")
    print()
    
    while True:
        try:
            sentence = input("Enter sentence: ").strip()
            
            if not sentence or sentence.lower() in ['quit', 'exit', 'q']:
                print("Goodbye!")
                break
            
            print(f"\nProcessing: {sentence}")
            print("-" * 40)
            
            result = extract_entities(sentence)
            
            print(f"Status: {result['status']}")
            print(f"Parameterized: {result['parameterized_sentence']}")
            
            if result.get('indexed_tokens'):
                print(f"Indexed tokens: {result['indexed_tokens']}")
            
            if result['grouped_entities'].get('groups'):
                print("\nExtracted Groups:")
                for i, group in enumerate(result['grouped_entities']['groups']):
                    print(f"  Group {i+1}:")
                    print(f"    Action: {group.get('action', 'None')}")
                    print(f"    Intent: {group.get('intent', 'None')}")
                    print(f"    Products: {group.get('products', [])}")
                    print(f"    Brands: {group.get('brands', [])}")
                    print(f"    Quantities: {group.get('quantities', [])}")
                    print(f"    Units: {group.get('units', [])}")
                    print(f"    Variants: {group.get('variants', [])}")
            else:
                print("No groups extracted")
            
            if result.get('notes'):
                print(f"\nNotes: {result['notes']}")
            
            print("\n" + "=" * 50)
            
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except (ValueError, KeyError, ImportError, FileNotFoundError) as e:
            print(f"Error: {e}")
            print("Please try again.\n")


def extract_entities_with_llm_fallback(sentence: str) -> Dict[str, Any]:
    """
    Enhanced entity extraction with LLM fallback for needs_llm_fix cases.
    
    This function runs the standard pipeline first, and if the result indicates
    that LLM processing is needed (status == "needs_llm_fix"), it automatically
    routes to the LLM extractor as a fallback.
    
    Args:
        sentence: Input sentence to process
        
    Returns:
        Enhanced extraction result with LLM fallback if needed
    """
    # Run the standard pipeline first
    extraction_result = extract_entities(sentence)
    
    # If the pipeline indicates LLM processing is needed, route to LLM
    if extraction_result["status"] == "needs_llm_fix":
        debug_print("[INFO] Routing to LLM for complex extraction...")
        
        try:
            # Use the LLM extractor as fallback
            llm_result = extract_cart_and_check_entities(sentence)
            
            # Merge LLM results into the extraction result
            extraction_result["llm_fallback"] = {
                "used": True,
                "llm_result": llm_result,
                "elapsed_seconds": llm_result.get("elapsed_seconds", 0)
            }
            
            # Update the main result with LLM findings
            if llm_result.get("status") == "success" and llm_result.get("groups"):
                # Replace the grouped entities with LLM results
                extraction_result["grouped_entities"] = {
                    "groups": llm_result["groups"],
                    "status": "success",
                    "notes": llm_result.get("notes", [])
                }
                extraction_result["status"] = "success"
                extraction_result["notes"].append("LLM fallback successful")
            else:
                extraction_result["status"] = "error"
                extraction_result["notes"].append(f"LLM fallback failed: {llm_result.get('reason', 'Unknown error')}")
                
        except (ValueError, KeyError, ImportError, FileNotFoundError, ConnectionError) as e:
            extraction_result["llm_fallback"] = {
                "used": True,
                "error": str(e)
            }
            extraction_result["status"] = "error"
            extraction_result["notes"].append(f"LLM fallback error: {str(e)}")
    
    return extraction_result


def extract_entities_smart(sentence: str, use_llm_fallback: bool = True) -> Dict[str, Any]:
    """
    Smart entity extraction that automatically uses LLM fallback when needed.
    
    This is the recommended function for production use as it provides
    intelligent fallback to LLM processing for complex cases while
    maintaining high performance for simple cases.
    
    Args:
        sentence: Input sentence to process
        use_llm_fallback: Whether to use LLM fallback for needs_llm_fix cases
    
    Returns:
        Enhanced extraction result with LLM fallback if needed
    """
    if use_llm_fallback:
        return extract_entities_with_llm_fallback(sentence)
    else:
        return extract_entities(sentence)


def run_test_examples():
    """Run predefined test examples."""
    test_sentences = [
        "Add 2 bags of rice and 1 Gucci bag",
        "Remove 3 bottles of Coca Cola and add 5 packs of Indomie noodles",
        "Please check if you have Dangote sugar in stock",
        "I want to buy 1 kg of beans and 2 cartons of milk",
        "Cancel my order of 4 pairs of Nike shoes"
    ]
    
    print("Entity Extraction Pipeline - Test Examples")
    print("=" * 50)
    
    # Warm up models at startup
    warmup_models()
    
    for i, sentence in enumerate(test_sentences, 1):
        print(f"\nTest {i}: {sentence}")
        print("-" * 30)
        
        result = extract_entities(sentence)
        
        print(f"Status: {result['status']}")
        print(f"Parameterized: {result['parameterized_sentence']}")
        
        if result['grouped_entities'].get('groups'):
            print("Extracted Groups:")
            for j, group in enumerate(result['grouped_entities']['groups']):
                print(f"  Group {j+1}: {group}")
        else:
            print("No groups extracted")
        
        if result.get('notes'):
            print(f"Notes: {result['notes']}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        run_test_examples()
    else:
        interactive_main()