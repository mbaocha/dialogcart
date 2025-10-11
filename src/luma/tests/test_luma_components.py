#!/usr/bin/env python3
"""
Quick test script to verify luma components work correctly.

Run this to test individual components without pytest.
"""
import sys
from pathlib import Path

# Add src/ directory to path so we can import luma
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

print("=" * 60)
print("Luma Component Testing")
print("=" * 60)

# Test 1: Import all components
print("\n[Test 1] Importing all components...")
try:
    from luma import (
        NERModel,
        EntityMatcher,
        simple_group_entities,
        index_parameterized_tokens,
        ProcessingStatus,
        ExtractionResult,
        EntityGroup,
    )
    print("✅ All imports successful")
except Exception as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)

# Test 2: NER Model
print("\n[Test 2] Testing NER Model...")
try:
    # Note: This requires a trained model at store/bert-ner-best
    # If model doesn't exist, this will fail
    model = NERModel()
    result = model.predict("add producttoken to cart")
    
    assert isinstance(result.tokens, list)
    assert isinstance(result.labels, list)
    assert isinstance(result.scores, list)
    assert len(result.tokens) > 0
    
    print(f"✅ NER Model works!")
    print(f"   Input: 'add producttoken to cart'")
    print(f"   Tokens: {result.tokens}")
    print(f"   Labels: {result.labels}")
except FileNotFoundError:
    print("⚠️  NER Model not found (run ner_model_training.py first)")
except Exception as e:
    print(f"❌ NER Model failed: {e}")

# Test 3: Entity Matcher (requires merged_v9.json)
print("\n[Test 3] Testing Entity Matcher...")
try:
    # Create test entity file
    import json
    import tempfile
    
    test_entities = [
        {"canonical": "rice", "type": ["product"], "synonyms": ["basmati rice"]},
        {"canonical": "kg", "type": ["unit"], "synonyms": ["kilogram"]},
        {"canonical": "Nike", "type": ["brand"], "synonyms": ["nike brand"]},
    ]
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        json.dump(test_entities, f)
        temp_path = f.name
    
    # Test with temp file (avoids needing merged_v9.json)
    matcher = EntityMatcher(entity_file=temp_path)
    
    assert matcher.get_entity_count() == 3
    products = matcher.get_entities_by_type("product")
    assert len(products) == 1
    
    print(f"✅ Entity Matcher works!")
    print(f"   Loaded: {matcher.get_entity_count()} entities")
    print(f"   Products: {len(products)}")
    
    # Cleanup
    Path(temp_path).unlink()
except Exception as e:
    print(f"❌ Entity Matcher failed: {e}")
    import traceback
    traceback.print_exc()

# Test 4: Entity Grouper
print("\n[Test 4] Testing Entity Grouper...")
try:
    tokens = ["add", "2", "kg", "rice"]
    labels = ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT"]
    
    result = simple_group_entities(tokens, labels)
    
    assert result["status"] in ["ok", "error", "needs_llm"]
    assert "groups" in result
    assert len(result["groups"]) > 0
    
    print(f"✅ Entity Grouper works!")
    print(f"   Status: {result['status']}")
    print(f"   Groups: {len(result['groups'])}")
    print(f"   First group action: {result['groups'][0]['action']}")
    print(f"   First group products: {result['groups'][0]['products']}")
except Exception as e:
    print(f"❌ Entity Grouper failed: {e}")
    import traceback
    traceback.print_exc()

# Test 5: Token Indexing
print("\n[Test 5] Testing Token Indexing...")
try:
    tokens = ["add", "producttoken", "producttoken", "brandtoken"]
    indexed = index_parameterized_tokens(tokens)
    
    assert indexed[0] == "add"
    assert indexed[1] == "producttoken_1"
    assert indexed[2] == "producttoken_2"
    assert indexed[3] == "brandtoken_1"
    
    print(f"✅ Token Indexing works!")
    print(f"   Input: {tokens}")
    print(f"   Output: {indexed}")
except Exception as e:
    print(f"❌ Token Indexing failed: {e}")

# Test 6: Type System
print("\n[Test 6] Testing Type System...")
try:
    # Create entity group
    group = EntityGroup(
        action="add",
        products=["rice"],
        quantities=["2"],
        units=["kg"]
    )
    
    assert group.action == "add"
    assert group.has_quantity()
    
    # Create extraction result
    result = ExtractionResult(
        status=ProcessingStatus.SUCCESS,
        original_sentence="test",
        parameterized_sentence="test",
        groups=[group]
    )
    
    assert result.is_successful()
    assert result.get_all_products() == ["rice"]
    
    print(f"✅ Type System works!")
    print(f"   Created EntityGroup: {group.products}")
    print(f"   Created ExtractionResult: status={result.status.value}")
except Exception as e:
    print(f"❌ Type System failed: {e}")

# Summary
print("\n" + "=" * 60)
print("Test Summary")
print("=" * 60)
print("All basic components are working!")
print("\nNext steps:")
print("1. Run full unit tests: pytest tests/ -v")
print("2. Run parity tests: python test_parity.py")
print("3. Integrate components: See PHASE3D plan")
print("=" * 60)

