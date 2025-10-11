import re
import unicodedata
import string
from typing import Dict, Any, Optional, List

from rapidfuzz import process, fuzz


# ---------------- NORMALIZATION ---------------- #
def normalize_for_trie(text: str) -> str:
    text = text.lower()
    text = text.replace("–", "-").replace("—", "-")
    text = text.replace("&", " and ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.replace("'", "").replace("-", " ")
    text = text.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", text).strip()


from rapidfuzz import process, fuzz

# ---------------- TRIE ---------------- #
class EntityTrie:
    def __init__(self):
        self.root: Dict[str, Any] = {}
        self.candidates: List[Dict[str, str]] = []  # still keep for global fuzzy fallback

    def insert(self, phrase: str, canonical: str, entity_type: str):
        norm_phrase = normalize_for_trie(phrase)
        node = self.root
        for word in norm_phrase.split():
            node = node.setdefault(word, {})
        node["_end_"] = {"canonical": canonical.lower(), "type": entity_type.lower()}

        self.candidates.append(
            {"phrase": norm_phrase, "canonical": canonical.lower(), "type": entity_type.lower()}
        )

    def longest_match(self, tokens: List[str], start_idx: int, fuzzy: bool = True) -> Optional[Dict[str, Any]]:
        from rapidfuzz.distance import Levenshtein
        from rapidfuzz import fuzz

        node = self.root
        last_valid = None
        used_fuzzy_any_step = False
        path_tokens = []

        for i in range(start_idx, len(tokens)):
            word = tokens[i]
            path_tokens.append(word)

            next_key = None

            # --- Exact match --- #
            if word in node:
                next_key = word

            elif fuzzy:
                for child in node.keys():
                    if child == "_end_":
                        continue
                    score = fuzz.ratio(word, child)
                    if score >= 85:
                        next_key = child
                        used_fuzzy_any_step = used_fuzzy_any_step or (word != child)
                        print(f"[TRIE] Fuzzy match: '{word}' → '{child}' (score={score})")
                        break

            if not next_key:
                break

            node = node[next_key]

            # Store any complete match we find, but continue searching for longer ones
            if "_end_" in node:
                last_valid = {
                    "match": " ".join(tokens[start_idx:i+1]),
                    "canonical": node["_end_"]["canonical"],
                    "type": node["_end_"]["type"],
                    "end": i,
                    "used_fuzzy": used_fuzzy_any_step,
                }
                print(f"[TRIE] ✅ Found: {last_valid['match']} → {last_valid['canonical']} ({last_valid['type']})")

        return last_valid

    def search(self, phrase: str, *, min_score: int = 80) -> Optional[Dict[str, Any]]:
        tokens = normalize_for_trie(phrase).split()
        return self.longest_match(tokens, 0, fuzzy=True)


def build_entity_trie(entities: List[Dict[str, Any]]) -> EntityTrie:
    trie = EntityTrie()
    for e in entities:
        canonical = e.get("canonical")
        entity_type = e.get("type")
        synonyms = e.get("synonyms", [])
        if canonical and entity_type:
            trie.insert(canonical, canonical, entity_type)
            for s in synonyms:
                trie.insert(s, canonical, entity_type)

    return trie



# ---------------- VALIDATION ---------------- #
def validate_entities(
    ner_entities,
    sentence,
    trie: EntityTrie,
    global_entities,
    *,
    min_score: int = 80,
    trie_only=None,
    fuzzy_allowed=None,
):
    print(f"\n[VALIDATE] Processing {len(ner_entities)} entities: {[e['text'] for e in ner_entities]}")
    if trie_only is None:
        trie_only = {"token", "unit", "quantity"}
    if fuzzy_allowed is None:
        fuzzy_allowed = {"product", "brand"}

    norm_sentence = normalize_for_trie(sentence)
    tokens = norm_sentence.split()

    results = []
    used_token_indices = set()
    entity_strings = [e["canonical"] for e in global_entities] + [
        s for e in global_entities for s in e.get("synonyms", [])
    ]

    for ent in ner_entities:
        ent_type = ent.get("type", "").lower()
        text = ent["text"]
        
        # Handle comma-separated brands
        if ent_type == "brand" and "," in text:
            brand_parts = [part.strip() for part in text.split(",")]
            print(f"[VALIDATE] Processing comma-separated brands: {brand_parts}")
            
            best_match = None
            for brand_part in brand_parts:
                norm_text = normalize_for_trie(brand_part)
                first_token = norm_text.split()[0] if norm_text else ""
                start_idxs = [i for i, tok in enumerate(tokens) if tok == first_token]
                
                for start_idx in start_idxs:
                    if start_idx in used_token_indices:
                        continue
                    
                    # Try to find the longest valid phrase starting from this position
                    match = trie.longest_match(tokens, start_idx, fuzzy=True)
                    if match:
                        if not best_match or match["end"] > best_match["end"]:
                            best_match = match
                            best_match["start_idx"] = start_idx
                            print(f"[VALIDATE] New best match from '{brand_part}': {match['match']} (end={match['end']})")
        else:
            # Single brand processing
            norm_text = normalize_for_trie(text)
            first_token = norm_text.split()[0] if norm_text else ""
            start_idxs = [i for i, tok in enumerate(tokens) if tok == first_token]

            best_match = None
            for start_idx in start_idxs:
                if start_idx in used_token_indices:
                    continue
                
                # Try to find the longest valid phrase starting from this position
                match = trie.longest_match(tokens, start_idx, fuzzy=True)
                if match:
                    if not best_match or match["end"] > best_match["end"]:
                        best_match = match
                        best_match["start_idx"] = start_idx

        if best_match:
            for i in range(best_match["start_idx"], best_match["end"] + 1):
                used_token_indices.add(i)
            results.append({
                "original": best_match["match"],
                "validated": True,
                "validation_method": "fuzzy" if best_match.get("used_fuzzy") else "trie",
                "canonical": best_match["canonical"],
                "type": best_match["type"],
                "score": 100,
            })
            continue

        # --- Fuzzy fallback ---
        if ent_type in fuzzy_allowed:
            best, score, _ = process.extractOne(text, entity_strings, scorer=fuzz.ratio)
            if best and score >= min_score:
                canonical, ent_type_found = None, None
                for e in global_entities:
                    if best == e["canonical"] or best in e.get("synonyms", []):
                        canonical, ent_type_found = e["canonical"], e["type"]
                        break
                print(f"[VALIDATE] Fuzzy match: '{text}' → '{canonical or best}' (score={score})")
                results.append({
                    "original": text,
                    "validated": True,
                    "validation_method": "fuzzy",
                    "canonical": canonical or best,
                    "type": ent_type_found or ent_type,
                    "score": score,
                })
                continue

        # --- Fallback ---
        print(f"[VALIDATE] No match found for: '{text}'")
        results.append({
            "original": text,
            "validated": False,
            "validation_method": "none",
            "canonical": text,
            "type": ent_type,
            "score": None,
        })

    return results


def validate_structured_items(
    structured,
    sentence,
    trie,
    global_entities,
    *,
    fuzzy_min_score: int = 80,
    move_across_slots: bool = True,
    clear_wrong_slot: bool = True,
):
    """
    Validate + canonicalize product/brand fields and tokens.
    - Products/brands: Trie → Fuzzy
    - Tokens: Trie only (exact match, no fuzzy)
    """

    def validate_one(ent_text, ent_type_hint):
        # --- If token, only allow trie --- #
        if ent_type_hint == "token":
            trie_match = validate_entities(
                [{"text": ent_text, "type": ent_type_hint}],
                sentence,
                trie,
                global_entities,
                min_score=999,  # disables fuzzy
            )[0]
            return trie_match

        # --- 1. Trie --- #
        trie_match = validate_entities(
            [{"text": ent_text, "type": ent_type_hint}],
            sentence,
            trie,
            global_entities,
            min_score=999,  # disables fuzzy
        )[0]
        if trie_match["validated"] and trie_match["validation_method"] == "trie":
            return trie_match

        # --- 2. Fuzzy --- #
        fuzzy_match = validate_entities(
            [{"text": ent_text, "type": ent_type_hint}],
            sentence,
            trie,
            global_entities,
            min_score=fuzzy_min_score,
        )[0]
        if fuzzy_match["validated"] and fuzzy_match["validation_method"] == "fuzzy":
            return fuzzy_match

        # --- fallback --- #
        return {
            "original": ent_text,
            "validated": False,
            "validation_method": "none",
            "canonical": ent_text,
            "type": ent_type_hint,
            "score": None,
        }

    # ---------------- MAIN LOOP ---------------- #
    for item in structured:
        # validate product/brand
        for slot in ["product", "brand"]:
            if item.get(slot):
                validated = validate_one(item[slot], slot)
                canonical = validated["canonical"]

                item[slot] = canonical
                item[f"{slot}_validated"] = validated["validated"]
                item[f"{slot}_validation_method"] = validated["validation_method"]

                if move_across_slots and validated["type"] != slot:
                    wrong_slot = slot
                    right_slot = validated["type"]
                    item[right_slot] = canonical
                    item[f"{right_slot}_validated"] = validated["validated"]
                    item[f"{right_slot}_validation_method"] = validated["validation_method"]

                    if clear_wrong_slot:
                        item[wrong_slot] = None
                        item[f"{wrong_slot}_validated"] = None
                        item[f"{wrong_slot}_validation_method"] = None

        # validate tokens → trie only
        tokens = list(item.get("tokens", []))
        promoted = []
        for t in tokens:
            validated = validate_one(t, "token")
            if validated["validated"] and validated["validation_method"] == "trie":
                dest_type = validated["type"]
                canonical = validated["canonical"]
                if dest_type in ["product", "brand"]:
                    item[dest_type] = canonical
                    item[f"{dest_type}_validated"] = True
                    item[f"{dest_type}_validation_method"] = "trie"
                    promoted.append(t)

        if promoted:
            item["tokens"] = [tok for tok in tokens if tok not in promoted]

    return structured






# ... keep everything above unchanged ...


# ---------------- TEST HARNESS ---------------- #
def test_validation():
    entities = [
        {
            "canonical": "sock",
            "type": "product",
            "synonyms": ["socks"],
        },
        {
            "canonical": "blouse",
            "type": "product",
            "synonyms": ["blouses"],
        },
        {
            "canonical": "prada",
            "type": "brand",
            "synonyms": [],
        },
    ]

    trie = build_entity_trie(entities)
    sentence = "Do you have Prada socks size 38 in blue?"

    ner_entities = [
        {"text": "Prada", "type": "brand"},
        {"text": "socks", "type": "product"},
        {"text": "38", "type": "quantity"},
        {"text": "blue", "type": "token"},
    ]

    print("\n=== VALIDATION (trie + fuzzy) ===")
    validated = validate_entities(ner_entities, sentence, trie, entities)
    for v in validated:
        print(v)

    print("\n=== VALIDATION (trie-only, fuzzy disabled) ===")
    validated = validate_entities(
        ner_entities,
        sentence,
        trie,
        entities,
        trie_only={"product", "brand", "token", "unit", "quantity"},
        fuzzy_allowed=set(),
    )
    for v in validated:
        print(v)

    print("\n=== validate_structured_items ===")
    structured = [
        {"value": "Prada", "entity": "BRAND", "confidence": 0.9},
        {"value": "socks", "entity": "PRODUCT", "confidence": 0.95},
        {"value": "38", "entity": "QUANTITY", "confidence": 0.85},
        {"value": "blue", "entity": "TOKEN", "confidence": 0.8},
    ]
    result = validate_structured_items(structured, sentence, trie, entities)
    print(result)


if __name__ == "__mainx__":
    test_validation()



from rapidfuzz import process, fuzz

def main():
    # Example global entities
    entities = [
        {"canonical": "coca cola", "type": "brand", "synonyms": []},
        {"canonical": "pepsi", "type": "brand", "synonyms": []},
        {"canonical": "sock", "type": "product", "synonyms": ["socks"]},
    ]

    # Build trie
    trie = build_entity_trie(entities)

    # Build fuzzy candidate list
    candidates = [e["canonical"] for e in entities] + [
        s for e in entities for s in e.get("synonyms", [])
    ]

    print("\n=== TRIE + FUZZY Test Mode ===")
    while True:
        text = input("Enter input (or 'quit'): ").strip()
        if not text or text.lower() in {"quit", "exit"}:
            break

        tokens = normalize_for_trie(text).split()
        match = trie.longest_match(tokens, 0)

        if match:
            print(f"✅ TRIE Match: {match}")
        else:
            # Fuzzy fallback
            best, score, _ = process.extractOne(text, candidates, scorer=fuzz.ratio)
            if best and score >= 80:
                print(f"✨ FUZZY Match: {best} (score={score})")
            else:
                print("❌ No match")

if __name__ == "__main__":
    main()

