import json

# Path to your JSON file
json_file = "merged.json"  # replace with your actual file
output_file = "merged_clean.json"

# Rules for cleanup
removals = {
    "trouser": ["knickers"],       # keep knickers only under panty
    "cassava flour": ["akpu"],     # keep akpu only under fufu
    "maize flour": ["ogi", "akamu"]  # keep ogi/akamu only under pap
}

with open(json_file, "r", encoding="utf-8") as f:
    data = json.load(f)

changes = []

# Apply removals
for entry in data:
    canonical = entry.get("canonical")
    if canonical in removals:
        before = set(entry.get("synonyms", []))
        to_remove = set(removals[canonical])
        after = list(before - to_remove)
        if before != set(after):
            entry["synonyms"] = after
            changes.append((canonical, list(to_remove)))

# Save cleaned file
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

# Print report
if changes:
    print("✅ Cleanup applied. The following synonyms were removed:\n")
    for canonical, removed in changes:
        print(f"- From '{canonical}': removed {removed}")
    print(f"\n💾 Cleaned file saved as: {output_file}")
else:
    print("No changes were needed — file already clean.")
