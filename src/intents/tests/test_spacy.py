import json
from pathlib import Path
import spacy
from sklearn.metrics import precision_recall_fscore_support

def load_gold_examples(json_path: Path):
    """Load gold examples from JSON."""
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)

def extract_gold_entities(example):
    """
    Convert flat dict {LABEL: value} into list of (label, value).
    Keeps ordering by numeric suffix if present (e.g. VERB_1, VERB_2).
    """
    entities = []
    for key, value in example["entities"].items():
        entities.append((key.split("_")[0], value.lower()))
    return entities

def extract_pred_entities(doc):
    """Convert spaCy doc into list of (label, value)."""
    return [(ent.label_, ent.text.lower()) for ent in doc.ents]

def compare_entities(gold, pred):
    """
    Return lists of y_true and y_pred (for sklearn metrics).
    Each gold entity is expected → 1 if predicted else 0.
    Each predicted entity not in gold is a false positive.
    """
    gold_set = set(gold)
    pred_set = set(pred)

    y_true = []
    y_pred = []

    # Check gold entities
    for ent in gold:
        y_true.append(1)
        y_pred.append(1 if ent in pred_set else 0)

    # Check false positives
    for ent in pred:
        if ent not in gold_set:
            y_true.append(0)
            y_pred.append(1)

    return y_true, y_pred

def evaluate(model_path: str, gold_json: Path):
    nlp = spacy.load(model_path)
    examples = load_gold_examples(gold_json)

    all_y_true, all_y_pred = [], []
    for ex in examples:
        text = ex["text"]
        gold = extract_gold_entities(ex)
        doc = nlp(text)
        pred = extract_pred_entities(doc)

        y_true, y_pred = compare_entities(gold, pred)
        all_y_true.extend(y_true)
        all_y_pred.extend(y_pred)

        if gold != pred:
            print(f"❌ Mismatch for: {text}")
            print(f"   Gold: {gold}")
            print(f"   Pred: {pred}\n")

    precision, recall, f1, _ = precision_recall_fscore_support(
        all_y_true, all_y_pred, average="binary"
    )

    print("\n📊 Evaluation Results")
    print(f"Precision: {precision:.3f}")
    print(f"Recall:    {recall:.3f}")
    print(f"F1-score:  {f1:.3f}")

if __name__ == "__main__":
    # Adjust these paths
    model_dir = "../entity_extraction/spacy_model"   # your trained model directory
    gold_file = Path("spacy_groceries_100.jsonl")  # the JSON file we'll generate
    evaluate(model_dir, gold_file)
