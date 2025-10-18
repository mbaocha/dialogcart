#!/usr/bin/env python3
"""
Test fuzzy entity recovery.

Tests the FuzzyEntityMatcher class for recovering misspelled or variant entities.
"""
import sys
from pathlib import Path

# Add src/ directory to path so we can import luma
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    from luma.extraction import FuzzyEntityMatcher, FUZZY_AVAILABLE
    if not FUZZY_AVAILABLE:
        print("⚠️  rapidfuzz not installed - skipping fuzzy matcher tests")
        print("   Install with: pip install rapidfuzz")
        sys.exit(0)
except ImportError:
    print("⚠️  rapidfuzz not installed - skipping fuzzy matcher tests")
    print("   Install with: pip install rapidfuzz")
    sys.exit(0)


def test_fuzzy_recovery():
    """Test fuzzy entity recovery with sample data."""
    print("\n" + "=" * 60)
    print("Testing Fuzzy Entity Recovery")
    print("=" * 60)
    
    # Sample catalog
    entities = [
        {
            "canonical": "air force 1",
            "type": ["product"],
            "synonyms": [
                "airforce 1",
                "air force ones",
                "air force one sneakers",
                "af1",
                "nike air force 1",
            ],
        },
        {
            "canonical": "brown beans",
            "type": ["product"],
            "synonyms": ["nigerian beans", "brown bean"],
        },
        {
            "canonical": "coca-cola",
            "type": ["brand"],
            "synonyms": ["cocacola", "coke", "coca cola"],
        },
    ]
    
    # Create matcher
    matcher = FuzzyEntityMatcher(entities, threshold=85)
    print("✅ FuzzyEntityMatcher initialized")
    
    # Test cases
    test_cases = [
        ("add airforce ones", "air force 1", "product"),
        ("I want nigerian beans", "brown beans", "product"),
        ("buy cocacola", "coca-cola", "brand"),
    ]
    
    try:
        import spacy
        nlp = spacy.load("en_core_web_sm")
        
        for sentence, expected_match, expected_type in test_cases:
            doc = nlp(sentence)
            results = matcher.recover_entities(doc, debug=False)
            
            if results:
                found = any(
                    r['text'] == expected_match and r['type'] == expected_type 
                    for r in results
                )
                status = "✅" if found else "❌"
                print(f"{status} '{sentence}' → found '{expected_match}' as {expected_type}")
            else:
                print(f"❌ '{sentence}' → no matches found")
        
        print("\n✅ Fuzzy recovery tests completed!")
        
    except ImportError:
        print("⚠️  spaCy not installed - skipping full tests")
        print("   Install with: pip install spacy")
        print("   Then run: python -m spacy download en_core_web_sm")


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Fuzzy Entity Matcher Test Suite")
    print("=" * 60)
    
    try:
        test_fuzzy_recovery()
        
        print("\n" + "=" * 60)
        print("✅ ALL TESTS COMPLETED!")
        print("=" * 60)
        print("\n💡 Note:")
        print("   Fuzzy matching is optional and requires 'rapidfuzz'")
        print("   Use it when you need typo tolerance")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

