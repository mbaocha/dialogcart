import json
import os


TABLE_NAME = "global_entities"
REGION = "eu-west-2"


def load_global_entities():
    """Load global entities from local merged_v2.json file."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(current_dir, "merged_v2.json")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Ensure we only return needed fields; keep extras if present
    entities = []
    for item in data:
        canonical = item.get("canonical")
        entity_type = item.get("type")
        synonyms = item.get("synonyms", [])
        if canonical and entity_type:
            entities.append({
                "canonical": canonical,
                "type": entity_type,
                "synonyms": synonyms,
            })

    return entities

def build_trie_map(entities):
    """
    Return dict mapping synonyms → {canonical, type}.
    Example: {"cat fish": {"canonical": "catfish", "type": "product"}}
    """
    synonym_map = {}
    for e in entities:
        canonical = e.get("canonical", "").lower()
        entity_type = e.get("type", "").lower()
        synonyms = e.get("synonyms", [])
        for s in synonyms:
            synonym_map[s.lower()] = {
                "canonical": canonical,
                "type": entity_type
            }
    return synonym_map


if __name__ == "__main__":
    entities = load_global_entities()


    # For Trie/Normalization
    synonym_map = build_trie_map(entities)
    print("Example synonym mappings:")
    for s, c in list(synonym_map.items())[:10]:
        print(f"  {s} -> {c}")
