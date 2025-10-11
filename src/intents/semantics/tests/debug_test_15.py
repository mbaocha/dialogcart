#!/usr/bin/env python3
"""
Debug test file for Test Case 15: "Include 4 pairs of underwear and delete 1 pair of shorts"
This file helps isolate and debug the specific issues with entity extraction and mapping.
"""

from nlp_processor import extract_entities_with_parameterization, init_nlp_with_entities
from ner_inference import process_text
from intent_mapper import IntentMapper
from extract_entities import extract_entities_from_sentence


def debug_test_case_15():
    """
    Debug the specific test case that's failing.
    """
    print("=" * 80)
    print("DEBUGGING TEST CASE 15")
    print("=" * 80)
    
    # The problematic sentence
    sentence = "Include 4 pairs of underwear and delete 1 pair of shorts"
    print(f"Input sentence: {sentence}")
    print()
    
    # Step 1: Initialize NLP and entities
    print("Step 1: Initializing NLP and entities...")
    nlp, entities = init_nlp_with_entities()
    print("✅ NLP and entities initialized")
    print()
    
    # Step 2: Extract entities and get parameterized sentence
    print("Step 2: Extracting entities with parameterization...")
    
    # Debug: Check if "pairs" and "pair" are in the entities
    print("Debug: Checking if 'pairs' and 'pair' are in entities...")
    for ent in entities:
        if "pair" in ent.get("canonical", "").lower() or any("pair" in s.lower() for s in ent.get("synonyms", [])):
            print(f"  Found pair entity: {ent}")
    
    # Debug: Check tokenization
    print("Debug: Checking tokenization...")
    doc = nlp(sentence)
    for i, token in enumerate(doc):
        print(f"  Token {i}: '{token.text}' (pos: {token.pos_}, lemma: {token.lemma_})")
    
    # Debug: Check quantity extraction logic
    print("Debug: Checking quantity extraction logic...")
    for i, token in enumerate(doc):
        if token.lemma_.lower() in ["pair", "pairs"]:
            prev_token = doc[i-1] if i > 0 else None
            print(f"  Found unit '{token.text}' at position {i}")
            print(f"    Previous token: {prev_token.text if prev_token else 'None'} (pos: {prev_token.pos_ if prev_token else 'None'})")
            print(f"    Should extract quantity: {prev_token.pos_ == 'NUM' if prev_token else False}")
    
    # Test the fixed function
    print("Testing the fixed extract_entities_from_sentence function...")
    result = extract_entities_from_sentence(sentence, debug_units=True)
    
    print("\n=== FIXED FUNCTION RESULTS ===")
    print(f"Brands: {result.get('brands', [])}")
    print(f"Products: {result.get('products', [])}")
    print(f"Units: {result.get('units', [])}")
    print(f"Quantities: {result.get('quantities', [])}")
    print(f"Structured entities: {result.get('structured_entities', [])}")
    print()
    
    # Also test the original function for comparison
    nlp_result = extract_entities_with_parameterization(nlp, sentence, entities, debug_units=True)
    
    print("\nNLP Processor Results:")
    print(f"  Brands: {nlp_result.get('brands', [])}")
    print(f"  Products: {nlp_result.get('products', [])}")
    print(f"  Units: {nlp_result.get('units', [])}")
    print(f"  Variants: {nlp_result.get('variants', [])}")
    print(f"  Quantities: {nlp_result.get('quantities', [])}")
    print(f"  Original sentence: {nlp_result.get('osentence', '')}")
    print(f"  Parameterized sentence: {nlp_result.get('psentence', '')}")
    print()
    
    # Step 3: Process the parameterized sentence with ner_inference
    print("Step 3: Processing parameterized sentence with HR inference...")
    parameterized_sentence = nlp_result.get("psentence", "")
    print(f"Parameterized sentence: '{parameterized_sentence}'")
    
    intent_mapper = IntentMapper()
    print("✅ IntentMapper initialized")
    
    hr_result = process_text(parameterized_sentence, intent_mapper=intent_mapper)
    
    print("\nHR Inference Results:")
    print(f"  Tokens: {hr_result.get('tokens', [])}")
    print(f"  Labels: {hr_result.get('labels', [])}")
    print(f"  Scores: {[f'{s:.2f}' for s in hr_result.get('scores', [])]}")
    print()
    
    print("Raw structured entities from HR inference:")
    structured_entities = hr_result.get('structured', [])
    for i, entity in enumerate(structured_entities):
        print(f"  Entity {i}: {entity}")
    print()
    
    # Step 4: Test intent mapping separately
    print("Step 4: Testing intent mapping...")
    test_actions = ["include", "delete", "add", "remove"]
    for action in test_actions:
        intent, confidence = intent_mapper.map_action_to_intent(action)
        print(f"  '{action}' → intent: {intent}, confidence: {confidence:.3f}")
    print()
    
    # Step 5: Map actual values back to structured entities
    print("Step 5: Mapping actual values back to structured entities...")
    mapped_entities = map_actual_values_to_structured_entities(structured_entities, nlp_result)
    
    print("Mapped entities:")
    for i, entity in enumerate(mapped_entities):
        print(f"  Entity {i}: {entity}")
    print()
    
    # Step 6: Analysis
    print("Step 6: Analysis...")
    print(f"Expected brands: []")
    print(f"Expected products: ['underwear', 'shorts']")
    print(f"Expected units: ['pairs']")
    print(f"Expected quantities: ['4', '1']")
    print()
    
    print("Actual results:")
    print(f"  Brands: {nlp_result.get('brands', [])}")
    print(f"  Products: {nlp_result.get('products', [])}")
    print(f"  Units: {nlp_result.get('units', [])}")
    print(f"  Quantities: {nlp_result.get('quantities', [])}")
    print()
    
    # Check for issues
    issues = []
    
    # Check if we have the right number of structured entities
    if len(structured_entities) > 2:
        issues.append(f"Too many structured entities: {len(structured_entities)} (expected 2)")
    
    # Check if entities have proper intents
    entities_without_intent = [i for i, e in enumerate(mapped_entities) if e.get('intent') is None]
    if entities_without_intent:
        issues.append(f"Entities without intent: {entities_without_intent}")
    
    # Check if we have parameterized tokens still present
    has_parameterized_tokens = any(
        e.get('brand') == 'brandtoken' or 
        e.get('product') == 'producttoken' or 
        e.get('product') == 'unittoken' or
        e.get('unit') == 'unittoken'
        for e in mapped_entities
    )
    if has_parameterized_tokens:
        issues.append("Still contains parameterized tokens")
    
    if issues:
        print("🚨 ISSUES FOUND:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("✅ No major issues found")
    
    return {
        'nlp_result': nlp_result,
        'hr_result': hr_result,
        'mapped_entities': mapped_entities,
        'issues': issues
    }


def map_actual_values_to_structured_entities(structured_entities, nlp_result):
    """
    Map actual extracted values back to structured entities, replacing parameterized tokens
    with the real entity values. Uses a more intelligent mapping approach.
    """
    # Get all available entities
    brands = nlp_result.get("brands", [])
    products = nlp_result.get("products", [])
    units = nlp_result.get("units", [])
    variants = nlp_result.get("variants", [])
    quantities = nlp_result.get("quantities", [])
    
    print(f"Available entities for mapping:")
    print(f"  Brands: {brands}")
    print(f"  Products: {products}")
    print(f"  Units: {units}")
    print(f"  Variants: {variants}")
    print(f"  Quantities: {quantities}")
    print()
    
    # Create counters to track usage
    brand_counter = 0
    product_counter = 0
    unit_counter = 0
    variant_counter = 0
    quantity_counter = 0
    
    # Apply mapping to structured entities
    mapped_entities = []
    for i, entity in enumerate(structured_entities):
        print(f"Mapping entity {i}: {entity}")
        mapped_entity = entity.copy()
        
        # Map brand - use next available brand
        if entity.get("brand") == "brandtoken" and brand_counter < len(brands):
            mapped_entity["brand"] = brands[brand_counter]
            brand_counter += 1
            print(f"  → Mapped brand: {mapped_entity['brand']}")
        
        # Map product - use next available product
        if entity.get("product") == "producttoken" and product_counter < len(products):
            mapped_entity["product"] = products[product_counter]
            product_counter += 1
            print(f"  → Mapped product: {mapped_entity['product']}")
        elif entity.get("product") == "unittoken" and product_counter < len(products):
            # Handle case where unit is misclassified as product
            mapped_entity["product"] = products[product_counter]
            product_counter += 1
            print(f"  → Mapped misclassified unit as product: {mapped_entity['product']}")
        
        # Map unit - use next available unit
        if entity.get("unit") == "unittoken" and unit_counter < len(units):
            mapped_entity["unit"] = units[unit_counter]
            unit_counter += 1
            print(f"  → Mapped unit: {mapped_entity['unit']}")
        
        # Map variant - use next available variant
        if entity.get("variant") == "varianttoken" and variant_counter < len(variants):
            mapped_entity["variant"] = variants[variant_counter]
            variant_counter += 1
            print(f"  → Mapped variant: {mapped_entity['variant']}")
        
        # Map quantity - use next available quantity
        if entity.get("quantity") and entity.get("quantity") != "unittoken" and quantity_counter < len(quantities):
            mapped_entity["quantity"] = quantities[quantity_counter]
            quantity_counter += 1
            print(f"  → Mapped quantity: {mapped_entity['quantity']}")
        
        # Special handling for entities that have both product and unit issues
        if entity.get("product") == "unittoken" and entity.get("unit") is None and unit_counter < len(units):
            # This is likely a unit that was misclassified as product
            mapped_entity["product"] = None
            mapped_entity["unit"] = units[unit_counter]
            unit_counter += 1
            print(f"  → Fixed misclassified unit: {mapped_entity['unit']}")
        
        print(f"  Final mapped entity: {mapped_entity}")
        print()
        mapped_entities.append(mapped_entity)
    
    return mapped_entities


def test_parameterized_sentence_processing():
    """
    Test what happens when we process the parameterized sentence directly.
    """
    print("=" * 80)
    print("TESTING PARAMETERIZED SENTENCE PROCESSING")
    print("=" * 80)
    
    # The parameterized sentence from the failing test
    parameterized_sentence = "include 4 unittoken of producttoken and delete 1 unittoken of producttoken"
    print(f"Parameterized sentence: '{parameterized_sentence}'")
    print()
    
    intent_mapper = IntentMapper()
    hr_result = process_text(parameterized_sentence, intent_mapper=intent_mapper)
    
    print("HR Inference Results:")
    print(f"  Tokens: {hr_result.get('tokens', [])}")
    print(f"  Labels: {hr_result.get('labels', [])}")
    print(f"  Scores: {[f'{s:.2f}' for s in hr_result.get('scores', [])]}")
    print()
    
    print("Structured entities:")
    for i, entity in enumerate(hr_result.get('structured', [])):
        print(f"  Entity {i}: {entity}")
    print()
    
    # Show token-label pairs
    print("Token-Label pairs:")
    for token, label in zip(hr_result.get('tokens', []), hr_result.get('labels', [])):
        print(f"  '{token}' → {label}")
    print()


if __name__ == "__main__":
    # Run the main debug test
    debug_test_case_15()
    
    print("\n" + "=" * 80)
    print("ADDITIONAL DEBUGGING")
    print("=" * 80)
    
    # Test parameterized sentence processing separately
    test_parameterized_sentence_processing()
