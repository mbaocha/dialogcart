import json
from collections import defaultdict

# Load your dataset (update path as needed)
with open("merged.json", "r", encoding="utf-8") as f:
    brands = json.load(f)

# Build synonym map
synonym_map = defaultdict(list)
for brand in brands:
    canonical = brand["canonical"].lower()
    for syn in brand.get("synonyms", []):
        synonym_map[syn.lower()].append(canonical)

# Find duplicates (synonyms pointing to multiple canonicals)
duplicates = {syn: c for syn, c in synonym_map.items() if len(c) > 1}

# Report
if duplicates:
    print("⚠️ Conflicting synonyms found:")
    for syn, canonicals in duplicates.items():
        print(f"  '{syn}' → {canonicals}")
else:
    print("✅ All synonyms are unique across brands.")
