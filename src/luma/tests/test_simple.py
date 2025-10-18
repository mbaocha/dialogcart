#!/usr/bin/env python3
"""
Simplest possible test - NO external dependencies required.

Tests only pure Python logic without numpy, spacy, transformers, etc.
"""
import sys
from pathlib import Path

# Add src/ directory to path so we can import luma
# File is in: src/luma/tests/test_simple.py
# We need: src/ in the path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

print("=" * 60)
print("Simple Luma Test (No Dependencies)")
print("=" * 60)

success = 0
total = 0

# Test 1: Grouper - extract_entities
print("\n[1] Grouper: extract_entities...")
total += 1
try:
    from luma.grouping.grouper import extract_entities
    
    tokens = ["add", "2", "kg", "rice"]
    labels = ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT"]
    
    result = extract_entities(tokens, labels)
    
    assert result["action"] == "add"
    assert result["products"] == ["rice"]
    assert result["quantities"] == ["2"]
    assert result["units"] == ["kg"]
    
    print(f"✅ Works!")
    print(f"   Action: {result['action']}")
    print(f"   Products: {result['products']}")
    print(f"   Quantities: {result['quantities']}")
    success += 1
except Exception as e:
    print(f"❌ Failed: {e}")
    import traceback
    traceback.print_exc()

# Test 2: Grouper - index_parameterized_tokens
print("\n[2] Grouper: index_parameterized_tokens...")
total += 1
try:
    from luma.grouping.grouper import index_parameterized_tokens
    
    tokens = ["add", "producttoken", "producttoken", "brandtoken"]
    result = index_parameterized_tokens(tokens)
    
    assert result == ["add", "producttoken_1", "producttoken_2", "brandtoken_1"]
    
    print(f"✅ Works!")
    print(f"   Input: {tokens}")
    print(f"   Output: {result}")
    success += 1
except Exception as e:
    print(f"❌ Failed: {e}")

# Test 3: Grouper - simple_group_entities
print("\n[3] Grouper: simple_group_entities...")
total += 1
try:
    from luma.grouping.grouper import simple_group_entities
    
    tokens = ["add", "rice"]
    labels = ["B-ACTION", "B-PRODUCT"]
    
    result = simple_group_entities(tokens, labels)
    
    assert result["status"] == "ok"
    assert len(result["groups"]) == 1
    assert result["groups"][0]["action"] == "add"
    assert result["groups"][0]["products"] == ["rice"]
    
    print(f"✅ Works!")
    print(f"   Status: {result['status']}")
    print(f"   Groups: {len(result['groups'])}")
    success += 1
except Exception as e:
    print(f"❌ Failed: {e}")
    import traceback
    traceback.print_exc()

# Test 4: Entity Matcher - normalize_hyphens
print("\n[4] Entity Matcher: normalize_hyphens...")
total += 1
try:
    from luma.extraction.matcher import normalize_hyphens
    
    result = normalize_hyphens("coca – cola")
    assert result == "coca-cola"
    
    print(f"✅ Works!")
    print(f"   'coca – cola' → '{result}'")
    success += 1
except Exception as e:
    print(f"❌ Failed: {e}")

# Test 5: Entity Matcher - pre_normalization
print("\n[5] Entity Matcher: pre_normalization...")
total += 1
try:
    from luma.extraction.matcher import pre_normalization
    
    result = pre_normalization("5kg of Kellogg's rice")
    
    assert "5 kg" in result
    assert "kelloggs" in result
    
    print(f"✅ Works!")
    print(f"   '5kg of Kellogg's rice' → '{result}'")
    success += 1
except Exception as e:
    print(f"❌ Failed: {e}")

# Test 6: Entity Matcher - load_global_entities
print("\n[6] Entity Matcher: load_global_entities...")
total += 1
try:
    import json
    import tempfile
    from luma.extraction.matcher import load_global_entities
    
    test_data = [
        {"canonical": "rice", "type": ["product"], "synonyms": ["basmati"]},
        {"canonical": "kg", "type": ["unit"], "synonyms": []},
    ]
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        json.dump(test_data, f)
        temp_path = f.name
    
    entities = load_global_entities(temp_path)
    
    assert len(entities) == 2
    assert entities[0]["canonical"] == "rice"
    
    Path(temp_path).unlink()
    
    print(f"✅ Works!")
    print(f"   Loaded {len(entities)} entities")
    success += 1
except Exception as e:
    print(f"❌ Failed: {e}")

# Summary
print("\n" + "=" * 60)
print(f"RESULTS: {success}/{total} tests passed")
print("=" * 60)

if success == total:
    print("🎉 ALL TESTS PASSED!")
    print("\n✅ Core luma logic works correctly!")
    print("\nTo test with full dependencies:")
    print("1. Install: pip install -r requirements.txt")
    print("2. Install spacy: python -m spacy download en_core_web_sm")
    print("3. Run full tests: python -m pytest tests/")
elif success > 0:
    print(f"✅ {success} tests passed")
    print(f"⚠️  {total - success} test(s) failed (likely missing dependencies)")
else:
    print("❌ All tests failed - check errors above")

print("=" * 60)

