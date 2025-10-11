#!/usr/bin/env python3
import os
import re
from allennlp.predictors.predictor import Predictor
import allennlp_models.tagging
import spacy

# ---------------------------------------------------------------------
# MODEL SETUP
# ---------------------------------------------------------------------
MODEL_PATH = os.path.join("store", "structured-prediction-srl-bert.2020.12.15.tar.gz")
if not os.path.exists(MODEL_PATH):
    raise FileNotFoundError(
        f"❌ Missing model file: {MODEL_PATH}\n"
        "Download it first from:\n"
        "https://storage.googleapis.com/allennlp-public-models/"
        "structured-prediction-srl-bert.2020.12.15.tar.gz"
    )

print("🚀 Loading SpaCy and AllenNLP models...")
nlp = spacy.load("en_core_web_sm")
predictor = Predictor.from_path(MODEL_PATH)
print("✅ Models loaded.")


# ---------------------------------------------------------------------
# NORMALIZATION HELPERS
# ---------------------------------------------------------------------
def normalize_cart_reference(action: str, phrase: str) -> str:
    """
    Normalize 'trolley', 'basket', etc. → 'cart'
    only when the action clearly involves cart interaction.
    """
    if not phrase:
        return phrase

    if action.lower() in {"add", "remove", "show", "checkout"}:
        pattern = r"\b(to|in|from|into|inside)?\s*(my\s+)?(trolley|basket|bag|checkout)\b"
        phrase = re.sub(pattern, r"to my cart", phrase, flags=re.I)
    return phrase


def clean_tokens(words):
    """Merge SpaCy-like tokens and fix apostrophe/contraction spacing."""
    joined = " ".join(words)

    # Merge split hyphenated words (e.g., t - shirts → t-shirts)
    joined = re.sub(r"\s*-\s*", "-", joined)

    # Fix English contractions (SpaCy tends to tokenize them badly)
    contractions = [
        (r"\b([A-Za-z])\s+’\s*s\b", r"\1’s"),  # there ’s → there’s
        (r"\b([A-Za-z])\s+’\s*re\b", r"\1’re"),
        (r"\b([A-Za-z])\s+’\s*m\b", r"\1’m"),
        (r"\b([A-Za-z])\s+’\s*d\b", r"\1’d"),
        (r"\b([A-Za-z])\s+’\s*ll\b", r"\1’ll"),
        (r"\b([A-Za-z])\s+’\s*ve\b", r"\1’ve"),
        (r"\b([A-Za-z])\s+’\s*t\b", r"\1’t"),
        (r"\b([A-Za-z])\s+’\s*em\b", r"\1’em"),
    ]
    for pattern, repl in contractions:
        joined = re.sub(pattern, repl, joined, flags=re.IGNORECASE)

    # Generic apostrophe merges
    joined = re.sub(r"\s+([’'])([A-Za-z])", r"\1\2", joined)
    joined = re.sub(r"([A-Za-z])\s+([’'])([A-Za-z])", r"\1\2\3", joined)

    # Remove spaces before punctuation
    joined = re.sub(r"\s+([?.!,])", r"\1", joined)

    return joined.strip()


# ---------------------------------------------------------------------
# SEMANTIC ROLE EXTRACTION
# ---------------------------------------------------------------------
def extract_roles(result, frame):
    """Extract SRL roles cleanly, no number conversion, non-breaking."""
    verb = frame["verb"]
    tags = frame["tags"]
    words = result["words"]

    current_role, roles, current_tokens = None, {}, []

    for tag, word in zip(tags, words):
        if tag.startswith("B-"):
            if current_role and current_tokens:
                roles[current_role] = clean_tokens(current_tokens)
            current_role = tag[2:]
            current_tokens = [word]
        elif tag.startswith("I-") and current_role == tag[2:]:
            current_tokens.append(word)
        else:
            if current_role and current_tokens:
                roles[current_role] = clean_tokens(current_tokens)
                current_role, current_tokens = None, []

    if current_role and current_tokens:
        roles[current_role] = clean_tokens(current_tokens)

    # Drop meaningless “me”, “us”, “yourself” for certain verbs
    if verb.lower() in {"show", "tell", "give", "send", "display"}:
        for key in ["ARG2", "ARGM-PRD"]:
            if key in roles and roles[key].lower() in {"me", "us", "yourself"}:
                del roles[key]

    # Normalize cart references
    for key in ["ARG2", "ARGM-DIR", "ARGM-LOC"]:
        if key in roles:
            roles[key] = normalize_cart_reference(verb, roles[key])

    # Join roles in logical order
    arg_text = " ".join(
        roles[r] for r in ["ARG1", "ARG2", "ARGM-DIR", "ARGM-LOC", "ARGM-MNR"]
        if r in roles
    )

    return f"{verb.capitalize()} {arg_text}".strip()


# ---------------------------------------------------------------------
# MAIN SPLITTER
# ---------------------------------------------------------------------
def split_semantic_commands(sentence):
    """Split a sentence into meaningful actionable commands."""
    result = predictor.predict(sentence=sentence)
    raw_cmds = [extract_roles(result, frame) for frame in result["verbs"]]
    raw_cmds = [cmd for cmd in raw_cmds if len(cmd.split()) > 1]

    # Remove duplicates and partials
    unique = []
    for cmd in raw_cmds:
        norm = cmd.lower()
        if norm not in [c.lower() for c in unique]:
            if not re.match(r"^(be|have|can|are|is|’s|left|what)\b", norm):
                unique.append(cmd)

    # Keep only syntactically valid clauses
    cleaned = []
    for cmd in unique:
        doc = nlp(cmd)
        if any(tok.pos_ == "VERB" for tok in doc) and len(doc) > 2:
            cleaned.append(cmd)

    return cleaned



# ---------------------------------------------------------------------
# TEST CASES
# ---------------------------------------------------------------------
if __name__ == "__main__":
    sentences = [
        "Add two medium red Nike t-shirts and one large blue Adidas hoodie to my cart, remove the small black jeans, and show me what’s left in my cart.",
        "Can you check if the white Puma sneakers in size 42 are available and if yes, add one pair to my cart, then show me my cart.",
        "Remove the blue denim jacket and add a black leather one in large, also check if there’s a brown version in medium.",
        "Add three pairs of white ankle socks and two black ones, remove the red scarf, and proceed to checkout.",
        "Check if Zara’s navy blue formal shirts in medium are available, add two to my cart if they are, and remove any small white shirts.",
        "Show me what’s in my cart, remove all the grey joggers, and add a pair of olive green cargo pants in large.",
        "Please check if you have the red Converse high-top sneakers in size 40, add them to my cart, and remove the white low-top ones.",
        "Remove all my small-sized items, add one medium green dress and two large red blazers, and show me the total before checkout.",
        "Can you check if there’s a black Gucci belt in stock, and if so, add one to my cart and go ahead with checkout.",
        "Add two pairs of Levi’s blue jeans in 32 waist and one pair in 34 waist, check if black ones are available, remove the old grey chinos, and show me my cart before checkout.",
        "Do you sell school bag?",
    ]

    for sentence in sentences:
        print(f"\n🧠 Sentence: {sentence}")
        cmds = split_semantic_commands(sentence)
        print("→ Split commands:\n")
        for c in cmds:
            print(f"  • {c}")
