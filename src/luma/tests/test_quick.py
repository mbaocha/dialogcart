#!/usr/bin/env python3
"""
Quick standalone test - doesn't require trained models or semantics.

Tests basic functionality of luma components.
"""
import sys
from pathlib import Path
import json
import tempfile

# Add src/ directory to path so we can import luma
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

print("=" * 60)
print("Quick Luma Test (Standalone)")
print("=" * 60)

success_count = 0
total_tests = 0

# Test 1: Type System
print("\n[1] Type System...")
total_tests += 1
try:
    from luma.data_types import EntityGroup, ExtractionResult, ProcessingStatus
    
    group = EntityGroup(
        action="add",
        products=["rice"],
        quantities=["2"],
        units=["kg"]
    )
    
    result = ExtractionResult(
        status=ProcessingStatus.SUCCESS,
        original_sentence="add 2kg rice",
        parameterized_sentence="add 2 unittoken producttoken",
        groups=[group]
    )
    
    assert result.is_successful()
    assert result.get_all_products() == ["rice"]
    
    print("✅ Type system works")
    success_count += 1
except Exception as e:
    print(f"❌ Failed: {e}")

# Test 2: Entity Loading
print("\n[2] Entity Loading...")
total_tests += 1
try:
    from luma.core.entity_matcher import load_global_entities
    
    # Create test file
    test_data = [
        {"canonical": "rice", "type": ["product"], "synonyms": ["basmati"]},
        {"canonical": "kg", "type": ["unit"], "synonyms": ["kilogram"]},
    ]
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        json.dump(test_data, f)
        temp_path = f.name
    
    entities = load_global_entities(temp_path)
    assert len(entities) == 2
    
    Path(temp_path).unlink()
    
    print("✅ Entity loading works")
    success_count += 1
except Exception as e:
    print(f"❌ Failed: {e}")

# Test 3: Text Normalization
print("\n[3] Text Normalization...")
total_tests += 1
try:
    from luma.core.entity_matcher import pre_normalization, normalize_hyphens
    
    # Test hyphen normalization
    assert normalize_hyphens("coca - cola") == "coca-cola"
    
    # Test pre_normalization
    normalized = pre_normalization("5kg of Kellogg's rice")
    assert "5 kg" in normalized
    assert "kelloggs" in normalized
    
    print(f"✅ Text normalization works")
    print(f"   '5kg of Kellogg's rice' → '{normalized}'")
    success_count += 1
except Exception as e:
    print(f"❌ Failed: {e}")

# Test 3b: Entity Matcher (without spaCy)
print("\n[3b] Entity Matcher (lazy mode)...")
total_tests += 1
try:
    from luma import EntityMatcher
    
    # Create test file
    test_entities = [
        {"canonical": "rice", "type": ["product"], "synonyms": ["basmati"]},
        {"canonical": "kg", "type": ["unit"], "synonyms": ["kilogram"]},
        {"canonical": "Nike", "type": ["brand"], "synonyms": []},
    ]
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        json.dump(test_entities, f)
        temp_path = f.name
    
    # Use lazy_load_spacy=True to skip spaCy (not needed for basic test)
    matcher = EntityMatcher(entity_file=temp_path, lazy_load_spacy=True)
    
    assert matcher.get_entity_count() == 3
    products = matcher.get_entities_by_type("product")
    assert len(products) == 1
    assert products[0]["canonical"] == "rice"
    
    print(f"✅ Entity matcher works (lazy mode)")
    print(f"   Loaded: {matcher.get_entity_count()} entities")
    print(f"   Support maps built successfully")
    
    # Cleanup
    Path(temp_path).unlink()
    success_count += 1
except Exception as e:
    print(f"❌ Failed: {e}")
    import traceback
    traceback.print_exc()

# Test 4: Entity Grouper
print("\n[4] Entity Grouper...")
total_tests += 1
try:
    from luma.core.grouper import (
        simple_group_entities,
        index_parameterized_tokens,
        extract_entities
    )
    
    # Test entity extraction from labels
    tokens = ["add", "2", "kg", "rice"]
    labels = ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT"]
    
    ents = extract_entities(tokens, labels)
    assert ents["action"] == "add"
    assert ents["products"] == ["rice"]
    assert ents["quantities"] == ["2"]
    assert ents["units"] == ["kg"]
    
    # Test grouping
    grouped = simple_group_entities(tokens, labels)
    assert grouped["status"] == "ok"
    assert len(grouped["groups"]) == 1
    assert grouped["groups"][0]["action"] == "add"
    assert grouped["groups"][0]["products"] == ["rice"]
    
    # Test indexing
    indexed = index_parameterized_tokens(["add", "producttoken", "producttoken"])
    assert indexed == ["add", "producttoken_1", "producttoken_2"]
    
    print(f"✅ Entity grouper works")
    print(f"   Grouped: {grouped['groups'][0]['products']}")
    print(f"   Indexed: {indexed}")
    success_count += 1
except Exception as e:
    print(f"❌ Failed: {e}")
    import traceback
    traceback.print_exc()

# Test 5: Adapters
print("\n[5] Adapters (Legacy Conversion)...")
total_tests += 1
try:
    from luma.adapters import from_legacy_result, to_legacy_result
    
    # Create typed result
    group = EntityGroup(action="add", products=["rice"])
    typed_result = ExtractionResult(
        status=ProcessingStatus.SUCCESS,
        original_sentence="add rice",
        parameterized_sentence="add producttoken",
        groups=[group]
    )
    
    # Convert to legacy dict
    legacy_dict = to_legacy_result(typed_result)
    assert isinstance(legacy_dict, dict)
    assert legacy_dict["status"] == "success"
    
    # Convert back to typed
    typed_again = from_legacy_result(legacy_dict)
    assert typed_again.status == ProcessingStatus.SUCCESS
    
    print(f"✅ Adapters work")
    print(f"   Round-trip conversion successful")
    success_count += 1
except Exception as e:
    print(f"❌ Failed: {e}")

# Final Summary
print("\n" + "=" * 60)
print(f"RESULTS: {success_count}/{total_tests} tests passed")
print("=" * 60)

if success_count == total_tests:
    print("🎉 ALL TESTS PASSED!")
    print("\nYour luma package is working correctly!")
    print("\nNext: Run 'python test_parity.py' to compare with semantics")
else:
    print(f"⚠️  {total_tests - success_count} test(s) failed")
    print("Check the errors above for details")

print("=" * 60)

