#!/usr/bin/env python3
"""
Debug script to troubleshoot NER response issues.
Specifically designed to debug why the second entity isn't being extracted.
"""

import sys
import os

# Add the current directory to the path so we can import the modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from extract_entities import (
    init_nlp_with_entities, 
    extract_entities_with_parameterization,
    map_actual_values_to_structured_entities
)
from ner_inference import process_text
from intent_mapper import IntentMapper


def debug_ner_response(sentence):
    """
    Debug function to show detailed NER response for troubleshooting
    """
    print(f"\n{'='*80}")
    print(f"DEBUG NER RESPONSE FOR: {sentence}")
    print(f"{'='*80}")
    
    # Step 1: NLP Processor
    print("\n1. NLP PROCESSOR STEP")
    print("-" * 40)
    nlp, entities = init_nlp_with_entities()
    nlp_result = extract_entities_with_parameterization(nlp, sentence, entities, debug_units=True)
    
    print("NLP Processor Results:")
    print(f"  Brands: {nlp_result.get('brands', [])}")
    print(f"  Products: {nlp_result.get('products', [])}")
    print(f"  Units: {nlp_result.get('units', [])}")
    print(f"  Quantities: {nlp_result.get('quantities', [])}")
    print(f"  Variants: {nlp_result.get('variants', [])}")
    print(f"  Parameterized sentence: '{nlp_result.get('psentence', '')}'")
    
    # Step 2: HR Inference
    print("\n2. HR INFERENCE STEP")
    print("-" * 40)
    parameterized_sentence = nlp_result.get("psentence", "")
    intent_mapper = IntentMapper()
    hr_result = process_text(parameterized_sentence, intent_mapper=intent_mapper)
    
    print("HR Inference Raw Results:")
    print(f"  Tokens: {hr_result.get('tokens', [])}")
    print(f"  Labels: {hr_result.get('labels', [])}")
    print(f"  Scores: {hr_result.get('scores', [])}")
    
    # Show token-label pairs for easier debugging
    print("\nToken-Label Pairs:")
    tokens = hr_result.get('tokens', [])
    labels = hr_result.get('labels', [])
    for i, (token, label) in enumerate(zip(tokens, labels)):
        print(f"  {i:2d}: '{token}' -> {label}")
    
    # Step 3: Grouping
    print("\n3. GROUPING STEP")
    print("-" * 40)
    from extract_entities import group_entities_action_centric
    intent_mapper = IntentMapper()

    structured = group_entities_action_centric(
        hr_result.get("tokens", []),
        hr_result.get("labels", []),
        hr_result.get("scores", []),
        intent_mapper=intent_mapper
    )

    if not structured:
        print("⚠️  No entities grouped. Possibly a labeling or token alignment issue.")
    else:
        print("Grouped Entities (before value mapping):")
        for i, entity in enumerate(structured):
            print(f"  {i}: {entity}")
    
    # Step 4: Value Mapping
    print("\n4. VALUE MAPPING STEP")
    print("-" * 40)
    mapped_entities = map_actual_values_to_structured_entities(structured, nlp_result)
    print("Final Mapped Entities:")
    for i, entity in enumerate(mapped_entities):
        print(f"  {i}: {entity}")
    
    print(f"\n{'='*80}")
    return {
        'nlp_result': nlp_result,
        'hr_result': hr_result,
        'structured': structured,
        'mapped_entities': mapped_entities
    }


def test_problematic_cases():
    """
    Test the problematic cases to understand what's happening
    """
    test_cases = [
        "Add 5kg of Dangote rice and 2 bottles of Coca Cola",
        "Add 2 bags of rice and 1 Gucci bag",
        "Remove 3 cartons of Indomie noodles",
        "Do you have yam or rice in stock"
    ]
    
    for i, sentence in enumerate(test_cases, 1):
        print(f"\n{'#'*100}")
        print(f"TEST CASE {i}: {sentence}")
        print(f"{'#'*100}")
        
        result = debug_ner_response(sentence)
        
        # Summary
        print(f"\nSUMMARY FOR TEST {i}:")
        print(f"  NLP extracted {len(result['nlp_result'].get('products', []))} products")
        print(f"  HR inference created {len(result['structured'])} entities")
        print(f"  Final result has {len(result['mapped_entities'])} entities")
        
        if len(result['mapped_entities']) == 0:
            print("  ⚠️  NO ENTITIES EXTRACTED!")
        elif len(result['mapped_entities']) == 1:
            print("  ⚠️  ONLY ONE ENTITY EXTRACTED (might be missing second entity)")
        else:
            print("  ✅ MULTIPLE ENTITIES EXTRACTED")


def analyze_specific_issue():
    """
    Deep dive into the specific issue with Test 3
    """
    sentence = "Add 5kg of Dangote rice and 2 bottles of Coca Cola"
    
    print(f"\n{'#'*100}")
    print("DEEP DIVE ANALYSIS: Why is the second entity missing?")
    print(f"{'#'*100}")
    
    result = debug_ner_response(sentence)
    
    # Analyze the parameterized sentence
    param_sentence = result['nlp_result'].get('psentence', '')
    print(f"\nANALYSIS:")
    print(f"Parameterized sentence: '{param_sentence}'")
    
    # Check if "Coca Cola" was properly parameterized
    if 'brandtoken' in param_sentence:
        print("✅ Brand tokenization detected")
    else:
        print("❌ No brand tokenization - this might be the issue!")
    
    # Check if "bottles" was properly parameterized
    if 'unittoken' in param_sentence:
        print("✅ Unit tokenization detected")
    else:
        print("❌ No unit tokenization - this might be the issue!")
    
    # Check HR inference tokens
    tokens = result['hr_result'].get('tokens', [])
    labels = result['hr_result'].get('labels', [])
    
    print(f"\nHR Inference Analysis:")
    print(f"Total tokens: {len(tokens)}")
    print(f"Total labels: {len(labels)}")
    
    # Look for specific patterns
    brand_count = sum(1 for label in labels if 'BRAND' in label)
    product_count = sum(1 for label in labels if 'PRODUCT' in label)
    unit_count = sum(1 for label in labels if 'UNIT' in label)
    quantity_count = sum(1 for label in labels if 'QUANTITY' in label)
    
    print(f"Brand labels: {brand_count}")
    print(f"Product labels: {product_count}")
    print(f"Unit labels: {unit_count}")
    print(f"Quantity labels: {quantity_count}")
    
    # Expected vs actual
    print(f"\nEXPECTED vs ACTUAL:")
    print(f"Expected: 2 products (rice, Coca Cola), 2 brands (Dangote, Coca Cola), 2 units (kg, bottles), 2 quantities (5, 2)")
    print(f"Actual: {product_count} products, {brand_count} brands, {unit_count} units, {quantity_count} quantities")


if __name__ == "__main__":
    print("NER DEBUGGING SCRIPT")
    print("=" * 80)
    
    # Run the specific analysis first
    analyze_specific_issue()
    
    # Then run all test cases
    test_problematic_cases()
    
    print(f"\n{'='*80}")
    print("DEBUGGING COMPLETE")
    print("=" * 80)
