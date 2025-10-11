import re
import random
import argparse
import tempfile
import subprocess
from pathlib import Path
from typing import List, Tuple

import spacy
from spacy.training.example import Example
from spacy.util import minibatch
from spacy.tokens import DocBin


# ----------------------------
# Rasa → SpaCy conversion utils
# ----------------------------

def parse_rasa_example(rasa_example: str) -> Tuple[str, dict]:
    entity_pattern = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<label>[^)]+)\)")
    entities = []
    clean_text = ""
    last_idx = 0

    offset_adjustment = 2 if rasa_example.strip().startswith("- ") else 0

    for match in entity_pattern.finditer(rasa_example):
        start, end = match.span()
        entity_text = match.group("text")
        entity_label = match.group("label")

        clean_text += rasa_example[last_idx:start]
        ent_start = len(clean_text)
        clean_text += entity_text
        ent_end = len(clean_text)
        entities.append((ent_start, ent_end, entity_label.upper()))
        last_idx = end

    clean_text += rasa_example[last_idx:]
    clean_text = clean_text.strip()

    if clean_text.startswith("- "):
        clean_text = clean_text[2:]

    if offset_adjustment > 0:
        entities = [(s - offset_adjustment, e - offset_adjustment, l) for s, e, l in entities]

    return clean_text, {"entities": entities}


def load_rasa_examples_from_yaml(yaml_path: Path, intents: List[str] | None = None) -> List[str]:
    import yaml
    yaml_data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not yaml_data or "nlu" not in yaml_data:
        return []

    examples: List[str] = []
    for nlu_item in yaml_data["nlu"]:
        if not isinstance(nlu_item, dict):
            continue
        intent_name = nlu_item.get("intent")
        if intent_name is None:
            continue
        if intents is not None and intent_name not in intents:
            continue
        block = nlu_item.get("examples")
        if not isinstance(block, str):
            continue
        for line in block.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("- "):
                examples.append(stripped)
    return examples


def convert_yaml_to_spacy_training_data(yaml_path: Path, intents: List[str] | None = None) -> List[Tuple[str, dict]]:
    rasa_examples = load_rasa_examples_from_yaml(yaml_path, intents=intents)
    spacy_train_data: List[Tuple[str, dict]] = []
    nlp = spacy.blank("en")

    for rasa_line in rasa_examples:
        if "[" not in rasa_line or "]" not in rasa_line or "(" not in rasa_line or ")" not in rasa_line:
            continue

        parsed_text, annotations = parse_rasa_example(rasa_line)
        if not annotations.get("entities"):
            continue

        doc = nlp.make_doc(parsed_text)
        valid_entities = []
        for start, end, label in annotations["entities"]:
            span = doc.char_span(start, end, label=label)
            if span is None:
                print(f"❌ Misaligned entity: '{parsed_text[start:end]}' in: {parsed_text}")
            else:
                valid_entities.append((start, end, label))

        if valid_entities:
            spacy_train_data.append((parsed_text, {"entities": valid_entities}))

    return spacy_train_data


# ----------------------------
# Training + Evaluation
# ----------------------------

def save_docbin(data, path, nlp=None):
    """Save (text, entities) tuples as a DocBin for spaCy train CLI."""
    nlp = nlp or spacy.blank("en")
    db = DocBin()
    for text, ann in data:
        doc = nlp.make_doc(text)
        example = Example.from_dict(doc, ann)
        db.add(example.reference)
    db.to_disk(path)


def train_spacy_ner(train_data, model_name="blank", n_iter=30, split=0.8, output_dir="spacy_model"):
    random.shuffle(train_data)
    split_idx = int(len(train_data) * split)
    train_examples = train_data[:split_idx]
    dev_examples = train_data[split_idx:]

    # 🔑 Transformer case → use config + spaCy train
    if model_name == "trf":
        print("🚀 Training with transformer pipeline via config.cfg")

        tmpdir = Path(tempfile.mkdtemp())
        train_file = tmpdir / "train.spacy"
        dev_file = tmpdir / "dev.spacy"

        save_docbin(train_examples, train_file)
        save_docbin(dev_examples, dev_file)

        config_path = Path(__file__).resolve().parent / "config_trf.cfg"

        subprocess.run([
            "python", "-m", "spacy", "train", str(config_path),
            "--paths.train", str(train_file),
            "--paths.dev", str(dev_file),
            "--output", output_dir
        ], check=True)

        return spacy.load(output_dir)

    # ✅ Fallback: medium or blank model (classic training loop)
    if model_name == "md":
        print("⚡ Using medium model (en_core_web_md)")
        nlp = spacy.load("en_core_web_md")
    else:
        print("📦 Using blank English pipeline")
        nlp = spacy.blank("en")

    if "ner" not in nlp.pipe_names:
        ner = nlp.add_pipe("ner")
    else:
        ner = nlp.get_pipe("ner")

    for _, ann in train_examples:
        for ent in ann.get("entities", []):
            ner.add_label(ent[2])

    optimizer = nlp.begin_training()

    for itn in range(n_iter):
        losses = {}
        batches = minibatch(train_examples, size=8)
        for batch in batches:
            examples = [Example.from_dict(nlp.make_doc(text), annots) for text, annots in batch]
            nlp.update(examples, drop=0.35, losses=losses, sgd=optimizer)
        print(f"Iteration {itn+1}: Losses {losses}")

    Path(output_dir).mkdir(exist_ok=True)
    nlp.to_disk(output_dir)
    print(f"✅ Model saved to {output_dir}")

    return nlp


# ----------------------------
# Main entrypoint
# ----------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["trf", "md", "blank"], default="blank", help="Choose spaCy model type")
    args = parser.parse_args()

    trainings_yaml = Path(__file__).resolve().parents[1] / "trainings" / "initial_training_data.yml"
    spacy_data = convert_yaml_to_spacy_training_data(trainings_yaml)
    print(f"Loaded {len(spacy_data)} valid training examples.")
    train_spacy_ner(spacy_data, model_name=args.model, n_iter=30, split=0.8, output_dir="spacy_model")
