from transformers import pipeline

# Initialize NER pipeline
ner_pipeline = pipeline(
    'token-classification',
    model='./minilm-ner-best',
    tokenizer='./minilm-ner-best',
    aggregation_strategy='none'
)

def run_test(test_num, description, text, expected_entities):
    """
    Run a test case and verify expected entities are detected.
    
    Args:
        test_num: Test case number
        description: Description of what is being tested
        text: Input text to test
        expected_entities: Dict with expected entity types (e.g., {'producttoken': 'B-PRODUCT', 'brandtoken': 'B-BRAND'})
    """
    print(f"\n{'='*80}")
    print(f"TEST {test_num}: {description}")
    print(f"Input: '{text}'")
    print(f"{'-'*80}")
    
    result = ner_pipeline(text)
    
    # Track found entities
    found_entities = {}
    
    for r in result:
        word = r["word"].replace('##', '')  # Clean subword tokens
        entity = r["entity"]
        score = r["score"]
        
        # Only print entities that are labeled (not 'O')
        if entity != 'O':
            print(f"  {word:20s} -> {entity:15s} (confidence: {score:.3f})")
            
            # Track entities for verification
            if word in expected_entities:
                found_entities[word] = entity
    
    # Verify expected entities
    print(f"{'-'*80}")
    passed = True
    for word, expected_label in expected_entities.items():
        actual_label = found_entities.get(word, 'NOT_FOUND')
        status = "✓ PASS" if actual_label == expected_label else "✗ FAIL"
        print(f"  {status}: '{word}' expected {expected_label}, got {actual_label}")
        if actual_label != expected_label:
            passed = False
    
    print(f"{'='*80}")
    return passed


# Test Cases
all_passed = True

# Test 1: Multi-product with variants (size and color)
all_passed &= run_test(
    1,
    "Multiple products with size and color variants",
    "Add 2 large red brandtoken producttoken and 3 small blue producttoken to cart",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 2: Brand + Product with flavor variant
all_passed &= run_test(
    2,
    "Brand and product with flavor variant",
    "I want to buy brandtoken chocolate flavored producttoken",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 3: Multi-intent: Add and remove different products
all_passed &= run_test(
    3,
    "Multi-intent: Add one product, remove another",
    "Add 5 bottles of brandtoken producttoken but remove the producttoken I added earlier",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 4: Product comparison with two brands
all_passed &= run_test(
    4,
    "Product comparison between brands",
    "Show me brandtoken producttoken and compare with brandtoken producttoken",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 5: Complex quantity units with brand
all_passed &= run_test(
    5,
    "Complex quantity units (kg, liters, packs)",
    "Get me 2.5 kg of brandtoken producttoken and 1 liter of producttoken",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 6: Multiple products with mixed parameterization
all_passed &= run_test(
    6,
    "Multiple products, some with brands, some without",
    "I need brandtoken producttoken, some rice, and another pack of brandtoken producttoken",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 7: Product with material/type variant
all_passed &= run_test(
    7,
    "Product with material variant",
    "Add organic cotton brandtoken producttoken size medium to my order",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 8: Question about availability with variants
all_passed &= run_test(
    8,
    "Stock inquiry with multiple variants",
    "Do you have brandtoken producttoken in extra large or the sugar-free brandtoken producttoken?",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 9: Update quantity for existing product
all_passed &= run_test(
    9,
    "Update/modify intent with brand and product",
    "Change the brandtoken producttoken quantity to 10 pieces instead",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 10: Bundle/combo with multiple products
all_passed &= run_test(
    10,
    "Bundle purchase with multiple products",
    "I want the combo with brandtoken producttoken, producttoken, and a free brandtoken producttoken",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 11: Product with packaging variant
all_passed &= run_test(
    11,
    "Product with packaging specification",
    "Add 3 economy packs of brandtoken producttoken and 1 travel size brandtoken producttoken",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 12: Price-based inquiry
all_passed &= run_test(
    12,
    "Price inquiry with brand and product",
    "What's the price of brandtoken premium producttoken compared to regular producttoken?",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 13: Multi-variant complex order
all_passed &= run_test(
    13,
    "Complex order with multiple variants",
    "I need 2 dozen brandtoken producttoken in vanilla, 6 pack brandtoken producttoken mint flavor, and producttoken",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 14: Substitution intent
all_passed &= run_test(
    14,
    "Substitution with alternative product",
    "Replace the brandtoken producttoken with brandtoken producttoken if out of stock",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 15: Multi-product search with filters
all_passed &= run_test(
    15,
    "Search with brand filter and product type",
    "Show all brandtoken producttoken and producttoken under 500 rupees",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 16: Preferred brand with variant fallback
all_passed &= run_test(
    16,
    "Preferred brand with fallback option",
    "I prefer brandtoken producttoken in 1L size but any brand of producttoken works if not available",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 17: Scheduled delivery with multi-product
all_passed &= run_test(
    17,
    "Scheduled delivery with multiple products",
    "Deliver 5 kg brandtoken producttoken and 2 boxes of brandtoken producttoken next Tuesday",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 18: Gift/special instruction with product
all_passed &= run_test(
    18,
    "Gift order with special instructions",
    "Send brandtoken producttoken as a gift with 3 brandtoken producttoken samples included",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 19: Bulk order with variants and conditions
all_passed &= run_test(
    19,
    "Bulk order with specific conditions",
    "Add 20 units of brandtoken producttoken in assorted colors but not green, and 15 brandtoken producttoken",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 20: Complex multi-intent with preferences
all_passed &= run_test(
    20,
    "Complex multi-intent with user preferences",
    "Add brandtoken low-fat producttoken, remove the full cream producttoken, and show me brandtoken producttoken options",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 21: Multi-product subscription with frequency and variants
all_passed &= run_test(
    21,
    "Subscription order with multiple products and delivery frequency",
    "Subscribe to weekly delivery of 4L brandtoken producttoken, 2 packs of brandtoken organic producttoken, and cancel my producttoken subscription but keep the monthly brandtoken producttoken",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 22: Cross-category order with brand loyalty and exceptions
all_passed &= run_test(
    22,
    "Cross-category order with brand preferences and exceptions",
    "Add 3 bottles of brandtoken producttoken in mango flavor, 5 kg brandtoken producttoken but not the frozen one, and do you have brandtoken sugar-free producttoken available for same-day delivery?",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 23: Reorder with modifications and new additions
all_passed &= run_test(
    23,
    "Reorder previous items with quantity modifications",
    "Reorder my last purchase but change brandtoken producttoken to 10 units instead of 5, add extra brandtoken producttoken in large size, remove the producttoken, and include 2 new items: brandtoken producttoken and producttoken",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 24: Split delivery with different product conditions
all_passed &= run_test(
    24,
    "Split delivery with multiple conditions per product",
    "Send 6 brandtoken producttoken and 3 boxes of producttoken tomorrow, but deliver the 12 pack brandtoken gluten-free producttoken next week with 4 brandtoken producttoken samples, and hold the producttoken until I confirm",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Test 25: Complex comparison with conditional purchase
all_passed &= run_test(
    25,
    "Multi-brand comparison with conditional bulk purchase",
    "Compare prices between brandtoken premium producttoken 500ml and brandtoken producttoken 1L, if brandtoken is cheaper add 20 units of brandtoken producttoken, otherwise add 15 of the brandtoken producttoken with free producttoken samples, and remove any producttoken under 250ml",
    {'brandtoken': 'B-BRAND', 'producttoken': 'B-PRODUCT'}
)

# Summary
print(f"\n{'='*80}")
print(f"FINAL RESULT: {'ALL TESTS PASSED ✓' if all_passed else 'SOME TESTS FAILED ✗'}")
print(f"{'='*80}")
