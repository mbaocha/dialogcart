import json

def validate_canonical_in_synonyms(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    errors = []
    for entry in data:
        canonical = entry.get("canonical", "").strip().lower()
        synonyms = [s.strip().lower() for s in entry.get("synonyms", [])]

        if canonical and canonical not in synonyms:
            errors.append({
                "pk": entry.get("pk"),
                "canonical": canonical,
                "synonyms": entry.get("synonyms", [])
            })

    if not errors:
        print("✅ All canonical values are present in their synonyms lists.")
    else:
        print(f"⚠️ {len(errors)} entries missing canonical in synonyms:")
        for e in errors:
            print(f"- pk={e['pk']} | canonical='{e['canonical']}' not in {e['synonyms']}")

# Example usage
validate_canonical_in_synonyms("../store/merged_v5.json")
