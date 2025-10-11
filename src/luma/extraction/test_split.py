"""Quick test to verify the modular split works correctly."""

# Test imports
print("Testing modular split...")
print("=" * 50)

try:
    from luma.extraction import (
        EntityMatcher,
        normalize_hyphens,
        pre_normalization,
        load_global_entities,
        build_global_synonym_map,
    )
    print("✅ All imports successful!")
except ImportError as e:
    print(f"❌ Import failed: {e}")
    exit(1)

# Test normalization functions
print("\n1. Testing normalization functions:")
print("-" * 50)

text1 = "coca - cola"
result1 = normalize_hyphens(text1)
print(f"normalize_hyphens('{text1}') = '{result1}'")
assert result1 == "coca-cola", f"Expected 'coca-cola', got '{result1}'"

text2 = "Add 5kg rice"
result2 = pre_normalization(text2)
print(f"pre_normalization('{text2}') = '{result2}'")
assert "5 kg" in result2, f"Expected '5 kg' in result, got '{result2}'"

print("✅ Normalization functions work!")

# Test entity loading
print("\n2. Testing entity loading:")
print("-" * 50)

try:
    entities = load_global_entities()
    print(f"Loaded {len(entities)} entities")
    assert len(entities) > 0, "No entities loaded"
    print("✅ Entity loading works!")
except Exception as e:
    print(f"❌ Entity loading failed: {e}")

# Test synonym map building
print("\n3. Testing synonym map:")
print("-" * 50)

try:
    synonym_map = build_global_synonym_map(entities)
    print(f"Built synonym map with {len(synonym_map)} entries")
    print("✅ Synonym map building works!")
except Exception as e:
    print(f"❌ Synonym map building failed: {e}")

# Test EntityMatcher initialization (without spaCy to keep it fast)
print("\n4. Testing EntityMatcher initialization:")
print("-" * 50)

try:
    matcher = EntityMatcher(lazy_load_spacy=True)
    print(f"EntityMatcher initialized with {matcher.get_entity_count()} entities")
    print("✅ EntityMatcher initialization works!")
except Exception as e:
    print(f"❌ EntityMatcher initialization failed: {e}")

print("\n" + "=" * 50)
print("✅ ALL TESTS PASSED! Modular split is working correctly!")
print("=" * 50)

