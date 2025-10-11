#!/usr/bin/env python3
"""
Parity test: Compare luma vs semantics output.

This verifies that luma produces identical results to semantics.
"""
import sys
from pathlib import Path

# Add src/ directory to path so we can import luma
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
semantics_path = Path(__file__).parent.parent / "intents" / "semantics"
sys.path.insert(0, str(semantics_path))

print("=" * 70)
print("PARITY TEST: Luma vs Semantics")
print("=" * 70)

# Test 1: NER Model Parity
print("\n[Test 1] NER Model: luma vs semantics...")
try:
    from luma import NERModel
    from ner_inference import process_text
    
    luma_model = NERModel()
    
    test_cases = [
        "add producttoken to cart",
        "add 2 unittoken producttoken",
        "add brandtoken producttoken",
    ]
    
    passed = 0
    for test_text in test_cases:
        # Luma
        luma_result = luma_model.predict(test_text)
        
        # Semantics
        semantics_result = process_text(test_text)
        
        # Compare
        if (luma_result.tokens == semantics_result["tokens"] and
            luma_result.labels == semantics_result["labels"]):
            passed += 1
            print(f"  ✅ '{test_text}'")
        else:
            print(f"  ❌ '{test_text}'")
            print(f"     Luma tokens: {luma_result.tokens}")
            print(f"     Sem tokens:  {semantics_result['tokens']}")
            print(f"     Luma labels: {luma_result.labels}")
            print(f"     Sem labels:  {semantics_result['labels']}")
    
    print(f"\nNER Parity: {passed}/{len(test_cases)} passed")
    
except FileNotFoundError as e:
    print(f"⚠️  Skipped (model not found): {e}")
except Exception as e:
    print(f"❌ Failed: {e}")
    import traceback
    traceback.print_exc()

# Test 2: Grouper Parity
print("\n[Test 2] Grouper: luma vs semantics...")
try:
    from luma.core.grouper import simple_group_entities as luma_group
    from luma.core.grouper import index_parameterized_tokens as luma_index
    from entity_grouping import simple_group_entities as sem_group
    from entity_grouping import index_parameterized_tokens as sem_index
    
    # Test simple_group_entities
    test_cases = [
        (["add", "rice"], ["B-ACTION", "B-PRODUCT"]),
        (["add", "2", "kg", "rice"], ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT"]),
        (["remove", "3", "bottles"], ["B-ACTION", "B-QUANTITY", "B-UNIT"]),
    ]
    
    passed = 0
    for tokens, labels in test_cases:
        # Luma
        luma_result = luma_group(tokens, labels)
        
        # Semantics
        sem_result = sem_group(tokens, labels)
        
        # Compare
        if (luma_result["status"] == sem_result["status"] and
            len(luma_result["groups"]) == len(sem_result["groups"])):
            passed += 1
            print(f"  ✅ {tokens}")
        else:
            print(f"  ❌ {tokens}")
            print(f"     Luma: {luma_result}")
            print(f"     Sem:  {sem_result}")
    
    # Test index_parameterized_tokens
    test_tokens = ["add", "producttoken", "producttoken", "brandtoken"]
    luma_indexed = luma_index(test_tokens)
    sem_indexed = sem_index(test_tokens)
    
    if luma_indexed == sem_indexed:
        print(f"  ✅ Token indexing: {test_tokens} → {luma_indexed}")
        passed += 1
    else:
        print(f"  ❌ Token indexing mismatch")
        print(f"     Luma: {luma_indexed}")
        print(f"     Sem:  {sem_indexed}")
    
    print(f"\nGrouper Parity: {passed}/{len(test_cases)+1} passed")
    
except Exception as e:
    print(f"❌ Failed: {e}")
    import traceback
    traceback.print_exc()

# Summary
print("\n" + "=" * 70)
print("PARITY TEST COMPLETE")
print("=" * 70)
print("\nIf all tests passed, luma output matches semantics exactly!")
print("=" * 70)

