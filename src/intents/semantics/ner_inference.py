import re
import os

import numpy as np
from transformers import pipeline


# ===== LOGGING CONFIGURATION =====
# Set DEBUG_NLP=1 in environment to enable debug logs
DEBUG_ENABLED = os.environ.get("DEBUG_NLP", "0") == "1"

def debug_print(*args, **kwargs):
    """Print debug message only if DEBUG_ENABLED is True"""
    if DEBUG_ENABLED:
        print(*args, **kwargs)


# --------------------------
# 1. Helpers
# --------------------------

def merge_wordpieces(tokens, labels, scores):
    """
    Merge subword tokens (e.g. 'co', '##ca', '##-cola') into full words.
    Non-breaking — just cleans up the token sequence.
    """
    merged_tokens, merged_labels, merged_scores = [], [], []
    current_token, current_label, current_scores = "", None, []

    for tok, lab, score in zip(tokens, labels, scores):
        if tok.startswith("##"):
            current_token += tok[2:]
            current_scores.append(score)
        else:
            if current_token:
                merged_tokens.append(current_token)
                merged_labels.append(current_label)
                merged_scores.append(float(np.mean(current_scores)))
            current_token = tok
            current_label = lab
            current_scores = [score]

    if current_token:
        merged_tokens.append(current_token)
        merged_labels.append(current_label)
        merged_scores.append(float(np.mean(current_scores)))

    return merged_tokens, merged_labels, merged_scores


def merge_adjacent_brands(tokens, labels, scores):
    """
    Merge adjacent brand entities that might be part of a longer brand name.
    E.g. ['coca', 'cola'] with ['B-BRAND', 'I-BRAND'] → ['coca cola'].
    Safe and non-breaking.
    """
    if not tokens:
        return tokens, labels, scores

    merged_tokens = []
    merged_labels = []
    merged_scores = []

    i = 0
    while i < len(tokens):
        if labels[i] == "B-BRAND":
            brand_tokens = [tokens[i]]
            brand_scores = [float(scores[i])]
            j = i + 1

            while j < len(tokens) and labels[j] in ["I-BRAND", "B-BRAND"]:
                brand_tokens.append(tokens[j])
                brand_scores.append(float(scores[j]))
                j += 1

            merged_tokens.append(" ".join(brand_tokens))
            merged_labels.append("B-BRAND")
            merged_scores.append(sum(brand_scores) / len(brand_scores))
            i = j
        else:
            merged_tokens.append(tokens[i])
            merged_labels.append(labels[i])
            merged_scores.append(scores[i])
            i += 1

    return merged_tokens, merged_labels, merged_scores

def fix_bio_sequence(tokens, labels):
    """
    Fix invalid BIO transitions (B-PRODUCT B-PRODUCT) only when
    neither token is a placeholder (e.g. 'producttoken', 'brandtoken', etc.).
    """
    fixed = labels.copy()
    for i in range(1, len(tokens)):
        prev_label, curr_label = fixed[i-1], fixed[i]
        prev_tok, curr_tok = tokens[i-1].lower(), tokens[i].lower()

        # Only fix consecutive B-PRODUCT → B-PRODUCT
        if prev_label == "B-PRODUCT" and curr_label == "B-PRODUCT":
            # Skip merges involving placeholders
            if "token" in prev_tok or "token" in curr_tok:
                continue

            # Merge only when both are natural tokens
            fixed[i] = "I-PRODUCT"

    return fixed


# --------------------------
# 2. Main Inference Function
# --------------------------

def process_text(user_text: str):

    # Get tokenizer to split text properly
    tokenizer = ner_pipeline.tokenizer
    
    # Tokenize and get predictions for ALL tokens
    inputs = tokenizer(user_text, return_tensors="pt", truncation=False)
    
    # Run model
    import torch
    model = ner_pipeline.model
    with torch.no_grad():
        outputs = model(**inputs)
    
    predictions = torch.argmax(outputs.logits, dim=-1)[0]
    scores = torch.softmax(outputs.logits, dim=-1)[0]
    
    # Convert token IDs back to words and labels
    token_ids = inputs["input_ids"][0]
    tokens, labels, token_scores = [], [], []
    
    SEP_TOKENS = {
        "and", "or", "plus", "then", "next", "after", "after that",
        "followed", "by", "as well as", "also", "in addition", ","
    }
    
    # Skip [CLS] and [SEP] tokens
    for i in range(1, len(token_ids) - 1):
        word = tokenizer.decode([token_ids[i]]).strip()
        if not word:  # Skip empty tokens
            continue
            
        label_id = predictions[i].item()
        label = model.config.id2label[label_id]
        score = scores[i][label_id].item()
        
        tokens.append(word)
        labels.append(label)
        token_scores.append(score)
        
        debug_print(f"[DEBUG] Word: {word}")
        debug_print(f"[DEBUG] Label: {label}")

    # Step 2: Merge HuggingFace artifacts
    merged_tokens, merged_labels, merged_scores = merge_wordpieces(
        tokens, labels, token_scores
    )
    merged_tokens, merged_labels, merged_scores = merge_adjacent_brands(
        merged_tokens, merged_labels, merged_scores
    )

    # Step 3: Normalize merged placeholders like "unittoken brandtoken"
    def normalize_placeholders(tokens, labels, scores):
        """
        Splits merged placeholders safely.
        Handles:
        - multiple placeholders merged (e.g. 'unittoken brandtoken')
        - placeholder + word merges (e.g. 'brandtoken nice')
        """
        fixed_toks, fixed_labs, fixed_scores = [], [], []
        placeholder_pattern = re.compile(
            r"\b(producttoken|brandtoken|unittoken|varianttoken)\b"
        )

        for tok, lab, sc in zip(tokens, labels, scores):
            parts = tok.split()
            # Case 1: multiple space-separated parts
            if len(parts) > 1:
                for part in parts:
                    fixed_toks.append(part)
                    fixed_labs.append(lab)
                    fixed_scores.append(sc)
                continue

            # Case 2: placeholder merged with other chars
            # (e.g. 'brandtoken-nice')
            # Split cleanly if any placeholder substring is detected
            match = placeholder_pattern.search(tok.lower())
            if match and tok.lower() != match.group(1):
                # Extract before + after segments
                base = match.group(1)
                before = tok[:match.start()].strip()
                after = tok[match.end():].strip()

                if before:
                    fixed_toks.append(before)
                    fixed_labs.append("O")
                    fixed_scores.append(sc)

                fixed_toks.append(base)
                fixed_labs.append(
                    f"B-{base.replace('token','').upper()}"
                )
                fixed_scores.append(sc)

                if after:
                    fixed_toks.append(after)
                    fixed_labs.append("O")
                    fixed_scores.append(sc)
            else:
                fixed_toks.append(tok)
                fixed_labs.append(lab)
                fixed_scores.append(sc)

        return fixed_toks, fixed_labs, fixed_scores

    merged_tokens, merged_labels, merged_scores = normalize_placeholders(
        merged_tokens, merged_labels, merged_scores
    )

    # Step 4: 🩹 Enforce correct labels for placeholders
    enforced_labels = {
        "producttoken": "B-PRODUCT",
        "brandtoken": "B-BRAND",
        "unittoken": "B-UNIT",
        "varianttoken": "B-TOKEN",
    }

    for i, tok in enumerate(merged_tokens):
        low_tok = tok.lower()
        if low_tok in enforced_labels:
            merged_labels[i] = enforced_labels[low_tok]
    # Step 5: 🩹 Fix invalid BIO transitions
    # (only when not placeholders)
    merged_labels = fix_bio_sequence(merged_tokens, merged_labels)

    # Step 6: Debug output
    debug_print("\n[DEBUG] HR Inference Output (flat labels only):")
    for t, l, s in zip(merged_tokens, merged_labels, merged_scores):
        debug_print(f"  {t:<15} -> {l:<12} ({s:.3f})")

    return {
        "tokens": merged_tokens,
        "labels": merged_labels,
        "scores": merged_scores,
        "structured": None,
    }



# --------------------------
# 3. Pipeline Setup
# --------------------------

ner_pipeline = pipeline(
    "token-classification",
    model="./bert-ner-best",
    tokenizer="./bert-ner-best",
    aggregation_strategy="none"
)


# --------------------------
# 3. Test Function
# --------------------------


def main():
    test_sentences = [
        "Please remove 2 small bags of producttoken from my basket, "
        "add 5 bottles of brandtoken producttoken, and switch the "
        "brandtoken noodles from chicken flavor to producttoken flavor.",
        "Cancel my previous order of 3 cartons of brandtoken producttoken, "
        "insert 6 packs of producttoken instead, and update the quantity "
        "of brandtoken juice to 4 bottles.",
        "Add 10 kg of producttoken, remove 2 kg of beans, and change my "
        "brandtoken producttoken from vanilla flavor to producttoken "
        "chocolate variant.",
        "Replace producttoken with 5 dozen of brandtoken producttoken, "
        "include 3 bottles of producttoken in the same order, and reduce "
        "my noodles from chicken flavor to producttoken beef flavor.",
        "Put 7 pairs of brandtoken producttoken into the cart, remove "
        "2 small bags of producttoken, and switch the brandtoken "
        "detergent from lavender to producttoken lemon scent.",
        "Buy 4 packs of producttoken, cancel 1 carton of brandtoken "
        "producttoken, and change the quantity of producttoken rice to "
        "6 kg while updating brandtoken milk to 2 tins.",
        "Throw in 3 bottles of producttoken and 2 bottles of brandtoken "
        "producttoken, remove the 5 kg pack of producttoken flour, and "
        "switch brandtoken noodles from spicy to producttoken mild flavor.",
        "Order 1 brandtoken producttoken in 500 ml size, remove "
        "2 small bags of producttoken, and replace the brandtoken "
        "lotion from aloe variant to producttoken shea butter variant.",
        "Include 8 packs of producttoken and cancel 2 dozen brandtoken "
        "producttoken, then update my order so that brandtoken bread is "
        "switched from white to producttoken brown.",
        "Remove 6 tins of brandtoken producttoken, add 3 cartons of "
        "producttoken, and switch the brandtoken cereal from honey "
        "flavor to producttoken chocolate flavor."
    ]

    total, successes = 0, 0

    for i, sent in enumerate(test_sentences, 1):
        print(f"\n=== Test {i} ===")
        print(f"Input: {sent}")

        result = process_text(sent)
        tokens = result["tokens"]
        labels = result["labels"]

        # Check if producttoken is labeled as B-PRODUCT or I-PRODUCT
        found_product = any(
            token.lower() == "producttoken" and label in ["B-PRODUCT", "I-PRODUCT"]
            for token, label in zip(tokens, labels)
        )
        
        # Check if brandtoken is labeled as B-BRAND or I-BRAND
        found_brand = any(
            token.lower() == "brandtoken" and label in ["B-BRAND", "I-BRAND"]
            for token, label in zip(tokens, labels)
        )

        if found_product and found_brand:
            print(
                "✅ SUCCESS: producttoken recognized as PRODUCT and "
                "brandtoken recognized as BRAND"
            )
            successes += 1
        else:
            print("❌ FAILURE")
            if not found_product:
                print("   - producttoken NOT recognized as PRODUCT")
            if not found_brand:
                print("   - brandtoken NOT recognized as BRAND")

            print("[DEBUG token-label pairs]:")
            for token, label in zip(tokens, labels):
                if token.lower() in ["producttoken", "brandtoken"]:
                    print(f"   {token} -> {label}")

        total += 1

    print(
        f"\n[SUMMARY] {successes}/{total} sentences passed "
        f"({(successes/total)*100:.1f}% success rate)"
    )



if __name__ == "__main__":
    main()
