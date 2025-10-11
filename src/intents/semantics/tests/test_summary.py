import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nlp_processor import init_nlp_with_entities, extract_entities_with_parameterization

# Initialize NLP with entity ruler
print("Initializing spaCy NLP with entity ruler and global entities...")
nlp, entities = init_nlp_with_entities()
print(f"Loaded {len(entities)} global entities")

def run_test_with_analysis(test_num, description, text, expected_entities):
    """
    Run a test case and return detailed analysis.
    """
    result = extract_entities_with_parameterization(nlp, text, entities, debug_units=False)
    
    # Analyze results
    test_result = {
        'test_num': test_num,
        'description': description,
        'text': text,
        'expected': expected_entities,
        'actual': {
            'brands': result.get('brands', []),
            'products': result.get('products', []),
            'units': result.get('units', []),
            'variants': result.get('variants', []),
            'quantities': result.get('quantities', [])
        },
        'passed': True,
        'failures': []
    }
    
    # Check each expected entity type
    for entity_type, expected_list in expected_entities.items():
        actual_list = result.get(entity_type, [])
        
        # Normalize lists for comparison (lowercase)
        expected_set = set([e.lower() for e in expected_list])
        actual_set = set([e.lower() for e in actual_list])
        
        # Check for missing entities
        missing = expected_set - actual_set
        if missing:
            test_result['passed'] = False
            test_result['failures'].append(f"{entity_type}: missing {list(missing)}")
        
        # Check for unexpected entities (optional - for debugging)
        unexpected = actual_set - expected_set
        if unexpected:
            test_result['unexpected'] = unexpected
    
    return test_result

# Test Cases
test_cases = [
    # GROCERY TESTS
    (1, "Grocery: Rice with Dangote brand", "Add 5kg of Dangote rice to my cart", 
     {'brands': ['dangote'], 'products': ['rice'], 'units': ['kg'], 'quantities': ['5']}),
    
    (2, "Grocery: Indomie noodles with flavor variants", "I want 3 packs of Indomie noodles but not the chicken flavor, add onion flavor instead", 
     {'brands': ['indomie'], 'products': ['noodles'], 'units': ['pack']}),
    
    (3, "Grocery: Peak milk with size and type variants", "Get me 2 large Peak milk and 1 small Peak condensed milk", 
     {'brands': ['peak'], 'products': ['milk'], 'variants': ['large', 'small']}),
    
    (4, "Grocery: Coca Cola with temperature specification", "Add 6 cold Coca Cola bottles and remove 2 warm ones", 
     {'brands': ['coca-cola']}),
    
    (5, "Grocery: Golden Penny rice comparison", "Show me Golden Penny rice and compare with Dangote rice prices", 
     {'brands': ['golden penny', 'dangote'], 'products': ['rice']}),
    
    (6, "Grocery: Multiple products with mixed brands", "I need Nestlé cereal, some beans, and 3 packs of Three Crowns milk", 
     {'brands': ['nestlé', 'three crowns'], 'products': ['cereal', 'beans', 'milk'], 'units': ['pack']}),
    
    (7, "Grocery: Soft drinks with flavor variants", "Add 4 Fanta orange bottles and 2 Sprite lemon bottles", 
     {'brands': ['fanta', 'sprite']}),
    
    (8, "Grocery: Honeywell flour with complex quantities", "Get 10kg of Honeywell flour and 5kg of regular flour", 
     {'brands': ['honeywell'], 'products': ['flour'], 'units': ['kg']}),
    
    # BEAUTY TESTS
    (9, "Beauty: MAC lipstick with shade specification", "Add 2 MAC lipsticks in red shade and 1 in nude shade", 
     {'brands': ['mac'], 'products': ['lipstick']}),
    
    (10, "Beauty: Foundation comparison with brands", "Compare MAC foundation with Maybelline foundation for my skin tone", 
     {'brands': ['mac', 'maybelline'], 'products': ['foundation']}),
    
    (11, "Beauty: Multiple lip products", "I want 3 lip glosses and 2 lip balms from different brands", 
     {'products': ['lip gloss', 'lip balm']}),
    
    (12, "Beauty: Complex order with multiple products", "Add MAC Ruby Woo lipstick, Maybelline Fit Me foundation, and some concealer", 
     {'brands': ['mac', 'maybelline'], 'products': ['concealer', 'lipstick', 'foundation']}),
    
    # MIXED CATEGORY TESTS
    (13, "Mixed: Grocery and Beauty products", "Add 2kg Dangote rice, 1 MAC lipstick, and 3 Peak milk bottles", 
     {'brands': ['dangote', 'mac', 'peak'], 'products': ['rice', 'lipstick', 'milk'], 'units': ['kg']}),
    
    (14, "Multi-intent: Add, remove, and compare with real entities", "Add 5kg Golden Penny rice, remove the Indomie noodles, and show me Coca Cola options", 
     {'brands': ['golden penny', 'indomie', 'coca-cola'], 'products': ['rice', 'noodles'], 'units': ['kg']}),
    
    (15, "Complex: Grocery order with multiple variants", "Get 3 large Peak milk, 2kg Honeywell flour, and 6 cold Fanta bottles", 
     {'brands': ['peak', 'honeywell', 'fanta'], 'products': ['milk', 'flour'], 'variants': ['large'], 'units': ['kg']}),
    
    (16, "Subscription: Weekly delivery with real brands", "Subscribe to weekly delivery of 2kg Dangote rice and 4 Peak milk bottles", 
     {'brands': ['dangote', 'peak'], 'products': ['rice', 'milk'], 'units': ['kg']}),
    
    (17, "Inquiry: Stock check with size variants", "Do you have large Peak milk and small Indomie noodles in stock?", 
     {'brands': ['peak', 'indomie'], 'products': ['milk', 'noodles'], 'variants': ['large', 'small']}),
    
    (18, "Price: Comparison between real brands", "What's the price of Golden Penny rice compared to Dangote rice?", 
     {'brands': ['golden penny', 'dangote'], 'products': ['rice']}),
    
    (19, "Bulk: Large order with specific conditions", "Add 20kg Dangote rice, 10 Peak milk bottles, but not the condensed milk", 
     {'brands': ['dangote', 'peak'], 'products': ['rice', 'milk'], 'units': ['kg']}),
    
    (20, "Complex: Multi-intent with real grocery and beauty products", "Add 5kg Golden Penny rice, remove MAC lipstick, and show me Peak milk options", 
     {'brands': ['golden penny', 'mac', 'peak'], 'products': ['rice', 'lipstick', 'milk'], 'units': ['kg']}),
    
    (21, "Fashion: Clothing with size variants", "Add 2 large t-shirts and 1 medium jeans to my order", 
     {'variants': ['large', 'medium']}),
    
    (22, "Delivery: Scheduled delivery with real products", "Deliver 3kg Dangote rice tomorrow and 2 Peak milk bottles next week", 
     {'brands': ['dangote', 'peak'], 'products': ['rice', 'milk'], 'units': ['kg']}),
    
    (23, "Gift: Gift order with real beauty products", "Send MAC Ruby Woo lipstick as a gift with 2 Maybelline lip glosses", 
     {'brands': ['mac', 'maybelline'], 'products': ['lip gloss', 'lipstick']}),
    
    (24, "Conditional: If-else logic with real brands", "If Dangote rice is available add 10kg, otherwise add 5kg Golden Penny rice", 
     {'brands': ['dangote', 'golden penny'], 'products': ['rice'], 'units': ['kg']}),
    
    (25, "Ultimate: Most complex test with real entities", "Add 3kg Golden Penny rice, 4 Peak milk bottles, remove MAC lipstick, show me Indomie noodles, and subscribe to weekly Dangote rice delivery", 
     {'brands': ['golden penny', 'peak', 'mac', 'indomie', 'dangote'], 
      'products': ['rice', 'milk', 'lipstick', 'noodles'], 'units': ['kg']})
]

# Run all tests
print("\n" + "="*100)
print("RUNNING ALL TESTS")
print("="*100)

results = []
for test_num, description, text, expected in test_cases:
    result = run_test_with_analysis(test_num, description, text, expected)
    results.append(result)

# Calculate summary statistics
passed_tests = [r for r in results if r['passed']]
failed_tests = [r for r in results if not r['passed']]

print(f"\n{'='*100}")
print("TEST SUMMARY")
print(f"{'='*100}")
print(f"Total Tests: {len(results)}")
print(f"✅ PASSED: {len(passed_tests)}")
print(f"❌ FAILED: {len(failed_tests)}")
print(f"Success Rate: {len(passed_tests)/len(results)*100:.1f}%")

# Detailed failure analysis
if failed_tests:
    print(f"\n{'='*100}")
    print("FAILED TESTS DETAILS")
    print(f"{'='*100}")
    
    for test in failed_tests:
        print(f"\n❌ TEST {test['test_num']}: {test['description']}")
        print(f"   Input: '{test['text']}'")
        print(f"   Failures: {', '.join(test['failures'])}")
        print(f"   Expected: {test['expected']}")
        print(f"   Actual:   {test['actual']}")

# Category breakdown
print(f"\n{'='*100}")
print("CATEGORY BREAKDOWN")
print(f"{'='*100}")

categories = {
    'Grocery (1-8)': results[0:8],
    'Beauty (9-12)': results[8:12], 
    'Mixed (13-20)': results[12:20],
    'Advanced (21-25)': results[20:25]
}

for category, tests in categories.items():
    passed = len([t for t in tests if t['passed']])
    total = len(tests)
    print(f"{category:20s}: {passed:2d}/{total:2d} ({passed/total*100:5.1f}%)")

# Most common failure types
if failed_tests:
    print(f"\n{'='*100}")
    print("COMMON FAILURE PATTERNS")
    print(f"{'='*100}")
    
    failure_types = {}
    for test in failed_tests:
        for failure in test['failures']:
            failure_type = failure.split(':')[0]
            failure_types[failure_type] = failure_types.get(failure_type, 0) + 1
    
    for failure_type, count in sorted(failure_types.items(), key=lambda x: x[1], reverse=True):
        print(f"{failure_type:15s}: {count:2d} tests")

print(f"\n{'='*100}")
print("SUMMARY COMPLETE")
print(f"{'='*100}")
