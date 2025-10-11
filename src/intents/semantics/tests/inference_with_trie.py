from transformers import pipeline
import numpy as np
from ner_training_data import modify_cart_examples, check_examples, multi_intent_examples
from entity_validator import validate_structured_items, build_entity_trie
from intent_mapper import IntentMapper


# --------------------------
# 1. Helpers
# --------------------------
def merge_wordpieces(tokens, labels, scores):
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


def group_entities(tokens, labels, scores, intent_mapper=None):
    # First, merge adjacent brand entities that might be part of a longer entity
    tokens, labels, scores = merge_adjacent_brands(tokens, labels, scores)
    
    results = []
    buffer = {"action": [], "brand": [], "product": [],
              "tokens": [], "quantity": None, "unit": None}
    pending = {"quantity": None, "unit": None, "tokens": []}

    def flush():
        nonlocal buffer, results, pending
        if buffer["product"] or buffer["action"]:
            action_text = " ".join(buffer["action"]) if buffer["action"] else None
            brand_text = " ".join(buffer["brand"]) if buffer["brand"] else None
            product_text = " ".join(buffer["product"]) if buffer["product"] else None
            
            intent, confidence = (None, None)
            if intent_mapper and action_text:
                intent, confidence = intent_mapper.map_action_to_intent(action_text)

            results.append({
                "intent": intent,
                "action": action_text,
                "brand": brand_text,
                "product": product_text,
                "tokens": buffer["tokens"],
                "quantity": buffer["quantity"],
                "unit": buffer["unit"],
                "confidence": confidence,
            })

        buffer = {"action": [], "brand": [], "product": [],
                  "tokens": [], "quantity": None, "unit": None}
        pending = {"quantity": None, "unit": None, "tokens": []}

    for i, (tok, lab, score) in enumerate(zip(tokens, labels, scores)):
        tag = lab.replace("B-", "").replace("I-", "")
        is_beginning = lab.startswith("B-")

        if tag == "ACTION":
            buffer["action"].append(tok)
        elif tag == "BRAND":
            if is_beginning:
                # Start a new brand entity
                if buffer["brand"]:
                    # If there's already a brand, append with comma
                    buffer["brand"].append(",")
                buffer["brand"].append(tok)
            else:
                # Continue the current brand entity
                buffer["brand"].append(tok)
        elif tag == "QUANTITY":
            if buffer["product"] and buffer["quantity"] is not None:
                flush()
            if buffer["product"]:
                buffer["quantity"] = tok
            else:
                pending["quantity"] = tok
        elif tag == "UNIT":
            if buffer["product"]:
                buffer["unit"] = tok
            else:
                pending["unit"] = tok
        elif tag == "TOKEN":
            if buffer["product"]:
                buffer["tokens"].append(tok)
            else:
                pending["tokens"].append(tok)
        elif tag == "PRODUCT":
            if is_beginning:
                # Start a new product entity
                if buffer["product"]:
                    flush()
                buffer["product"] = [tok]
            else:
                # Continue the current product entity
                buffer["product"].append(tok)
            
            if pending["quantity"]:
                buffer["quantity"] = pending["quantity"]
            if pending["unit"]:
                buffer["unit"] = pending["unit"]
            if pending["tokens"]:
                buffer["tokens"].extend(pending["tokens"])
            pending = {"quantity": None, "unit": None, "tokens": []}

    if buffer["product"] or buffer["action"]:
        flush()

    return results


def merge_adjacent_brands(tokens, labels, scores):
    """
    Merge adjacent brand entities that might be part of a longer entity.
    E.g., ['coca', 'cola', 'lala'] with ['B-BRAND', 'I-BRAND', 'B-BRAND'] 
    becomes ['coca cola lala'] with ['B-BRAND']
    """
    if not tokens:
        return tokens, labels, scores
    
    merged_tokens = []
    merged_labels = []
    merged_scores = []
    
    i = 0
    while i < len(tokens):
        if labels[i] == "B-BRAND":
            # Start of a brand sequence
            brand_tokens = [tokens[i]]
            brand_scores = [float(scores[i])]
            j = i + 1
            
            # Look for adjacent brand tokens (B-BRAND or I-BRAND)
            while j < len(tokens) and labels[j] in ["B-BRAND", "I-BRAND"]:
                brand_tokens.append(tokens[j])
                brand_scores.append(float(scores[j]))
                j += 1
            
            # Merge all brand tokens into one
            merged_tokens.append(" ".join(brand_tokens))
            merged_labels.append("B-BRAND")
            merged_scores.append(sum(brand_scores) / len(brand_scores))  # Average score
            
            i = j
        else:
            # Not a brand token, keep as is
            merged_tokens.append(tokens[i])
            merged_labels.append(labels[i])
            merged_scores.append(scores[i])
            i += 1
    
    return merged_tokens, merged_labels, merged_scores


def process_text(user_text: str, trie, global_entities, intent_mapper=None):
    raw_outputs = ner_pipeline(user_text)

    tokens = [r["word"] for r in raw_outputs]
    labels = [r["entity"] for r in raw_outputs]
    scores = [r["score"] for r in raw_outputs]

    merged_tokens, merged_labels, merged_scores = merge_wordpieces(tokens, labels, scores)
    structured = group_entities(merged_tokens, merged_labels, merged_scores, intent_mapper=intent_mapper)
    
    print(f"\n[DEBUG] Grouped entities before validation:")
    for i, item in enumerate(structured):
        print(f"  {i}: {item}")
    
    validated = validate_structured_items(structured, user_text, trie, global_entities)

    return {
        "tokens": merged_tokens,
        "labels": merged_labels,
        "scores": merged_scores,
        "structured": validated,
    }


# --------------------------
# 2. Model + Pipeline setup
# --------------------------
ner_pipeline = pipeline(
    "token-classification",
    model="./minilm-ner-best",
    tokenizer="./minilm-ner-best",
    aggregation_strategy="none"
)

training_examples = {**modify_cart_examples, **check_examples, **multi_intent_examples}



# test_runner.py
from ner_inference import process_text
from entity_validator import validate_structured_items, build_entity_trie
from intent_mapper import IntentMapper
from entity_loader import load_global_entities

from hf_unit_test_examples import examples  # <-- your JSON list




from entity_loader import load_global_entities
from entity_validator import build_entity_trie
from ner_inference import process_text
from intent_mapper import IntentMapper


def normalize_unit(u):
    """Normalize unit: plural → singular (bags → bag)."""
    if not u:
        return u
    return u.rstrip("s")


def compare_dicts(expected, actual, ignore_keys=None, normalize=None):
    """
    Flexible dict comparison.
    - Ignores keys in `ignore_keys`
    - Normalizes values using `normalize` mapping (key -> fn)
    Returns dict of differences.
    """
    ignore_keys = ignore_keys or {"confidence"}
    normalize = normalize or {}
    differences = {}

    for k, v in expected.items():
        if k in ignore_keys:
            continue

        actual_val = actual.get(k)

        # Apply normalization if configured
        if k in normalize:
            v = normalize[k](v)
            actual_val = normalize[k](actual_val)

        if v != actual_val:
            differences[k] = {"expected": v, "actual": actual_val}

    return differences


def run_test_examples(entities, trie, mapper):
    from hf_unit_test_examples import examples

    print("\n=== Running Test Examples ===\n")

    passed = 0
    total = len(examples)

    for i, example in enumerate(examples, 1):
        text = example["sentence"]
        expected = example["response"]

        print(f"Test {i}: {text}")

        result = process_text(text, trie, entities, intent_mapper=mapper)

        # Flatten actual result (take first structured item)
        actual_item = result["structured"][0] if result["structured"] else {}

        actual = {
            "intent": actual_item.get("intent"),
            "action": actual_item.get("action"),
            "brand": actual_item.get("brand"),
            "product": actual_item.get("product"),
            "tokens": actual_item.get("tokens", []),
            "quantity": actual_item.get("quantity"),
            "unit": actual_item.get("unit"),
            "confidence": actual_item.get("confidence"),
            "product_validated": actual_item.get("product_validated"),
            "product_validation_method": actual_item.get("product_validation_method"),
            "brand_validated": actual_item.get("brand_validated"),
            "brand_validation_method": actual_item.get("brand_validation_method"),
        }

        # Flexible comparison
        diffs = compare_dicts(
            expected, actual,
            ignore_keys={"confidence"},
            normalize={"unit": normalize_unit}
        )
        test_passed = not diffs

        if test_passed:
            passed += 1
            status = "✅ PASS"
        else:
            status = "❌ FAIL"

        print(f"Status: {status}")
        print(f"Expected: {expected}")
        print(f"Actual:   {actual}")

        if diffs:
            print("Differences:")
            for k, d in diffs.items():
                print(f"  {k}: expected {d['expected']}, got {d['actual']}")

        print("-" * 50)

    print(f"\nResults: {passed}/{total} tests passed")


def run_interactive_mode(entities, trie, mapper):
    print("\n=== Interactive Mode (type 'quit' to exit) ===\n")

    while True:
        text = input("Enter a sentence: ").strip()
        if not text or text.lower() in {"q", "quit", "exit"}:
            break

        result = process_text(text, trie, entities, intent_mapper=mapper)

        print(f"\nInput: {text}")
        print(f"Tokens: {result['tokens']}")
        print(f"Labels: {result['labels']}")
        print(f"Scores: {[f'{s:.2f}' for s in result['scores']]}")

        print("\nNER Extracted Entities:")
        for tok, lab, score in zip(result["tokens"], result["labels"], result["scores"]):
            if lab != "O":
                print(f"  {tok} → {lab.replace('B-', '').replace('I-', '')} (conf {score:.2f})")
            else:
                print(f"  {tok} → O")

        print("\nGrouped items (canonicalized):")
        for item in result["structured"]:
            print(item)
        print()


def main():
    entities = load_global_entities()
    trie = build_entity_trie(entities)
    mapper = IntentMapper()

    print("Choose mode:")
    print("1. Run test examples")
    print("2. Interactive mode")
    
    while True:
        choice = input("\nEnter choice (1 or 2): ").strip()
        if choice == "1":
            run_test_examples(entities, trie, mapper)
            break
        elif choice == "2":
            run_interactive_mode(entities, trie, mapper)
            break
        else:
            print("Please enter 1 or 2")


if __name__ == "__main__":
    main()
