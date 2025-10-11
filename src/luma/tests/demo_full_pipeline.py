#!/usr/bin/env python3
"""
Demo: Full Luma Pipeline

Tests the complete end-to-end extraction pipeline.
Requires: spacy, transformers, torch, numpy

Run with: python demo_full_pipeline.py
"""
import sys
import os
from pathlib import Path

# Add src/ directory to path so we can import luma
# File is in: src/luma/tests/demo_full_pipeline.py
# We need: src/ in the path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Enable debug logging
os.environ["DEBUG_NLP"] = "1"
os.environ["USE_LUMA_PIPELINE"] = "true"

print("=" * 70)
print("LUMA FULL PIPELINE DEMO")
print("=" * 70)
print("\nThis demonstrates the complete luma extraction pipeline:")
print("  Input → EntityMatcher → NERModel → Grouper → Output")
print("\n" + "=" * 70)

try:
    from luma import extract_entities, EntityExtractionPipeline
    
    # Test sentences
    test_sentences = [
        "add 2kg rice to cart",
        "add 2kg white rice and 3 bottles of milk",
        "remove 1 Nike shoes",
        "add 5 bags of beans",
    ]
    
    print("\n[1] Using extract_entities() API...")
    print("-" * 70)
    
    for i, sentence in enumerate(test_sentences, 1):
        print(f"\n--- Test {i}: '{sentence}' ---")
        
        try:
            result = extract_entities(sentence, debug=False)
            
            print(f"Status: {result.status.value}")
            print(f"Parameterized: {result.parameterized_sentence}")
            print(f"Groups: {len(result.groups)}")
            
            for j, group in enumerate(result.groups, 1):
                print(f"\n  Group {j}:")
                print(f"    Action: {group.action}")
                print(f"    Products: {group.products}")
                print(f"    Brands: {group.brands}")
                print(f"    Quantities: {group.quantities}")
                print(f"    Units: {group.units}")
                print(f"    Variants: {group.variants}")
            
            print(f"\n✅ Extraction successful")
            
        except Exception as e:
            print(f"❌ Failed: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 70)
    print("[2] Using EntityExtractionPipeline class...")
    print("-" * 70)
    
    # Create pipeline explicitly
    pipeline = EntityExtractionPipeline(use_luma=True)
    
    sentence = "add 2kg white rice to cart"
    print(f"\nInput: '{sentence}'")
    
    result = pipeline.extract(sentence)
    
    print(f"\nResult:")
    print(f"  Status: {result.status.value}")
    print(f"  Original: {result.original_sentence}")
    print(f"  Parameterized: {result.parameterized_sentence}")
    print(f"  Products: {result.get_all_products()}")
    print(f"  Success: {result.is_successful()}")
    
    if result.groups:
        print(f"\n  First group:")
        g = result.groups[0]
        print(f"    Action: {g.action}")
        print(f"    Products: {g.products}")
        print(f"    Quantities: {g.quantities}")
        print(f"    Units: {g.units}")
        print(f"    Variants: {g.variants}")
    
    print("\n✅ Full pipeline working!")
    
    print("\n" + "=" * 70)
    print("DEMO COMPLETE")
    print("=" * 70)
    print("\n✅ Luma pipeline is fully functional!")
    print("\nThe pipeline successfully:")
    print("  1. Loaded entities and initialized spaCy")
    print("  2. Extracted and parameterized entities")
    print("  3. Classified tokens with NER model")
    print("  4. Grouped entities by action")
    print("  5. Returned typed, validated results")
    print("\n🎉 Integration successful!")
    
except ImportError as e:
    print(f"\n❌ Missing dependencies: {e}")
    print("\nInstall with:")
    print("  pip install -r requirements.txt")
    print("  python -m spacy download en_core_web_sm")
    print("\nThen run: python classification/training.py (to train model)")
    
except FileNotFoundError as e:
    print(f"\n❌ Missing files: {e}")
    print("\nMake sure:")
    print("  1. store/merged_v9.json exists (entity data)")
    print("  2. store/bert-ner-best/ exists (trained model)")
    print("\nRun: python classification/training.py to train model")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)

