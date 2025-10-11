import re
import unicodedata
import string
from typing import Dict, Any, Optional, List
import spacy
from load_data import load_global_entities
from entity_norm_examples import sentences as test_sentences


# Load spaCy English model (small is fine for speed)
nlp = spacy.load("en_core_web_sm")

# Units and stopwords to filter out
UNIT_WORDS = {
    "kg", "g", "gram", "grams", "litre", "liter", "ml",
    "bottle", "bottles", "pack", "packs", "carton", "cartons",
    "can", "cans", "pair", "pairs", "tube", "tubes", "jar", "jars", "box", "boxes", "crate", "crates"
}
STOPWORDS = {"i", "you", "we", "they", "he", "she", "it", "me", "us", "them"}


def normalize_for_trie(text: str) -> str:
    """Aggressive normalization for trie keys."""
    text = text.lower()
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("–", "-").replace("—", "-")
    text = text.replace("&", " and ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.replace("'", "").replace("-", " ")
    text = text.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", text).strip()


class EntityTrie:
    def __init__(self):
        self.root: Dict[str, Any] = {}

    def insert(self, phrase: str, canonical: str, entity_type: str):
        norm_phrase = normalize_for_trie(phrase)
        node = self.root
        for word in norm_phrase.split():
            node = node.setdefault(word, {})
        node["_end_"] = {"canonical": canonical.lower(), "type": entity_type.lower()}

    def search(self, phrase: str) -> Optional[Dict[str, str]]:
        node = self.root
        for word in normalize_for_trie(phrase).split():
            if word not in node:
                return None
            node = node[word]
        return node.get("_end_")

    def longest_match(self, tokens: List[str], start_idx: int, fuzzy: bool = True) -> Optional[Dict[str, Any]]:
        from rapidfuzz.distance import Levenshtein
        from rapidfuzz import fuzz

        node = self.root
        last_valid = None

        for i in range(start_idx, len(tokens)):
            word = tokens[i]
            next_key, best_score = None, 0

            if word in node:  # exact match
                next_key = word
            elif fuzzy:
                for child in node.keys():
                    if child == "_end_":
                        continue
                    if "_end_" not in node[child] and not any("_end_" in n for n in node[child].values()):
                        continue
                    dist = Levenshtein.distance(word, child)
                    score = fuzz.ratio(word, child)
                    if len(word) <= 4:
                        if dist <= 1 and score >= 90 and score > best_score:
                            next_key, best_score = child, score
                    else:
                        max_dist = 1 if len(child) <= 8 else 2
                        if dist <= max_dist and score >= 80 and score > best_score:
                            next_key, best_score = child, score

            if not next_key:
                break

            node = node[next_key]
            if "_end_" in node:
                last_valid = {
                    "match": node["_end_"]["canonical"],
                    "canonical": node["_end_"]["canonical"],
                    "type": node["_end_"]["type"],
                    "end": i,
                }

        return last_valid

    def extract_candidates(self, sentence: str) -> List[str]:
        """Use spaCy to extract candidate noun phrases and orgs."""
        doc = nlp(sentence)
        candidates = set()

        print(f"[DEBUG] Extracting candidates from: {sentence}")

        # Noun chunks
        for chunk in doc.noun_chunks:
            norm = normalize_for_trie(chunk.text)
            head = chunk.root.lemma_.lower()
            print(f"  - Raw chunk: '{chunk.text}' | Head: '{head}' | Normalized: '{norm}'")
            if not norm or head in STOPWORDS or head in UNIT_WORDS:
                print("    ❌ Skipped (stopword/unit)")
                continue
            candidates.add(norm)

        # ORG entities
        for ent in doc.ents:
            if ent.label_ == "ORG":
                norm = normalize_for_trie(ent.text)
                print(f"  - ORG entity: '{ent.text}' → '{norm}'")
                if norm:
                    candidates.add(norm)

        print(f"[DEBUG] Final candidates: {list(candidates)}")
        return list(candidates)

    def scan_sentence(self, sentence: str, parameterize: bool = False, fuzzy: bool = True) -> Any:
        norm_sentence = normalize_for_trie(sentence)
        tokens = norm_sentence.split()

        # Get candidates via spaCy
        candidates = self.extract_candidates(sentence)

        results, new_tokens, params = [], tokens[:], []
        product_count, brand_count = 0, 0

        for cand in candidates:
            for i in range(len(tokens)):
                match = self.longest_match(tokens, i, fuzzy=fuzzy)
                if match and match["canonical"] not in params:
                    results.append(match)
                    params.append(match["canonical"])
                    if match["type"] == "product":
                        product_count += 1
                        placeholder = f"product_{product_count}"
                    else:
                        brand_count += 1
                        placeholder = f"brand_{brand_count}"
                    for j in range(i, match["end"] + 1):
                        new_tokens[j] = placeholder if j == i else ""

        if parameterize:
            return {"sentence": " ".join([t for t in new_tokens if t]), "params": params}
        return results


def build_entity_trie(entities: List[Dict[str, Any]]) -> EntityTrie:
    trie = EntityTrie()
    for e in entities:
        canonical, entity_type = e.get("canonical"), e.get("type")
        synonyms = e.get("synonyms", [])
        if canonical and entity_type:
            trie.insert(canonical, canonical, entity_type)
            for s in synonyms:
                trie.insert(s, canonical, entity_type)
    return trie


def main():
    entities = load_global_entities()
    print(f"Loaded {len(entities)} entities from DynamoDB")

    trie = build_entity_trie(entities)

    total_tests, passed_tests, failed_tests = len(test_sentences), 0, []

    print(f"\n=== Testing {total_tests} Example Sentences ===")
    print("=" * 60)

    for i, example in enumerate(test_sentences, 1):
        original, expected_sentence, expected_params = (
            example["original"],
            example["sentence"],
            example["params"],
        )

        result = trie.scan_sentence(original, parameterize=True, fuzzy=True)
        actual_sentence, actual_params = result["sentence"], result["params"]

        sentence_match = actual_sentence.lower() == expected_sentence.lower()
        params_match = actual_params == expected_params
        test_passed = params_match

        if test_passed:
            passed_tests += 1
            print(f"✅ PASSED {i}: {original}")
        else:
            failed_tests.append(
                {
                    "index": i,
                    "original": original,
                    "expected_sentence": expected_sentence,
                    "actual_sentence": actual_sentence,
                    "expected_params": expected_params,
                    "actual_params": actual_params,
                }
            )
            print(f"❌ FAILED {i}: {original}")

    print("\n" + "=" * 60)
    print(f"TEST SUMMARY: {passed_tests}/{total_tests} passed "
          f"({passed_tests/total_tests*100:.1f}%)")

    if failed_tests:
        print(f"\nFAILED TESTS ({len(failed_tests)}):")
        for test in failed_tests:
            print(f"\nTest {test['index']}: {test['original']}")
            print(f"  Expected: {test['expected_sentence']} | Params: {test['expected_params']}")
            print(f"  Actual:   {test['actual_sentence']} | Params: {test['actual_params']}")


if __name__ == "__main__":
    main()
