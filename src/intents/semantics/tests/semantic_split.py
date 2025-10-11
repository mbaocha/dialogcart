#!/usr/bin/env python3
import re
import numpy as np
from sentence_transformers import SentenceTransformer, util

# ---------------------------------------------------------------------
# 1. Load a compact embedding model
# ---------------------------------------------------------------------
print("🚀 Loading MiniLM embeddings...")
embedder = SentenceTransformer("all-MiniLM-L6-v2")
print("✅ Model loaded.\n")

# ---------------------------------------------------------------------
# 2. Define known intent prototypes
# ---------------------------------------------------------------------
INTENT_PROTOTYPES = {
    "add": [
        "add to cart", "throw in", "put in", "insert", "include", "buy", "get", "place in basket"
    ],
    "remove": [
        "remove from cart", "delete", "expunge", "take out", "drop", "cancel", "clear out"
    ],
    "check": [
        "check availability", "verify", "see if available", "find out if you have"
    ],
    "show": [
        "show cart", "display cart", "view items", "see basket", "what’s left"
    ],
    "checkout": [
        "checkout", "proceed to checkout", "complete purchase", "buy now", "go ahead with checkout"
    ],
}

# Precompute intent prototype embeddings
intent_centroids = {}
for intent, phrases in INTENT_PROTOTYPES.items():
    intent_centroids[intent] = embedder.encode(phrases, convert_to_tensor=True).mean(dim=0)


# ---------------------------------------------------------------------
# 3. Utility functions
# ---------------------------------------------------------------------
def cosine(a, b):
    return util.cos_sim(a, b).item()


def sliding_windows(tokens, size=5):
    """Generate all n-grams up to size."""
    spans = []
    for n in range(1, size + 1):
        for i in range(len(tokens) - n + 1):
            span = " ".join(tokens[i:i+n])
            spans.append((i, i+n, span))
    return spans


def mask_text(tokens, start, end):
    """Mask used span so it’s not re-embedded."""
    return tokens[:start] + ["[MASKED]"] * (end - start) + tokens[end:]


# ---------------------------------------------------------------------
# 4. Intent peeling algorithm
# ---------------------------------------------------------------------
def extract_intents(sentence, threshold=0.42):
    tokens = sentence.split()
    intents = []
    text_remaining = tokens[:]

    while True:
        spans = sliding_windows(text_remaining, size=5)
        if not spans:
            break

        span_texts = [s[2] for s in spans]
        span_embeds = embedder.encode(span_texts, convert_to_tensor=True)

        best_match = None
        best_score = 0.0
        best_intent = None
        best_span = None

        for intent, centroid in intent_centroids.items():
            sims = util.cos_sim(span_embeds, centroid)
            top_idx = int(np.argmax(sims))
            score = float(sims[top_idx])

            if score > best_score:
                best_score = score
                best_span = spans[top_idx]
                best_intent = intent

        if best_score < threshold:
            break

        start, end, span_text = best_span
        intents.append((best_intent, span_text.strip(), round(best_score, 2)))
        text_remaining = mask_text(text_remaining, start, end)

    return intents


# ---------------------------------------------------------------------
# 5. Demo
# ---------------------------------------------------------------------
test_sentences = [
    "throw in rice to cart and expunge beans",
    "Add two medium red Nike t-shirts and one large blue Adidas hoodie to my cart, remove the small black jeans, and show me what’s left in my cart.",
    "Please proceed to checkout after adding milk and bread.",
    "check if yams are available and then throw in rice",
]

for s in test_sentences:
    print(f"\n🧠 Sentence: {s}")
    results = extract_intents(s)
    print("→ Split by intent:\n")
    for intent, span, score in results:
        print(f"  • ({intent}, {score}) {span}")
