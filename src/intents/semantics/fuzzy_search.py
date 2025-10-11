from rapidfuzz import process, fuzz
import re

def fuzzy_recover_multiword_entities(doc, entities, threshold=88, debug=False):
    """
    Fuzzy-recover multiword entities (products, brands, variants, units)
    that were not matched by the EntityRuler.

    Steps:
    - Skip meaningless spans (with connectors like 'and', 'of', 'to', etc.)
    - Generate n-grams (2–4 tokens) from non-entity tokens
    - Fuzzy match them against canonical + synonym lists in global catalog
    - Return new fuzzy-recovered entity candidates with types + confidence
    """
    STOPWORDS = {"and", "or", "to", "of", "for", "in", "the", "a"}
    catalog_maps = {
        "brand": {},
        "product": {},
        "variant": {},
        "unit": {}
    }

    # === Build fuzzy lookup maps ===
    for ent in entities:
        canonical = ent["canonical"].lower()
        types = ent.get("type", [])
        synonyms = [s.lower() for s in ent.get("synonyms", [])]
        all_terms = {canonical, *synonyms}

        for t in types:
            if t in catalog_maps:
                for term in all_terms:
                    catalog_maps[t][term] = canonical

    results = []

    # === Collect tokens not already marked as entities ===
    tokens = [t.text.lower() for t in doc]
    ent_spans = {(ent.start, ent.end) for ent in doc.ents}

    # Build index of token positions that belong to an entity
    occupied = set()
    for start, end in ent_spans:
        occupied.update(range(start, end))

    # === Generate multi-word spans (n-grams) for non-entity tokens ===
    ngrams = []
    for n in range(2, 5):  # 2–4 word phrases only
        for i in range(len(tokens) - n + 1):
            if any((i + j) in occupied for j in range(n)):
                continue
            span_tokens = tokens[i:i+n]

            # Skip punctuation-only or numeric-only phrases
            if all(re.fullmatch(r"[\W\d]+", t) for t in span_tokens):
                continue

            phrase = " ".join(span_tokens)

            # Skip spans with internal stopwords (e.g., "rice and beans")
            if any(tok in STOPWORDS for tok in span_tokens[1:-1]):
                continue

            # Skip if phrase is entirely stopwords
            if all(tok in STOPWORDS for tok in span_tokens):
                continue

            ngrams.append((phrase, i, i+n))

    if debug:
        print(f"[FUZZY] Candidate n-grams: {len(ngrams)}")

    # === Run fuzzy match against catalog ===
    for phrase, start, end in ngrams:
        for label, cmap in catalog_maps.items():
            if not cmap:
                continue

            # Ignore stopwords during matching for better accuracy
            cleaned_phrase = re.sub(r"\b(and|or|to|of|for|in|the|a)\b", "", phrase).strip()

            # Run fuzzy matching
            best_match = process.extractOne(
                cleaned_phrase,
                cmap.keys(),
                scorer=fuzz.token_sort_ratio
            )

            if not best_match:
                continue

            matched_text, score, _ = best_match
            if score >= threshold:
                canonical = cmap[matched_text]
                results.append({
                    "type": label,
                    "text": canonical,
                    "span": (start, end),
                    "score": score,
                    "source": "fuzzy"
                })

                if debug:
                    print(f"[FUZZY] '{phrase}' → '{canonical}' ({score}%) [{label}]")

    return results


import spacy
import time
from fuzzy_search import fuzzy_recover_multiword_entities

def main():
    print("🧠 Fuzzy Entity Recovery Tester\n")

    # === Load spaCy model ===
    nlp = spacy.load("en_core_web_sm")

    # === Sample catalog (simulating merged_v8.json entries) ===
    entities = [
        {
            "canonical": "air force 1",
            "type": ["product"],
            "synonyms": [
                "airforce 1",
                "air force ones",
                "air force one sneakers",
                "af1",
                "nike air force 1",
            ],
        },
        {
            "canonical": "brown beans",
            "type": ["product"],
            "synonyms": ["nigerian beans", "brown bean"],
        },
        {
            "canonical": "red rice",
            "type": ["product"],
            "synonyms": ["local red rice"],
        },
        {
            "canonical": "nike",
            "type": ["brand"],
            "synonyms": ["nike inc", "nike brand"],
        },
    ]

    print("Model and catalog loaded ✅")
    print("=" * 60)

    while True:
        text = input("\nEnter sentence (or 'exit'): ").strip()
        if text.lower() in {"exit", "quit"}:
            print("👋 Exiting.")
            break

        doc = nlp(text)

        start = time.time()
        matches = fuzzy_recover_multiword_entities(doc, entities, threshold=85, debug=True)
        elapsed = time.time() - start

        print("\n----------------------------------------")
        print(f"🕓 Elapsed time: {elapsed:.2f}s\n")

        if matches:
            print("✅ Fuzzy Matches Found:")
            for m in matches:
                span_text = " ".join([t.text for t in doc[m['span'][0]:m['span'][1]]])
                print(f" - {m['type'].upper()}: '{span_text}' → '{m['text']}' ({m['score']}%)")
        else:
            print("⚠️ No fuzzy matches found.")

        print("----------------------------------------")


if __name__ == "__main__":
    main()
