import json
from collections import defaultdict, Counter

JSON_PATH = "global_entities.json"  # assumes same dir as script

def analyze_synonyms_with_context(path=JSON_PATH):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    all_synonyms = []
    context_map = defaultdict(list)  # synonym -> list of (pk, sk)

    for item in data:
        pk, sk = item.get("pk"), item.get("sk")
        syns = item.get("synonyms", [])
        for s in syns:
            syn = s.strip().lower()
            all_synonyms.append(syn)
            context_map[syn].append(f"{pk}::{sk}")

    counter = Counter(all_synonyms)

    total_synonyms = len(all_synonyms)
    unique_synonyms = len(counter)
    dupes = {syn: count for syn, count in counter.items() if count > 1}

    print(f"\nTotal synonym strings: {total_synonyms}")
    print(f"Unique synonym strings: {unique_synonyms}")
    print(f"  - Appear only once: {sum(1 for c in counter.values() if c == 1)}")
    print(f"  - Appear more than once: {len(dupes)}")

    if dupes:
        print("\n⚠ Duplicate synonyms with context:")
        for syn, count in counter.most_common():
            if count > 1:
                print(f"'{syn}' appears {count} times")
                for ctx in context_map[syn]:
                    print(f"   ↳ {ctx}")
        print()

if __name__ == "__main__":
    analyze_synonyms_with_context()
