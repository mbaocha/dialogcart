import spacy
import os, json
import re
from spacy.tokenizer import Tokenizer

from rapidfuzz import fuzz, process

from rapidfuzz import fuzz, process

import re
import unicodedata

# ===== LOGGING CONFIGURATION =====
# Set DEBUG_NLP=1 in environment to enable debug logs
DEBUG_ENABLED = os.environ.get("DEBUG_NLP", "0") == "1"

def debug_print(*args, **kwargs):
    """Print debug message only if DEBUG_ENABLED is True"""
    if DEBUG_ENABLED:
        print(*args, **kwargs)

def normalize_hyphens(text: str) -> str:
    """
    Normalize all dash-like characters to a simple hyphen and
    remove spaces around them. Ensures variants like 'coca – cola'
    or 'coca - cola' become 'coca-cola'.
    """
    text = unicodedata.normalize("NFKC", text)
    # Replace en/em/minus dashes etc. with simple hyphen
    text = re.sub(r"[‐-‒–—−]", "-", text)
    # Remove spaces around hyphens
    text = re.sub(r"\s*-\s*", "-", text)
    return text


def load_global_entities():
    """Load global entities from local file."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(current_dir, "store/merged_v9.json")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entities = []
    for item in data:
        canonical = item.get("canonical")
        entity_type = item.get("type", [])
        synonyms = item.get("synonyms", [])
        if canonical and entity_type:
            entities.append({
                "canonical": canonical,
                "type": entity_type,   # ✅ type is now always a list
                "synonyms": synonyms,
                "example": item.get("example", {})  # ✅ keep example if present
            })
    return entities


def _remove_entity_occurrence(entity_list, position, length):
    """Remove an entity from a list by its position if it overlaps the same span."""
    if not entity_list:
        return
    start, end = position, position + length
    entity_list[:] = [
        e for e in entity_list
        if not (isinstance(e, dict)
                and start <= e.get("position", -1) < end)
    ]


def customize_tokenizer(nlp):
    """
    Custom tokenizer that:
      - ✅ Preserves internal hyphens (e.g., 'coca-cola' stays one token)
      - ✅ Still splits between digits and letters (e.g., '5kg' → '5', 'kg')
    """
    import re
    from spacy.tokenizer import Tokenizer

    # Remove "-" from infix pattern so words like "coca-cola" are preserved
    infix_re = re.compile(r'''(?<=\d)(?=[a-zA-Z])|(?<=[a-zA-Z])(?=\d)''')

    # Keep the same prefix/suffix rules
    prefix_re = re.compile(r'''^[\[\("']''')
    suffix_re = re.compile(r'''[\]\)"']$''')

    return Tokenizer(
        nlp.vocab,
        rules=nlp.Defaults.tokenizer_exceptions,
        prefix_search=prefix_re.search,
        suffix_search=suffix_re.search,
        infix_finditer=infix_re.finditer,
        token_match=None
    )



def init_nlp_with_entities():
    nlp = spacy.load("en_core_web_sm")
    nlp.tokenizer = customize_tokenizer(nlp)
    entities = load_global_entities()
    patterns = build_entity_patterns(entities)

    ruler = nlp.add_pipe("entity_ruler", before="ner", config={"overwrite_ents": True})
    ruler.add_patterns(patterns)

    return nlp, entities


def normalize_longest_phrases(text, synonym_map, max_n=5):
    """
    Normalize using the longest valid phrase from synonym_map.
    Ensures 'soft drink' overrides 'soft' or 'drink'.
    """
    words = text.lower().split()
    normalized = words[:]
    skip_until = -1
    i = 0

    while i < len(words):
        if i < skip_until:
            i += 1
            continue

        matched_len = 0
        matched_canonical = None

        # check from longest to shortest phrase
        for n in range(max_n, 0, -1):
            span = " ".join(words[i:i + n])
            if span in synonym_map:
                matched_len = n
                matched_canonical = synonym_map[span]
                break  # longest match wins

        if matched_canonical:
            normalized[i:i + matched_len] = [matched_canonical]
            skip_until = i + matched_len
        i += 1
    debug_print("normalized: ", normalized)

    return " ".join(normalized)

def normalize_plural_to_singular(text: str, nlp) -> str:
    """
    Converts plural nouns to singular using the provided spaCy pipeline.
    Reuses the already-loaded nlp instance to avoid reloading models.
    """
    doc = nlp(text)
    normalized_tokens = [token.lemma_ if token.pos_ == "NOUN" else token.text for token in doc]
    return " ".join(normalized_tokens)



def build_entity_patternsx(entities):
    """Convert entities into spaCy ruler patterns for BRAND, PRODUCT, UNIT, VARIANT, NOISE.
       Only add single-type entities. Multi-type entities are skipped (context decides)."""
    patterns = []
    for ent in entities:
        types = ent.get("type", [])
        names = sorted(ent.get("synonyms", []), key=lambda x: -len(x.split()))

        # 🚫 skip ambiguous multi-type entities
        if len(types) > 1:
            continue

        t = types[0]
        label = t.upper()  # "brand" -> BRAND, etc.
        for name in names:
            patterns.append({"label": label, "pattern": name})

    return patterns

def build_entity_patterns(entities):
    """
    Convert entities into spaCy ruler patterns for BRAND, PRODUCT, UNIT, VARIANT, NOISE, PRODUCTBRAND.
    - Single-type → direct label (BRAND, PRODUCT, etc.)
    - Multi-type brand+product → PRODUCTBRAND
    - All patterns sorted longest-first.
    """
    patterns = []

    for ent in entities:
        types = ent.get("type", [])
        names = sorted(ent.get("synonyms", []), key=lambda x: -len(x.split()))

        # ✅ Case 1: brand+product combo → PRODUCTBRAND
        if "brand" in types and "product" in types:
            for name in names:
                patterns.append({"label": "PRODUCTBRAND", "pattern": name})
            continue  # don't double-add as BRAND/PRODUCT below

        # ✅ Case 2: skip other multi-type for context classification
        if len(types) > 1:
            continue

        # ✅ Case 3: regular single-type entity
        t = types[0]
        label = t.upper()  # brand → BRAND, etc.
        for name in names:
            patterns.append({"label": label, "pattern": name})

    return patterns

def build_support_maps(entities):
    """
    Build lookup maps for units, variants, products, and brands.
    Rule:
      - Single-type entities → go into canonical maps.
      - Multi-type entities → go only into ambiguous sets.
    """
    unit_map, variant_map, product_map, brand_map, noise_set = {}, {}, {}, {}, set()
    unambiguous_units, ambiguous_units = set(), set()
    unambiguous_variants, ambiguous_variants = set(), set()
    ambiguous_brands = set()

    for ent in entities:
        canon = ent["canonical"].lower()
        synonyms = [s.lower() for s in ent.get("synonyms", [])]
        all_terms = [canon] + synonyms
        types = ent.get("type", [])

        # --- Single-type entities ---
        if len(types) == 1:
            t = types[0]
            if t == "unit":
                for term in all_terms:
                    unit_map[term] = canon
                unambiguous_units.update(all_terms)

            elif t == "variant":
                for term in all_terms:
                    variant_map[term] = canon
                unambiguous_variants.update(all_terms)

            elif t == "product":
                for term in all_terms:
                    product_map[term] = canon

            elif t == "brand":
                for term in all_terms:
                    brand_map[term] = canon

            elif t == "noise":
                noise_set.update(all_terms)
            

        # --- Multi-type entities (ambiguous) ---
        else:
            if "unit" in types:
                ambiguous_units.update(all_terms)
            if "variant" in types:
                ambiguous_variants.update(all_terms)
            if "brand" in types:
                ambiguous_brands.update(all_terms)
            # Note: products/brands in ambiguity don’t get maps
            # they must be resolved by context classification only

    return (
        unit_map, variant_map, product_map, brand_map, noise_set,
        unambiguous_units, ambiguous_units,
        unambiguous_variants, ambiguous_variants,
        ambiguous_brands   # ✅ new
    )


def build_global_synonym_map(entities):
    """
    Build a synonym normalization map ONLY from entities explicitly marked
    with type=["global_synonym"] (and no other types).

    """
    synonym_map = {}

    for ent in entities:
        types = ent.get("type", [])
        canonical = ent.get("canonical", "").lower().strip()
        synonyms = [s.lower().strip() for s in ent.get("synonyms", [])]

        # ✅ only include if type is exactly ["global_synonym"]
        if len(types) == 1 and types[0] == "global_synonym" and canonical:
            for syn in synonyms:
                synonym_map[syn] = canonical
            # include self mapping
            synonym_map[canonical] = canonical

    return synonym_map

def add_entity_with_tracking(result, entity_counts, entity_type, text, position, debug_units=False, length=1):
    # Always dict with position (+ span length for parameterization)
    if entity_type not in entity_counts:
        entity_counts[entity_type] = {}
    
    current_count = entity_counts[entity_type].get(text.lower(), 0) + 1
    entity_counts[entity_type][text.lower()] = current_count

    entity_obj = {
        "text": text,
        "occurrence": current_count,
        "position": position,
        "length": length  # ✅ span length in spaCy tokens
    }
    result[entity_type].append(entity_obj)

    if debug_units:
        debug_print(f"[DEBUG] Added {entity_type}: {entity_obj}")


def extract_entities(nlp, text: str, entities: list, debug_units=False):
    doc = nlp(text)

    (
        unit_map, variant_map, product_map,
        brand_map, noise_set,
        unambiguous_units, ambiguous_units,
        unambiguous_variants, ambiguous_variants, ambiguous_brands
    ) = build_support_maps(entities)

    result = {
        "brands": [],
        "likely_brands": [],
        "products": [],
        "likely_products": [],
        "variants": [],
        "likely_variants": [],
        "productbrands": [],     # ✅ new group
        "quantities": [],
        "units": []
    }

    entity_counts = {k: {} for k in result.keys()}

    # Track ambiguous candidates for later context classification
    candidate_ambiguous_units = []
    candidate_ambiguous_variants = []
    candidate_ambiguous_brands = []
    candidate_productbrands = []  # ✅ new

    # === Step 1: EntityRuler matches
    for ent in doc.ents:
        lemma = ent.text.lower()
        span_len = ent.end - ent.start

        if ent.label_ == "PRODUCTBRAND":
            add_entity_with_tracking(result, entity_counts, "productbrands",
                                    ent.text, ent.start, debug_units, length=span_len)

        elif ent.label_ == "BRAND":
            add_entity_with_tracking(result, entity_counts, "brands", ent.text, ent.start, debug_units, length=span_len)

        elif ent.label_ == "PRODUCT":
            if lemma not in noise_set:
                add_entity_with_tracking(result, entity_counts, "products", ent.text, ent.start, debug_units, length=span_len)


        elif ent.label_ == "UNIT":
            if lemma not in unit_map:
                continue
            if lemma in ambiguous_units:
                candidate_ambiguous_units.append((lemma, ent.start))
            else:
                add_entity_with_tracking(result, entity_counts, "units", ent.text, ent.start, debug_units, length=span_len)

        elif ent.label_ == "VARIANT":
            if lemma not in variant_map:
                continue
            if lemma in ambiguous_variants:
                candidate_ambiguous_variants.append((lemma, ent.start))
            else:
                add_entity_with_tracking(result, entity_counts, "variants", ent.text, ent.start, debug_units, length=span_len)
    
    # === Step 1b: Classify and promote PRODUCTBRAND spans ===
    if result["productbrands"]:
        product_lex = set(product_map.keys())
        unit_lex = set(unit_map.keys())
        pb_decisions = classify_productbrands(doc, result["productbrands"], product_lex, unit_lex, debug=debug_units)
        for d in pb_decisions:
            if d["label"] == "brand":
                add_entity_with_tracking(result, entity_counts, "brands", d["text"], d["position"], debug_units, length=d["length"])
                _remove_entity_occurrence(result["products"], position=d["position"], length=d["length"])
            else:
                add_entity_with_tracking(result, entity_counts, "products", d["text"], d["position"], debug_units, length=d["length"])
                _remove_entity_occurrence(result["brands"], position=d["position"], length=d["length"])

    # === Step 2: Token-level fallback (same as before)
    for i, token in enumerate(doc):
        if token.ent_type_ in {"BRAND","PRODUCT","VARIANT","UNIT","PRODUCTBRAND"}:
            continue

        clean_text = token.text.rstrip('.,!?;:')
        lemma = clean_text.lower()
        if not lemma.isalpha():
            continue

        if lemma in brand_map:
            add_entity_with_tracking(result, entity_counts, "brands", clean_text, i, debug_units)
        elif lemma in product_map and lemma not in noise_set:
            add_entity_with_tracking(result, entity_counts, "products", clean_text, i, debug_units)
        elif lemma in unambiguous_units:
            add_entity_with_tracking(result, entity_counts, "units", clean_text, i, debug_units)
            if i > 0 and doc[i - 1].pos_ == "NUM":
                add_entity_with_tracking(result, entity_counts, "quantities", doc[i - 1].text, i-1, debug_units)
        elif lemma in ambiguous_units:
            candidate_ambiguous_units.append((lemma, i))
        elif lemma in unambiguous_variants:
            add_entity_with_tracking(result, entity_counts, "variants", clean_text, i, debug_units)
        elif lemma in ambiguous_variants:
            candidate_ambiguous_variants.append((lemma, i))
        elif lemma in ambiguous_brands:
            candidate_ambiguous_brands.append((lemma, i))
        elif lemma in noise_set:
            # ✅ Keep noise tokens for context, just mark them as noise
            if "noise_tokens" not in result:
                result["noise_tokens"] = []
            result["noise_tokens"].append({"text": token.text, "position": i})
            # do NOT 'continue' — preserve them in doc flow
        elif token.pos_ == "PROPN":
            add_entity_with_tracking(result, entity_counts, "likely_brands", token.text, i, debug_units)
        elif token.pos_ == "NOUN":
            add_entity_with_tracking(result, entity_counts, "likely_products", token.text, i, debug_units)
        elif token.pos_ in {"ADJ", "X"}:
            add_entity_with_tracking(result, entity_counts, "likely_variants", token.text, i, debug_units)
        elif token.pos_ == "NUM":
            if not (i + 1 < len(doc) and doc[i + 1].lemma_.lower() in unit_map):
                add_entity_with_tracking(result, entity_counts, "variants", token.text, i, debug_units)

    # === Step 3: Context classification
    # (keep existing units, variants, brands logic)
    if candidate_ambiguous_units:
        entity_texts = [lemma for lemma, _ in candidate_ambiguous_units]
        classification_result = classify_ambiguous_units(text, entity_texts, ambiguous_units, entities, debug=debug_units)
        for lemma, pos in candidate_ambiguous_units:
            if any(u["entity"] == lemma and u["position"] == pos for u in classification_result["units"]):
                add_entity_with_tracking(result, entity_counts, "units", lemma, pos, debug_units)
            elif any(p["entity"] == lemma and p["position"] == pos for p in classification_result["products"]):
                add_entity_with_tracking(result, entity_counts, "products", lemma, pos, debug_units)

    if candidate_ambiguous_variants:
        entity_texts = [lemma for lemma, _ in candidate_ambiguous_variants]
        classification_result = classify_ambiguous_variants(text, entity_texts, ambiguous_variants, entities, debug=debug_units)
        for lemma, pos in candidate_ambiguous_variants:
            if any(v["entity"] == lemma and v["position"] == pos for v in classification_result["variants"]):
                add_entity_with_tracking(result, entity_counts, "variants", lemma, pos, debug_units)
            elif any(p["entity"] == lemma and p["position"] == pos for p in classification_result["products"]):
                add_entity_with_tracking(result, entity_counts, "products", lemma, pos, debug_units)

    if candidate_ambiguous_brands:
        entity_texts = [lemma for lemma, _ in candidate_ambiguous_brands]
        classification_result = classify_ambiguous_brands(doc, entity_texts, ambiguous_brands, entities, debug=debug_units)
        for lemma, pos in candidate_ambiguous_brands:
            if any(b["entity"] == lemma and b["position"] == pos for b in classification_result["brands"]):
                add_entity_with_tracking(result, entity_counts, "brands", lemma, pos, debug_units)
            elif any(p["entity"] == lemma and p["position"] == pos for p in classification_result["products"]):
                add_entity_with_tracking(result, entity_counts, "products", lemma, pos, debug_units)

    # === Step 3d: Handle productbrand disambiguation (brand vs product)
    if candidate_productbrands:
        product_lex = set(product_map.keys())
        unit_lex = set(unit_map.keys())
        classification_result = classify_productbrands(doc, candidate_productbrands, product_lex, unit_lex, debug=debug_units)
        for pb in candidate_productbrands:
            if any(b["position"] == pb["position"] for b in classification_result["brands"]):
                add_entity_with_tracking(result, entity_counts, "brands", pb["text"], pb["position"], debug_units)
            elif any(p["position"] == pb["position"] for p in classification_result["products"]):
                add_entity_with_tracking(result, entity_counts, "products", pb["text"], pb["position"], debug_units)

    return result


def pre_normalizationx(text):
    """
    Normalize text before spaCy processing:
    - Handle apostrophes and curly quotes (Kellogg's → Kelloggs)
    - Split digit-letter boundaries (5kg → 5 kg)
    - **Convert "a/an/one + unit" → "1 + unit"**  ← NEW
    - Add spaces around punctuation
    - Lowercase and normalize whitespace
    """
    import re
    import unicodedata

    # 1️⃣ Normalize Unicode (e.g., curly quotes)
    text = unicodedata.normalize("NFKC", text)
    text = normalize_hyphens(text)


    # 2️⃣ Normalize apostrophes and possessives
    text = text.replace("'", "'").replace("`", "'")
    text = re.sub(r"(\w)'s\b", r"\1s", text)
    text = re.sub(r"(\w)'(\w)", r"\1\2", text)

    # 3️⃣ Split digits and letters (5kg → 5 kg)
    text = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", text)
    text = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", text)

    # 🆕 4️⃣ Convert "a/an/one + unit" patterns to "1 + unit"
    # Match common units: bag, tin, crate, bottle, pack, kg, etc.
    unit_pattern = r"\b(bag|tin|crate|bottle|pack|box|carton|kg|g|lb|liter|ml|case|sack|jar|can)s?\b"
    text = re.sub(rf"\b(a|an|one)\s+({unit_pattern})", r"1 \2", text, flags=re.IGNORECASE)

    # 5️⃣ Add spaces around punctuation
    text = re.sub(r"([.!?;:,])(?=\S)", r"\1 ", text)
    text = re.sub(r"(?<=\S)([.!?;:,])", r" \1", text)

    # 6️⃣ Normalize spaces
    text = re.sub(r"\s+", " ", text).strip()

    # 7️⃣ Lowercase
    text = text.lower()

    return text


def pre_normalization(text):
    """
    Normalize text before spaCy processing:
    - Handle apostrophes and curly quotes (Kellogg's → Kelloggs)
    - Split digit-letter boundaries (5kg → 5 kg)
    - Convert "a/an/one + unit" → "1 + unit"
    - Add spaces around punctuation
    - Lowercase and normalize whitespace
    """
    import re
    import unicodedata

    # 1️⃣ Normalize Unicode (e.g., curly quotes)
    text = unicodedata.normalize("NFKC", text)

    # ✅ NEW: Fix spaced or dash variants (e.g. "coca - cola" → "coca-cola")
    text = re.sub(r"\s*[-–—−]\s*", "-", text)

    # 2️⃣ Normalize apostrophes and possessives
    text = text.replace("'", "'").replace("`", "'")
    text = re.sub(r"(\w)'s\b", r"\1s", text)
    text = re.sub(r"(\w)'(\w)", r"\1\2", text)

    # 3️⃣ Split digits and letters (5kg → 5 kg)
    text = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", text)
    text = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", text)

    # 4️⃣ Convert "a/an/one + unit" → "1 + unit"
    unit_pattern = r"\b(bag|tin|crate|bottle|pack|box|carton|kg|g|lb|liter|ml|case|sack|jar|can)s?\b"
    text = re.sub(rf"\b(a|an|one)\s+({unit_pattern})", r"1 \2", text, flags=re.IGNORECASE)

    # 5️⃣ Add spaces around punctuation
    text = re.sub(r"([.!?;:,])(?=\S)", r"\1 ", text)
    text = re.sub(r"(?<=\S)([.!?;:,])", r" \1", text)

    # 6️⃣ Normalize spaces
    text = re.sub(r"\s+", " ", text).strip()

    # 7️⃣ Lowercase
    text = text.lower()

    return text

def post_normalize_parameterized_text(text):
    """
    Clean and normalize parameterized text AFTER placeholders are inserted.
    Ensures placeholders are space-separated, punctuation is spaced, and
    text is clean for downstream token-level alignment or model input.
    """
    import re

    placeholder_pattern = r"(producttoken|brandtoken|varianttoken|unittoken|quantitytoken)"

    # 1️⃣ Split consecutive placeholders
    text = re.sub(rf"({placeholder_pattern})(?={placeholder_pattern})", r"\1 ", text)

    # 2️⃣ Add space between placeholder and adjacent letters
    text = re.sub(rf"({placeholder_pattern})([a-zA-Z])", r"\1 \2", text)
    text = re.sub(rf"([a-zA-Z])({placeholder_pattern})", r"\1 \2", text)

    # 3️⃣ Add spaces around punctuation near placeholders
    text = re.sub(rf"({placeholder_pattern})([.,!?;:])", r"\1 \2", text)
    text = re.sub(rf"([.,!?;:])({placeholder_pattern})", r"\1 \2", text)

    # 4️⃣ Collapse multiple spaces and trim
    text = re.sub(r"\s+", " ", text).strip()

    # 5️⃣ Lowercase
    text = text.lower()

    return text


def build_parameterized_sentence_from_docx(doc, result):
    tokens = [t.text.lower() for t in doc]
    parameterized_tokens = tokens[:]

    placeholders = {
        "brands": "brandtoken",
        "products": "producttoken",
        "units": "unittoken",
        "variants": "varianttoken",
        "productbrands": "productbrandtoken"  # ✅ new token
    }

    replacements = []
    for entity_type, entities in result.items():
        if entity_type in placeholders:
            placeholder = placeholders[entity_type]
            for ent in entities:
                if isinstance(ent, dict) and "position" in ent:
                    start = ent["position"]
                    length = ent.get("length", 1)
                    end = start + max(1, length)
                    replacements.append((start, end, placeholder))

    replacements.sort(key=lambda x: (x[0], -(x[1]-x[0])), reverse=True)
    for start, end, placeholder in replacements:
        parameterized_tokens[start:end] = [placeholder]

    return " ".join(parameterized_tokens)


def build_parameterized_sentence_from_doc(doc, result):
    tokens = [t.text.lower() for t in doc]
    parameterized_tokens = tokens[:]

    # ✅ productbrands intentionally excluded
    placeholders = {
        "brands": "brandtoken",
        "products": "producttoken",
        "units": "unittoken",
        "variants": "varianttoken"
    }

    replacements = []
    for entity_type, entities in result.items():
        if entity_type in placeholders:
            placeholder = placeholders[entity_type]
            for ent in entities:
                if isinstance(ent, dict) and "position" in ent:
                    start = ent["position"]
                    length = ent.get("length", 1)
                    end = start + max(1, length)
                    replacements.append((start, end, placeholder))

    # Replace longer spans first (so multi-word entities stay intact)
    replacements.sort(key=lambda x: (x[0], -(x[1] - x[0])), reverse=True)
    for start, end, placeholder in replacements:
        parameterized_tokens[start:end] = [placeholder]
    
    debug_print("[DEBUG] Tokens before replacement:", tokens)
    debug_print("[DEBUG] Replacements:", replacements)
    debug_print("[DEBUG] Parameterized tokens after replacement:", parameterized_tokens)


    return " ".join(parameterized_tokens)

def build_parameterized_sentence(sentence, result):
    """(legacy) Kept for compatibility but no longer used."""
    tokens = sentence.lower().split()
    parameterized_tokens = tokens[:]

    placeholders = {
        "brands": "brandtoken",
        "products": "producttoken",
        "units": "unittoken",
        "variants": "varianttoken"
    }

    replacements = []
    for entity_type, entities in result.items():
        if entity_type in placeholders:
            placeholder = placeholders[entity_type]
            for ent in entities:
                if isinstance(ent, dict) and "position" in ent:
                    ent_text = ent["text"].lower()
                    ent_position = ent["position"]
                    ent_tokens = ent_text.split()
                    start = ent_position
                    end = start + len(ent_tokens)
                    replacements.append((start, end, placeholder))

    # Sort by start position descending, and longer spans first
    replacements.sort(key=lambda x: (x[0], -(x[1]-x[0])), reverse=True)

    for start, end, placeholder in replacements:
        parameterized_tokens[start:end] = [placeholder]

    return " ".join(parameterized_tokens)


def simplify_result(result, doc):
    """
    Simplify result by removing positions and adding parameterized sentence,
    using the *same spaCy doc* that was used to generate entity positions.

    Keeps productbrands in final output (for debugging or analytics),
    but ensures psentence reflects promoted tokens.
    """
    simplified = {
        "brands": [],
        "likely_brands": [],
        "products": [],
        "likely_products": [],
        "variants": [],
        "quantities": [],
        "units": [],
        "productbrands": [],   # ✅ keep this in final output
        "osentence": doc.text,
        "psentence": ""
    }

    # ✅ Copy over all entity texts cleanly
    for entity_list_name in [
        "brands",
        "products",
        "units",
        "variants",
        "likely_brands",
        "likely_products",
        "productbrands"   # include new group
    ]:
        for entity_obj in result.get(entity_list_name, []):
            if isinstance(entity_obj, dict):
                simplified[entity_list_name].append(entity_obj["text"])
            else:
                simplified[entity_list_name].append(entity_obj)

    # Quantities remain as-is
    simplified["quantities"] = result.get("quantities", [])

    # ✅ Parameterize based on all entity positions (including productbrands)
    psentence_raw = build_parameterized_sentence_from_doc(doc, result)
    simplified["psentence"] = post_normalize_parameterized_text(psentence_raw)

    return simplified



def extract_entities_with_parameterization(nlp, text: str, entities: list, debug_units=False):
    """
    Extract entities and return simplified result with parameterized sentence.
    Includes:
      - Pre-normalization (spacing, apostrophes, etc.)
      - Longest-phrase synonym normalization
      - spaCy entity extraction
      - Canonicalization of detected entities
    """
    text = normalize_hyphens(text)

    # Step 1️⃣ — Pre-normalize the input
    normalized_text = pre_normalization(text)

    normalized_text = normalize_plural_to_singular(normalized_text, nlp)


    # Step 2️⃣ — Build synonym map and normalize longest valid phrases
    synonym_map = build_global_synonym_map(entities)
    normalized_text = normalize_longest_phrases(normalized_text, synonym_map)

    if debug_units:
        debug_print("\n[DEBUG] === Normalized Input ===")
        debug_print(normalized_text)
        debug_print("===============================")

    # Step 3️⃣ — spaCy entity detection
    doc = nlp(normalized_text)
    result = extract_entities(nlp, normalized_text, entities, debug_units)

    # Step 4️⃣ — Simplify + parameterize
    simplified_result = simplify_result(result, doc)
    final_result = {k: v for k, v in simplified_result.items() if k != "noise_tokens"}

    # Step 5️⃣ — Canonicalize entities AFTER parameterization
    (
        unit_map, variant_map, product_map, brand_map, noise_set,
        _, _,  # unambiguous_units, ambiguous_units (not used here)
        _, _,  # unambiguous_variants, ambiguous_variants (not used here)
        _      # ambiguous_brands (not used here)
    ) = build_support_maps(entities)

    result = canonicalize_entities(result, unit_map, variant_map, product_map, brand_map, debug_units)

    # Step 6️⃣ — Attach canonicalized entities to simplified output (sorted by position)
    final_result["brands"] = [e["text"] if isinstance(e, dict) else e for e in sorted(result["brands"], key=lambda x: x.get("position", 0) if isinstance(x, dict) else 0)]
    final_result["products"] = [e["text"] if isinstance(e, dict) else e for e in sorted(result["products"], key=lambda x: x.get("position", 0) if isinstance(x, dict) else 0)]
    final_result["units"] = [e["text"] if isinstance(e, dict) else e for e in sorted(result["units"], key=lambda x: x.get("position", 0) if isinstance(x, dict) else 0)]
    final_result["variants"] = [e["text"] if isinstance(e, dict) else e for e in sorted(result["variants"], key=lambda x: x.get("position", 0) if isinstance(x, dict) else 0)]



    if debug_units:
        debug_print("[DEBUG] === Final Simplified Result ===")
        debug_print(json.dumps(final_result, indent=2))
        debug_print("======================================")
    
    debug_print("[DEBUG] Tokens before parameterization:", [t.text for t in doc])
    debug_print("[DEBUG] Entities with positions:", {k: [(e.get('text'), e.get('position')) for e in v if isinstance(e, dict)] for k,v in result.items()})
    debug_print("[DEBUG] Final simplified result:", final_result)


    return final_result




def canonicalize_entities(result, unit_map, variant_map, product_map, brand_map, debug=False):
    """
    Canonicalize all entities in the result using the provided maps.
    """
    if debug:
        debug_print("[DEBUG] Canonicalizing entities")
    
    # Canonicalize brands
    for brand_obj in result["brands"]:
        if isinstance(brand_obj, dict):
            original_text = brand_obj["text"]
            canonical_text = brand_map.get(original_text.lower(), original_text)
            brand_obj["text"] = canonical_text
            if debug:
                debug_print(f"[DEBUG] Canonicalized brand: '{original_text}' → '{canonical_text}'")
    
    # Canonicalize products
    for product_obj in result["products"]:
        if isinstance(product_obj, dict):
            original_text = product_obj["text"]
            canonical_text = product_map.get(original_text.lower(), original_text)
            product_obj["text"] = canonical_text
            if debug:
                debug_print(f"[DEBUG] Canonicalized product: '{original_text}' → '{canonical_text}'")
    
    # Canonicalize units
    for unit_obj in result["units"]:
        if isinstance(unit_obj, dict):
            original_text = unit_obj["text"]
            canonical_text = unit_map.get(original_text.lower(), original_text)
            unit_obj["text"] = canonical_text
            if debug:
                debug_print(f"[DEBUG] Canonicalized unit: '{original_text}' → '{canonical_text}'")
    
    # Canonicalize variants
    for variant_obj in result["variants"]:
        if isinstance(variant_obj, dict):
            original_text = variant_obj["text"]
            canonical_text = variant_map.get(original_text.lower(), original_text)
            variant_obj["text"] = canonical_text
            if debug:
                debug_print(f"[DEBUG] Canonicalized variant: '{original_text}' → '{canonical_text}'")
    
    return result


def classify_ambiguous_units(sentence, entity_list, ambiguous_units, entities, debug=False):
    """
    Classify ambiguous entities as units or products.
    Returns two lists:
      - products: includes "product" and "ignored"
      - units: includes "unit"
    """
    tokens = sentence.lower().replace(',', ' ,').replace('.', ' .').split()
    
    # Extract brand words from global entities
    brand_words = set()
    for ent in entities:
        if ent["type"] == "brand":
            brand_words.add(ent["canonical"].lower())
            brand_words.update(s.lower() for s in ent.get("synonyms", []))

    results = {"products": [], "units": []}

    # Collect token positions of each entity (exact match)
    token_entities = [(i, token.rstrip('.,!?;:')) for i, token in enumerate(tokens)]

    counters = {}
    used_positions = {e: 0 for e in entity_list}

    for entity in entity_list:
        counters[entity] = counters.get(entity, 0) + 1

        # Find next matching token position
        positions = [i for i, tok in token_entities if tok == entity]
        if not positions or used_positions[entity] >= len(positions):
            if debug:
                debug_print(f"{entity}: not found in tokens")
            continue

        pos = positions[used_positions[entity]]
        used_positions[entity] += 1

        prev_token = tokens[pos-1] if pos > 0 else None
        next_token = tokens[pos+1] if pos+1 < len(tokens) else None

        if debug:
            debug_print(f"Analyzing '{entity}' at position {pos}")
            debug_print(f"  Prev: {prev_token or 'N/A'} | Next: {next_token or 'N/A'}")

        # Only classify entities that are ambiguous units.
        if entity not in ambiguous_units:
            if debug:
                debug_print(f"  Skipping non-ambiguous entity '{entity}' in unit classification")
            continue

        label = "product"  # default for ambiguous entity

        # Rule 3: brand (overrides all)
        if prev_token and prev_token in brand_words:
            label = "product"
            if debug: debug_print(f"  Rule 3: brand '{prev_token}' → product")
        # Rule 1: number before entity
        elif prev_token and prev_token.isdigit():
            label = "unit"
            if debug: debug_print(f"  Rule 1: number '{prev_token}' → unit")
        # Rule 2: followed by "of"
        elif next_token == "of":
            label = "unit"
            if debug: debug_print("  Rule 2: followed by 'of' → unit")

        # Add to results for ambiguous entities only
        if label == "unit":
            results["units"].append({"entity": entity, "position": pos})
        else:
            results["products"].append({"entity": entity, "position": pos})

        if debug:
            debug_print(f"  Final classification: {label}")

    return results


def classify_ambiguous_variants(sentence, entity_list, ambiguous_variants, entities, debug=False):
    """
    Classify ambiguous entities as variants or products based on context.
    Returns two lists:
      - products: includes "product" and "ignored"
      - variants: includes "variant"
    """
    tokens = sentence.lower().replace(',', ' ,').replace('.', ' .').split()

    # Extract brand and product words from global entities
    brand_words, product_words = set(), set()
    for ent in entities:
        if ent["type"] == "brand":
            brand_words.add(ent["canonical"].lower())
            brand_words.update(s.lower() for s in ent.get("synonyms", []))
        elif ent["type"] == "product":
            product_words.add(ent["canonical"].lower())
            product_words.update(s.lower() for s in ent.get("synonyms", []))

    results = {"products": [], "variants": []}

    # Collect token positions of each entity (exact match)
    token_entities = [(i, token.rstrip('.,!?;:')) for i, token in enumerate(tokens)]
    counters = {}
    used_positions = {e: 0 for e in entity_list}

    for entity in entity_list:
        counters[entity] = counters.get(entity, 0) + 1

        # Find next matching token position
        positions = [i for i, tok in token_entities if tok == entity]
        if not positions or used_positions[entity] >= len(positions):
            if debug:
                debug_print(f"{entity}: not found in tokens")
            continue

        pos = positions[used_positions[entity]]
        used_positions[entity] += 1

        prev_token = tokens[pos-1] if pos > 0 else None
        next_token = tokens[pos+1] if pos+1 < len(tokens) else None

        if debug:
            debug_print(f"Analyzing '{entity}' at position {pos}")
            debug_print(f"  Prev: {prev_token or 'N/A'} | Next: {next_token or 'N/A'}")

        # Only classify entities that are ambiguous variants
        if entity not in ambiguous_variants:
            if debug:
                debug_print(f"  Skipping non-ambiguous entity '{entity}' in variant classification")
            continue

        label = "product"  # default for ambiguous variant

        # Rule 3: if preceded by a known product, treat as variant
        if prev_token and prev_token in product_words:
            label = "variant"
            if debug: debug_print(f"  Rule 3: product '{prev_token}' → variant")
        # Rule 1: if followed by a known product, treat as variant
        elif next_token and next_token in product_words:
            label = "variant"
            if debug: debug_print(f"  Rule 1: followed by product '{next_token}' → variant")
        # Rule 2: if followed by "size", "color", etc. (contextual cues)
        elif next_token in {"size", "color", "flavor"}:
            label = "variant"
            if debug: debug_print(f"  Rule 2: followed by '{next_token}' → variant")
        # Otherwise: leave as product-like word
        else:
            label = "product"

        # Add to results
        if label == "variant":
            results["variants"].append({"entity": entity, "position": pos})
        else:
            results["products"].append({"entity": entity, "position": pos})

        if debug:
            debug_print(f"  Final classification: {label}")

    return results

def classify_ambiguous_brands(doc, entity_list, ambiguous_brands, entities, debug=False):
    """
    Classify ambiguous entities as BRAND or PRODUCT based on context.
    Uses spaCy POS tagging and already-tokenized entities.
    """
    results = {"brands": [], "products": []}
    used_positions = {e: 0 for e in entity_list}

    # Build lookup for already tagged product tokens (optional)
    known_product_tokens = {t.text.lower() for t in doc if t.ent_type_ == "PRODUCT" or t.text.lower() == "producttoken"}

    for entity in entity_list:
        # Find all matching tokens in doc
        matches = [t for t in doc if t.text.lower() == entity]
        if not matches:
            if debug:
                debug_print(f"{entity}: not found in doc")
            continue

        token = matches[used_positions[entity]] if used_positions[entity] < len(matches) else None
        used_positions[entity] += 1

        if not token:
            continue

        next_token = doc[token.i + 1] if token.i + 1 < len(doc) else None
        prev_token = doc[token.i - 1] if token.i - 1 >= 0 else None

        if debug:
            debug_print(f"\nAnalyzing '{entity}' (pos={token.i})")
            debug_print(f"  Prev: {prev_token.text if prev_token else 'N/A'} ({prev_token.pos_ if prev_token else '-'})")
            debug_print(f"  Next: {next_token.text if next_token else 'N/A'} ({next_token.pos_ if next_token else '-'})")

        # Skip if not ambiguous
        if entity not in ambiguous_brands:
            continue

        label = "product"  # default

        # === Rule 1: followed by known PRODUCT or producttoken
        if next_token and next_token.text.lower() in known_product_tokens:
            label = "brand"
            if debug: debug_print(f"  Rule 1: next_token '{next_token.text}' is PRODUCT → BRAND")

        # === Rule 2: followed by generic NOUN (not punctuation)
        elif next_token and next_token.pos_ == "NOUN" and next_token.text.isalpha():
            label = "brand"
            if debug: debug_print(f"  Rule 2: next_token '{next_token.text}' is NOUN → BRAND")

        # === Rule 3: followed by unit or number → PRODUCT
        elif next_token and (next_token.pos_ == "NUM" or next_token.text.lower() in {"unittoken", "quantitytoken"}):
            label = "product"
            if debug: debug_print(f"  Rule 3: next_token '{next_token.text}' is NUM/unit → PRODUCT")

        # === Default
        else:
            label = "product"
            if debug: debug_print("  Default → PRODUCT")

        results[f"{label}s"].append({"entity": entity, "position": token.i})
        if debug:
            debug_print(f"  Final classification: {label.upper()}")

    return results


def classify_productbrands(doc, productbrands, product_lex, unit_lex, debug=False):
    """
    Promote each productbrand to BRAND or PRODUCT.
    Rule:
      - If followed by a known product → BRAND
      - Otherwise → PRODUCT (default)
    """
    results = []
    for pb in productbrands:
        if not isinstance(pb, dict):
            continue
        start = pb["position"]
        end = start + pb.get("length", 1)
        next_token = doc[end] if end < len(doc) else None
        label = "product"  # default

        if next_token:
            next_lower = next_token.text.lower()

            # ✅ Rule: followed by known product → brand
            if next_lower in product_lex:
                label = "brand"

            # Optional: if followed by unit/number, it's definitely product
            elif next_lower in unit_lex or next_token.pos_ == "NUM":
                label = "product"

        if debug:
            nxt = next_token.text if next_token else "None"
            debug_print(f"[DEBUG] Analyzing productbrand '{pb['text']}' at pos={start}")
            debug_print(f"  Next: {nxt} ({next_token.pos_ if next_token else '-'})")
            debug_print(f"  Final classification → {label.upper()}")

        results.append({
            "text": pb["text"],
            "position": start,
            "length": pb["length"],
            "label": label
        })

    return results



def test_ambiguous_units():
    """Simple test function."""
    test_cases = [
        {
            "sentence": "Add 2 bags of rice and 1 Gucci bag.",
            "entity_list": ["rice", "bags", "bag"],
            "ambiguous_units": ["bags", "bag"],
            "expected_units": [{"entity": "bags", "position": 2}],
            "expected_products": [{"entity": "rice", "position": 4}, {"entity": "bag", "position": 8}]
        },
        {
            "sentence": "I need a case file for my lawyer.",
            "entity_list": ["case"],
            "ambiguous_units": ["case"],
            "expected_units": [],
            "expected_products": [{"entity": "case", "position": 3}]
        }
    ]
    
    print("=== Testing Ambiguous Units ===\n")
    
    for i, test in enumerate(test_cases, 1):
        print(f"Test {i}: {test['sentence']}")
        result = classify_ambiguous_units(
            test["sentence"], 
            test["entity_list"], 
            test["ambiguous_units"],
            [],  # empty entities list for testing
            debug=True
        )
        
        # Debug the tokenization
        tokens = test["sentence"].lower().replace(',', ' ,').replace('.', ' .').split()
        print(f"Tokens: {tokens}")
        print(f"Entity positions: {[(i, token) for i, token in enumerate(tokens) if token.rstrip('.,!?;:') in test['entity_list']]}")
        print(f"Result: {result}")
        
        # Check if results match expected
        units_match = result["units"] == test["expected_units"]
        products_match = result["products"] == test["expected_products"]
        overall_match = units_match and products_match
        
        print(f"Units match: {'✅' if units_match else '❌'} (got {result['units']}, expected {test['expected_units']})")
        print(f"Products match: {'✅' if products_match else '❌'} (got {result['products']}, expected {test['expected_products']})")
        print(f"Overall: {'✅ PASS' if overall_match else '❌ FAIL'}")
        print("-" * 40)


def main():
    sentences = [
        # Groceries
        "Add 3 large imported bags of Golden Penny rice and remove 2 cartons of Indomie noodles.",
        "Please set my cold Coca Cola soda order to 6 bottles instead of 4.",
        "Remove 1 small packet of crayfish and add 2 tins of Peak milk powder.",
        "Can you check if fresh organic yam tubers from Dangote are in stock?",
        "I want to add 5kg of local brown beans and drop 2 bags of rice.",
        "Replace my current 3 cartons of Indomie noodles with 2 cartons of Supreme noodles.",
        "Add 1 imported large bottle of Kings vegetable oil and remove 2 small ones.",
        "Set garri flour to 10kg and cancel the 5kg bag I added earlier.",
        "Please include 4 packs of cold Fanta and delete 1 pack of Coca Cola.",
        "Adjust my cart to have 2 cartons of Indomie Onion noodles instead of Chicken flavor.",
        "Add 2 tins of sweetened condensed milk from Peak and remove 1 tin of unsweetened.",
        "Do you stock Dangote white sugar in 50kg bags?",
        "Add 6 bottles of cold Heineken beer and remove 3 bottles of Guinness.",
        "Set my bread order to 5 loaves and drop the 2 butter packets.",
        "Check if you have imported Golden Penny rice available in 25kg sacks.",
        "Cancel 3 bottles of Sprite and add 2 cold Fanta Orange bottles.",
        "Change my current yam order from 5 tubers to 8 fresh tubers.",
        "Do you have large imported sweetened Peak milk powder in stock?",
        "Add 10 sachets of Milo chocolate drink mix and remove 2 tins of Ovaltine.",
        "Switch my sugar order from 1 bag of Dangote white sugar to 2 bags of brown sugar.",

        # Fashion
        "Add 1 pair of red Adidas sneakers size 44 and remove the Nike black pair.",
        "Please check if you have Gucci leather handbags in brown available.",
        "Replace my order of 2 pairs of blue jeans with 3 pairs of black chinos.",
        "Set my T-shirt order to 5 white V-neck shirts and cancel the round-neck ones.",
        "Include 2 formal shirts size 42 and drop the casual polo shirts.",
        "Can you see if Zara slim fit trousers in navy blue are in stock?",
        "Remove 1 pair of worn-out socks and add 3 pairs of cotton ankle socks.",
        "Please adjust my order to 2 pairs of loafers and cancel the sneakers.",
        "Do you have large-size hoodies from H&M in grey color?",
        "Add 1 leather belt from Tommy Hilfiger and remove the fabric one.",
        "Swap my jacket order from 1 bomber jacket to 2 denim jackets.",
        "Please include 4 pairs of underwear and delete 1 pair of shorts.",
        "Add 2 silk ties in dark red and remove 1 wool scarf.",
        "Check if you have Puma running shorts in medium size.",
        "Set my order to 3 white cotton shirts and cancel 2 linen shirts.",
        "Please get me 1 pair of Levi’s 501 jeans and drop the chinos.",
        "Add 1 trench coat in beige and remove 1 black leather jacket.",
        "Can you verify if Nike Air Jordan sneakers size 43 are available?",
        "Change my suit order from slim fit to classic fit, keeping the same size.",
        "Add 1 winter jacket with fur lining and remove the raincoat.",

        # Beauty
        "Add 2 bottles of Dove body lotion and remove 1 of Vaseline.",
        "Please set my order to 3 lipsticks from MAC in red shade.",
        "Remove 1 jar of Nivea cream and add 2 jars of Shea butter.",
        "Check if Maybelline waterproof mascara is available in black.",
        "Replace my current shampoo with 2 bottles of Head & Shoulders anti-dandruff.",
        "Add 1 compact powder from Fenty Beauty and drop the Revlon one.",
        "Please include 2 packs of cotton pads and delete 1 pack of wipes.",
        "Set my perfume order to 1 bottle of Chanel No. 5 instead of Dior.",
        "Can you see if Clinique moisturizer in 50ml jars is in stock?",
        "Add 3 nail polishes in pastel colors and remove 1 dark shade.",
        "Swap my foundation from L’Oréal to Estée Lauder, same shade.",
        "Add 1 eyeliner pencil from Bobbi Brown and remove the Maybelline one.",
        "Please adjust my skincare order to 2 serums and cancel the toner.",
        "Check if you have Garnier micellar water in 400ml bottles.",
        "Add 2 bottles of Neutrogena face wash and drop 1 from Cetaphil.",
        "Change my body wash order from Dove to Palmolive, same size.",
        "Please get me 1 pack of disposable razors and remove the electric shaver.",
        "Add 1 lipstick in nude shade and remove the bright pink one.",
        "Verify if Olay anti-aging cream in 100ml jars is available.",
        "Add 2 boxes of hair dye in chestnut brown and drop 1 in black."
    ]

    sentences = [
        "Add 2 bags of rice and 1 Gucci bag.",
        "Remove 1 case of Heineken beer and add 2 cases of Pepsi.",
        "I need a case file for my lawyer.",
        "Add 3 packs of Indomie noodles and remove 1 pack of lipstick.",
        "Please include 1 box of cornflakes and 2 boxes of soap.",
        "She bought a jewelry box from Zara.",
        "Add 1 sack of garri and remove 2 sacks of yam.",
        "Get me 1 pair of sneakers size 43.",
        "Add 400ml bottle of Garnier micellar water.",
        "Set my order to 1 bag of beans, 1 case of beer, and 1 box of cereal.",
        "do you sell coca cola soda"
    ]


    sentences = [
        "do you sell coca cola soda", 
        "do you sell coca cola",
        "do you sell pepsi soda", 
        "do you sell pepsi", 
        "do you sell coca cola soda and pepsi and indomie noodles", 
        "do you sell coca cola soda and pepsi and noodles",
        "Add 2 bags of Mama Gold rice, 3 tins of Peak milk, and a crate of Coca-Cola to my cart."
        
        ]



    nlp, entities = init_nlp_with_entities()   # load spaCy + entity patterns once

    # Run ambiguous unit classification tests first
    print("=" * 60)
    print("RUNNING AMBIGUOUS UNIT CLASSIFICATION TESTS")
    print("=" * 60)
    
    print("\n" + "=" * 60)
    print("RUNNING MAIN ENTITY EXTRACTION TESTS")
    print("=" * 60)
    
    for i, s in enumerate(sentences, start=1):
        print(f"{i:02d}. {s}")
        # Enable debug for the first sentence only
        debug_mode = (i == 1)
        if debug_mode:
            print("=== DEBUG MODE ENABLED ===")
        result = extract_entities_with_parameterization(nlp, s, entities, debug_units=debug_mode)
        print(result)
        print()






if __name__ == "__main__":
    main()

# if __name__ == "__main__":
#     # Test ambiguous units function
#     test_ambiguous_units()
    
#     print("\n" + "=" * 60)
#     print("TESTING INTEGRATED EXTRACTION")
#     print("=" * 60)
    
#     # Test the integrated extraction
#     nlp, entities = init_nlp_with_entities()
#     test_sentence = "Add 2 bags of rice and 1 Gucci bag."
#     print(f"Testing: {test_sentence}")
#     result = extract_entities(nlp, test_sentence, entities, debug_units=True)
#     print(f"Final result: {result}")
