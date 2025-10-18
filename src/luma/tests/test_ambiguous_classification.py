#!/usr/bin/env python3
"""
Test ambiguous entity classification functions.

Verifies that the classification logic correctly distinguishes:
- Units vs Products (e.g., "bag" in "2 bags of rice" vs "Gucci bag")
- Variants vs Products (e.g., "red" in "red rice" vs "Red brand")
- Brands vs Products (e.g., "Dove" in "Dove soap" vs standalone "dove")
"""
import sys
from pathlib import Path

# Add src/ directory to path so we can import luma
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from luma.extraction import (
    EntityClassifier,  # Class-based approach (better performance)
    classify_ambiguous_units,  # Standalone function (backward compatibility)
    classify_ambiguous_variants,
    classify_ambiguous_brands,
)


def test_ambiguous_units():
    """Test unit vs product classification."""
    print("\n" + "=" * 60)
    print("Testing Ambiguous Units Classification")
    print("=" * 60)
    
    # Test case 1: "bag" as unit (preceded by number)
    sentence1 = "add 2 bags of rice"
    entity_list1 = ["bags"]
    ambiguous_units1 = {"bags", "bag"}
    entities1 = []
    
    result1 = classify_ambiguous_units(sentence1, entity_list1, ambiguous_units1, entities1, debug=False)
    assert len(result1["units"]) == 1, f"Expected 1 unit, got {len(result1['units'])}"
    assert result1["units"][0]["entity"] == "bags"
    print("✅ Test 1 passed: 'add 2 bags of rice' → bags is UNIT")
    
    # Test case 2: "bag" as product (preceded by brand)
    sentence2 = "add 1 gucci bag"
    entity_list2 = ["bag"]
    ambiguous_units2 = {"bags", "bag"}
    entities2 = [
        {"canonical": "gucci", "type": "brand", "synonyms": ["gucci"]}
    ]
    
    result2 = classify_ambiguous_units(sentence2, entity_list2, ambiguous_units2, entities2, debug=False)
    assert len(result2["products"]) == 1, f"Expected 1 product, got {len(result2['products'])}"
    assert result2["products"][0]["entity"] == "bag"
    print("✅ Test 2 passed: 'add 1 gucci bag' → bag is PRODUCT")
    
    # Test case 3: "case" as unit (followed by "of")
    sentence3 = "add case of beer"
    entity_list3 = ["case"]
    ambiguous_units3 = {"case"}
    entities3 = []
    
    result3 = classify_ambiguous_units(sentence3, entity_list3, ambiguous_units3, entities3, debug=False)
    assert len(result3["units"]) == 1, f"Expected 1 unit, got {len(result3['units'])}"
    assert result3["units"][0]["entity"] == "case"
    print("✅ Test 3 passed: 'add case of beer' → case is UNIT")
    
    print("\n✅ All unit classification tests passed!")


def test_ambiguous_variants():
    """Test variant vs product classification."""
    print("\n" + "=" * 60)
    print("Testing Ambiguous Variants Classification")
    print("=" * 60)
    
    # Test case 1: "red" as variant (followed by product)
    sentence1 = "add red rice"
    entity_list1 = ["red"]
    ambiguous_variants1 = {"red"}
    entities1 = [
        {"canonical": "rice", "type": "product", "synonyms": ["rice"]}
    ]
    
    result1 = classify_ambiguous_variants(sentence1, entity_list1, ambiguous_variants1, entities1, debug=False)
    assert len(result1["variants"]) == 1, f"Expected 1 variant, got {len(result1['variants'])}"
    assert result1["variants"][0]["entity"] == "red"
    print("✅ Test 1 passed: 'add red rice' → red is VARIANT")
    
    # Test case 2: "large" as variant (preceded by product)
    sentence2 = "add rice large"
    entity_list2 = ["large"]
    ambiguous_variants2 = {"large"}
    entities2 = [
        {"canonical": "rice", "type": "product", "synonyms": ["rice"]}
    ]
    
    result2 = classify_ambiguous_variants(sentence2, entity_list2, ambiguous_variants2, entities2, debug=False)
    assert len(result2["variants"]) == 1, f"Expected 1 variant, got {len(result2['variants'])}"
    assert result2["variants"][0]["entity"] == "large"
    print("✅ Test 2 passed: 'add rice large' → large is VARIANT")
    
    # Test case 3: "blue" as product (standalone)
    sentence3 = "add blue"
    entity_list3 = ["blue"]
    ambiguous_variants3 = {"blue"}
    entities3 = []
    
    result3 = classify_ambiguous_variants(sentence3, entity_list3, ambiguous_variants3, entities3, debug=False)
    assert len(result3["products"]) == 1, f"Expected 1 product, got {len(result3['products'])}"
    assert result3["products"][0]["entity"] == "blue"
    print("✅ Test 3 passed: 'add blue' → blue is PRODUCT")
    
    print("\n✅ All variant classification tests passed!")


def test_ambiguous_brands():
    """Test brand vs product classification."""
    print("\n" + "=" * 60)
    print("Testing Ambiguous Brands Classification")
    print("=" * 60)
    
    # Note: This test requires spaCy, so we'll use a simple mock doc
    try:
        import spacy
        nlp = spacy.load("en_core_web_sm")
        
        # Test case 1: "dove" as brand (followed by product)
        sentence1 = "add dove soap"
        doc1 = nlp(sentence1)
        entity_list1 = ["dove"]
        ambiguous_brands1 = {"dove"}
        entities1 = []
        
        result1 = classify_ambiguous_brands(doc1, entity_list1, ambiguous_brands1, entities1, debug=False)
        # "dove" followed by "soap" (NOUN) should be classified as BRAND
        assert len(result1["brands"]) == 1, f"Expected 1 brand, got {len(result1['brands'])}"
        assert result1["brands"][0]["entity"] == "dove"
        print("✅ Test 1 passed: 'add dove soap' → dove is BRAND")
        
        # Test case 2: "dove" as product (standalone)
        sentence2 = "add dove"
        doc2 = nlp(sentence2)
        entity_list2 = ["dove"]
        ambiguous_brands2 = {"dove"}
        entities2 = []
        
        result2 = classify_ambiguous_brands(doc2, entity_list2, ambiguous_brands2, entities2, debug=False)
        # Standalone "dove" defaults to PRODUCT
        assert len(result2["products"]) == 1, f"Expected 1 product, got {len(result2['products'])}"
        assert result2["products"][0]["entity"] == "dove"
        print("✅ Test 2 passed: 'add dove' → dove is PRODUCT")
        
        print("\n✅ All brand classification tests passed!")
        
    except ImportError:
        print("⚠️  Skipping brand classification tests (spaCy not available)")


def test_class_based_approach():
    """Test class-based approach for better performance."""
    print("\n" + "=" * 60)
    print("Testing Class-Based Approach (Recommended)")
    print("=" * 60)
    
    # Create classifier once
    entities = [
        {"canonical": "gucci", "type": "brand", "synonyms": ["gucci"]},
        {"canonical": "rice", "type": "product", "synonyms": ["rice"]},
    ]
    classifier = EntityClassifier(entities)
    
    # ✅ Reuse classifier for multiple calls (no rebuilding of lookup maps)
    print("Testing multiple classifications with cached classifier...")
    
    # Test 1
    result1 = classifier.classify_units("add 2 bags of rice", ["bags"], {"bags", "bag"}, debug=False)
    assert len(result1["units"]) == 1
    print("✅ Call 1: Unit classification successful")
    
    # Test 2
    result2 = classifier.classify_units("add 1 gucci bag", ["bag"], {"bags", "bag"}, debug=False)
    assert len(result2["products"]) == 1
    print("✅ Call 2: Product classification successful")
    
    # Test 3
    result3 = classifier.classify_variants("add red rice", ["red"], {"red"}, debug=False)
    assert len(result3["variants"]) == 1
    print("✅ Call 3: Variant classification successful")
    
    print("\n💡 Performance Note: Class-based approach reuses cached lookups!")
    print("   - Brand words: cached once")
    print("   - Product words: cached once")
    print("   - Faster for multiple classifications")


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Ambiguous Entity Classification Test Suite")
    print("=" * 60)
    
    try:
        test_ambiguous_units()
        test_ambiguous_variants()
        test_ambiguous_brands()
        test_class_based_approach()
        
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        print("\n📝 Recommendation:")
        print("   - Use EntityClassifier class for multiple classifications")
        print("   - Use standalone functions for one-off classifications")
        print("=" * 60)
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

