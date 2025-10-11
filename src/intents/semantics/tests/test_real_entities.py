import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nlp_processor import init_nlp_with_entities, extract_entities_with_parameterization, extract_entities, normalize_text_for_tokenization

# Initialize NLP with entity ruler
print("Initializing spaCy NLP with entity ruler and global entities...")
nlp, entities = init_nlp_with_entities()
print(f"Loaded {len(entities)} global entities")

def run_test(test_num, description, text, expected_entities):
    """
    Run a test case and verify expected entities are detected.
    
    Args:
        test_num: Test case number
        description: Description of what is being tested
        text: Input text to test
        expected_entities: Dict with expected entity lists (e.g., {'brands': ['dangote'], 'products': ['rice']})
    """
    print(f"\n{'='*80}")
    print(f"TEST {test_num}: {description}")
    print(f"Input: '{text}'")
    print(f"{'-'*80}")
    
    # Extract entities using the spaCy entity ruler
    debug_enabled = test_num == 2  # Enable debug for test 2
    result = extract_entities_with_parameterization(nlp, text, entities, debug_units=debug_enabled)
    
    # Get the raw result with positions before simplification
    raw_result = extract_entities(nlp, normalize_text_for_tokenization(text), entities, debug_units=False)
    
    # Display raw extracted entities with positions
    print("Raw Extracted Entities (with positions):")
    print(raw_result)
    
    # Display extracted entities and parameterized sentence
    print("\nSimplified Entities:")
    print(f"  Brands:          {result.get('brands', [])}")
    print(f"  Products:        {result.get('products', [])}")
    print(f"  Units:           {result.get('units', [])}")
    print(f"  Variants:        {result.get('variants', [])}")
    print(f"  Quantities:      {result.get('quantities', [])}")
    print(f"  Likely Brands:   {result.get('likely_brands', [])}")
    print(f"  Likely Products: {result.get('likely_products', [])}")
    print(f"  Parameterized:   {result.get('psentence', '')}")
    
    # Verify expected entities
    print(f"{'-'*80}")
    passed = True
    
    for entity_type, expected_list in expected_entities.items():
        actual_list = result.get(entity_type, [])
        
        # Normalize lists for comparison (lowercase)
        expected_set = set([e.lower() for e in expected_list])
        actual_set = set([e.lower() for e in actual_list])
        
        # Check if all expected entities are found
        for expected_entity in expected_set:
            if expected_entity in actual_set:
                print(f"  ✓ PASS: {entity_type} contains '{expected_entity}'")
            else:
                print(f"  ✗ FAIL: {entity_type} missing '{expected_entity}' (got: {actual_list})")
                passed = False
    
    print(f"{'='*80}")
    return passed


# Test Cases using REAL entities from merged_v2.json
all_passed = True

# GROCERY TESTS
# Test 1: Rice with Dangote brand
all_passed &= run_test(
    1,
    "Grocery: Rice with Dangote brand",
    "Add 5kg of Dangote rice to my cart",
    {'brands': ['dangote'], 'products': ['rice'], 'units': ['kg'], 'quantities': ['5']}
)

# Test 2: Indomie noodles with variants
all_passed &= run_test(
    2,
    "Grocery: Indomie noodles with flavor variants",
    "I want 3 packs of Indomie noodles but not the chicken flavor, add onion flavor instead",
    {'brands': ['indomie'], 'products': ['noodles'], 'units': ['pack'], 'variants': ['chicken', 'onion']}
)

# Test 3: Peak milk with size specification
all_passed &= run_test(
    3,
    "Grocery: Peak milk with size and type variants",
    "Get me 2 large Peak milk and 1 small Peak condensed milk",
    {'brands': ['peak'], 'products': ['milk'], 'variants': ['large', 'small']}
)

# Test 4: Coca Cola with temperature and quantity
all_passed &= run_test(
    4,
    "Grocery: Coca Cola with temperature specification",
    "Add 6 cold Coca Cola bottles and remove 2 warm ones",
    {'brands': ['coca-cola'], 'variants': ['cold', 'warm']}
)

# Test 5: Golden Penny rice comparison
all_passed &= run_test(
    5,
    "Grocery: Golden Penny rice comparison",
    "Show me Golden Penny rice and compare with Dangote rice prices",
    {'brands': ['golden penny', 'dangote'], 'products': ['rice']}
)

# Test 6: Multiple grocery products with brands
all_passed &= run_test(
    6,
    "Grocery: Multiple products with mixed brands",
    "I need Nestlé cereal, some beans, and 3 packs of Three Crowns milk",
    {'brands': ['nestlé', 'three crowns'], 'products': ['cereal', 'beans', 'milk'], 'units': ['pack']}
)

# Test 7: Fanta and Sprite with variants
all_passed &= run_test(
    7,
    "Grocery: Soft drinks with flavor variants",
    "Add 4 Fanta orange bottles and 2 Sprite lemon bottles",
    {'brands': ['fanta', 'sprite'], 'variants': ['orange', 'lemon']}
)

# Test 8: Honeywell flour with quantity units
all_passed &= run_test(
    8,
    "Grocery: Honeywell flour with complex quantities",
    "Get 10kg of Honeywell flour and 5kg of regular flour",
    {'brands': ['honeywell'], 'products': ['flour'], 'units': ['kg'], 'variants': ['regular']}
)

# BEAUTY TESTS
# Test 9: MAC lipstick with shade variants
all_passed &= run_test(
    9,
    "Beauty: MAC lipstick with shade specification",
    "Add 2 MAC lipsticks in red shade and 1 in nude shade",
    {'brands': ['mac'], 'products': ['lipstick'], 'variants': ['red', 'nude']}
)

# Test 10: Foundation comparison
all_passed &= run_test(
    10,
    "Beauty: Foundation comparison with brands",
    "Compare MAC foundation with Maybelline foundation for my skin tone",
    {'brands': ['mac', 'maybelline'], 'products': ['foundation']}
)

# Test 11: Lip gloss and lip balm
all_passed &= run_test(
    11,
    "Beauty: Multiple lip products",
    "I want 3 lip glosses and 2 lip balms from different brands",
    {'products': ['lip gloss', 'lip balm'], 'variants': ['different']}
)

# Test 12: Complex beauty order
all_passed &= run_test(
    12,
    "Beauty: Complex order with multiple products",
    "Add MAC Ruby Woo lipstick, Maybelline Fit Me foundation, and some concealer",
    {'brands': ['mac', 'maybelline'], 'products': ['concealer', 'lipstick', 'foundation']}
)

# MIXED CATEGORY TESTS
# Test 13: Grocery and Beauty mixed
all_passed &= run_test(
    13,
    "Mixed: Grocery and Beauty products",
    "Add 2kg Dangote rice, 1 MAC lipstick, and 3 Peak milk bottles",
    {'brands': ['dangote', 'mac', 'peak'], 'products': ['rice', 'lipstick', 'milk'], 'units': ['kg']}
)

# Test 14: Multi-intent with real entities
all_passed &= run_test(
    14,
    "Multi-intent: Add, remove, and compare with real entities",
    "Add 5kg Golden Penny rice, remove the Indomie noodles, and show me Coca Cola options",
    {'brands': ['golden penny', 'indomie', 'coca-cola'], 'products': ['rice', 'noodles'], 'units': ['kg']}
)

# Test 15: Complex grocery order with variants
all_passed &= run_test(
    15,
    "Complex: Grocery order with multiple variants",
    "Get 3 large Peak milk, 2kg Honeywell flour, and 6 cold Fanta bottles",
    {'brands': ['peak', 'honeywell', 'fanta'], 'products': ['milk', 'flour'], 'variants': ['large', 'cold'], 'units': ['kg']}
)

# Test 16: Subscription with real entities
all_passed &= run_test(
    16,
    "Subscription: Weekly delivery with real brands",
    "Subscribe to weekly delivery of 2kg Dangote rice and 4 Peak milk bottles",
    {'brands': ['dangote', 'peak'], 'products': ['rice', 'milk'], 'units': ['kg']}
)

# Test 17: Stock inquiry with variants
all_passed &= run_test(
    17,
    "Inquiry: Stock check with size variants",
    "Do you have large Peak milk and small Indomie noodles in stock?",
    {'brands': ['peak', 'indomie'], 'products': ['milk', 'noodles'], 'variants': ['large', 'small']}
)

# Test 18: Price comparison with real entities
all_passed &= run_test(
    18,
    "Price: Comparison between real brands",
    "What's the price of Golden Penny rice compared to Dangote rice?",
    {'brands': ['golden penny', 'dangote'], 'products': ['rice']}
)

# Test 19: Bulk order with conditions
all_passed &= run_test(
    19,
    "Bulk: Large order with specific conditions",
    "Add 20kg Dangote rice, 10 Peak milk bottles, but not the condensed milk",
    {'brands': ['dangote', 'peak'], 'products': ['rice', 'milk'], 'units': ['kg'], 'variants': ['condensed']}
)

# Test 20: Complex multi-intent with real entities
all_passed &= run_test(
    20,
    "Complex: Multi-intent with real grocery and beauty products",
    "Add 5kg Golden Penny rice, remove MAC lipstick, and show me Peak milk options",
    {'brands': ['golden penny', 'mac', 'peak'], 'products': ['rice', 'lipstick', 'milk'], 'units': ['kg']}
)

# Test 21: Fashion entities (if available)
all_passed &= run_test(
    21,
    "Fashion: Clothing with size variants",
    "Add 2 large t-shirts and 1 medium jeans to my order",
    {'variants': ['large', 'medium']}
)

# Test 22: Delivery scheduling with real entities
all_passed &= run_test(
    22,
    "Delivery: Scheduled delivery with real products",
    "Deliver 3kg Dangote rice tomorrow and 2 Peak milk bottles next week",
    {'brands': ['dangote', 'peak'], 'products': ['rice', 'milk'], 'units': ['kg']}
)

# Test 23: Gift order with real entities
all_passed &= run_test(
    23,
    "Gift: Gift order with real beauty products",
    "Send MAC Ruby Woo lipstick as a gift with 2 Maybelline lip glosses",
    {'brands': ['mac', 'maybelline'], 'products': ['lip gloss', 'lipstick']}
)

# Test 24: Conditional purchase with real entities
all_passed &= run_test(
    24,
    "Conditional: If-else logic with real brands",
    "If Dangote rice is available add 10kg, otherwise add 5kg Golden Penny rice",
    {'brands': ['dangote', 'golden penny'], 'products': ['rice'], 'units': ['kg']}
)

# Test 25: Ultimate complex test with real entities
all_passed &= run_test(
    25,
    "Ultimate: Most complex test with real entities",
    "Add 3kg Golden Penny rice, 4 Peak milk bottles, remove MAC lipstick, show me Indomie noodles, and subscribe to weekly Dangote rice delivery",
    {'brands': ['golden penny', 'peak', 'mac', 'indomie', 'dangote'], 
     'products': ['rice', 'milk', 'lipstick', 'noodles'], 'units': ['kg']}
)

# Summary
print(f"\n{'='*80}")
print(f"FINAL RESULT: {'ALL TESTS PASSED ✓' if all_passed else 'SOME TESTS FAILED ✗'}")
print(f"{'='*80}")
print("\nTest Summary:")
print(f"- Total Tests: 25")
print(f"- Testing: spaCy Entity Ruler with Global Entities from merged_v2.json")
print(f"- Entity Types: brands, products, units, variants, quantities")
print(f"\nReal entities tested:")
print("BRANDS: Dangote, Golden Penny, Indomie, Peak, Coca Cola, Nestlé, Three Crowns, Fanta, Sprite, Honeywell, MAC, Maybelline")
print("PRODUCTS: rice, noodles, milk, cereal, beans, flour, lipstick, foundation, lip gloss, lip balm, concealer, Ruby Woo, Fit Me")
print("UNITS: kg, packs, bottles")
print("VARIANTS: large, small, medium, cold, warm, red, nude")
print("\nTest Categories:")
print("- Grocery Tests (1-8): Rice, noodles, milk, soft drinks with brands")
print("- Beauty Tests (9-12): Makeup products with brands")
print("- Mixed Category (13-20): Complex cross-category scenarios")
print("- Advanced Scenarios (21-25): Subscriptions, deliveries, conditionals")
