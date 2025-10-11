from datasets import Dataset
from transformers import (
    AutoTokenizer, AutoModelForTokenClassification,
    TrainingArguments, Trainer, TrainerCallback, EarlyStoppingCallback
)
import evaluate
import numpy as np
from tabulate import tabulate
import time
import os
from luma.classification.training_data import training_examples

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Store is one level up from models/ directory
STORE_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "store")


# -----------------------
# 1. Tokenizer + labels
# -----------------------
# model_name = "microsoft/MiniLM-L12-H384-uncased"

model_name = "bert-base-uncased"
tokenizer = AutoTokenizer.from_pretrained(model_name)

# 🔹 Add custom placeholder tokens to tokenizer
special_tokens = ["brandtoken", "producttoken", "varianttoken", "unittoken", "quantitytoken"]
num_added = tokenizer.add_tokens(special_tokens)
print(f"Added {num_added} new tokens to tokenizer: {special_tokens}")

all_labels = [lab for labs in training_examples.values() for lab in labs]

# Collect base entity types (ACTION, UNIT, PRODUCT, etc.)
entity_types = sorted({lab.replace("B-", "").replace("I-", "") 
                       for lab in all_labels if lab != "O"})

# Always include both B- and I- variants
unique_tags = ["O"]
for ent in entity_types:
    unique_tags.append(f"B-{ent}")
    unique_tags.append(f"I-{ent}")

label2id = {l: i for i, l in enumerate(unique_tags)}
id2label = {i: l for l, i in label2id.items()}


def tokenize_and_align(text, labels, max_length=64):
    """
    Tokenize input text and align labels with subword pieces.
    """
    words = text.split()
    
    if len(words) != len(labels):
        raise ValueError(
            f"Mismatch between words and labels!\n"
            f"Text: '{text}'\n"
            f"Words ({len(words)}): {words}\n"
            f"Labels ({len(labels)}): {labels}"
        )
    
    tokenized = tokenizer(
        words,
        is_split_into_words=True,
        truncation=True,
        padding="max_length",
        max_length=max_length
    )

    word_ids = tokenized.word_ids()
    aligned_labels = []
    prev_word = None
    for word_id in word_ids:
        if word_id is None:
            aligned_labels.append(-100)
        elif word_id != prev_word:
            if word_id >= len(labels):
                raise IndexError(
                    f"word_id {word_id} out of range for labels of length {len(labels)}\n"
                    f"Text: '{text}'\n"
                    f"Words: {words}\n"
                    f"Labels: {labels}"
                )
            aligned_labels.append(label2id[labels[word_id]])
        else:
            curr_label = labels[word_id]
            if curr_label.startswith("B-"):
                curr_label = "I-" + curr_label[2:]
            aligned_labels.append(label2id[curr_label])
        prev_word = word_id

    while len(aligned_labels) < max_length:
        aligned_labels.append(-100)
    aligned_labels = aligned_labels[:max_length]

    tokenized["labels"] = aligned_labels
    return tokenized


# -----------------------
# 2. Build dataset
# -----------------------
tokenized_examples = [
    tokenize_and_align(text, labels)
    for text, labels in training_examples.items()
]

dataset = Dataset.from_list(tokenized_examples).train_test_split(test_size=0.2)


# -----------------------
# 3. Model + metrics
# -----------------------
model = AutoModelForTokenClassification.from_pretrained(
    model_name,
    num_labels=len(label2id),
    id2label=id2label,
    label2id=label2id
)

# 🔹 Resize embeddings because we added tokens
model.resize_token_embeddings(len(tokenizer))

seqeval = evaluate.load("seqeval")


def compute_metrics(eval_pred):
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=-1)

    true_preds = [
        [id2label[p] for (p, l) in zip(pred, lab) if l != -100]
        for pred, lab in zip(preds, labels)
    ]
    true_labels = [
        [id2label[l] for (p, l) in zip(pred, lab) if l != -100]
        for pred, lab in zip(preds, labels)
    ]

    report = seqeval.compute(predictions=true_preds, references=true_labels)

    table = []
    for tag, metrics in report.items():
        if isinstance(metrics, dict) and "f1" in metrics:
            table.append([tag, metrics["precision"], metrics["recall"], metrics["f1"], metrics["number"]])
    if table:
        print(tabulate(table, headers=["Label", "Precision", "Recall", "F1", "Count"], floatfmt=".2f"))

    return {
        "overall_f1": report["overall_f1"],
        "overall_precision": report["overall_precision"],
        "overall_recall": report["overall_recall"],
        "overall_accuracy": report["overall_accuracy"],
    }


# -----------------------
# 4. Training setup
# -----------------------
class StopWhenPerfectCallback(TrainerCallback):
    def on_evaluate(self, training_args, state, control, metrics=None, **kwargs):
        if metrics is None:
            return
        overall_precision = metrics.get("eval_overall_precision", 0.0)
        overall_recall = metrics.get("eval_overall_recall", 0.0)
        overall_f1 = metrics.get("eval_overall_f1", 0.0)
        if overall_precision == 1.0 and overall_recall == 1.0 and overall_f1 == 1.0:
            print("🎯 All metrics are perfect → stopping training immediately!")
            control.should_training_stop = True
        return control


# Create store directory if it doesn't exist
os.makedirs(STORE_DIR, exist_ok=True)

training_args = TrainingArguments(
    output_dir=os.path.join(STORE_DIR, "bert-ner-checkpoints"),
    evaluation_strategy="epoch",
    save_strategy="epoch",
    learning_rate=5e-5,
    per_device_train_batch_size=8,
    per_device_eval_batch_size=8,
    num_train_epochs=15,
    weight_decay=0.01,
    load_best_model_at_end=True,
    metric_for_best_model="overall_f1",
    greater_is_better=True,
    logging_dir=os.path.join(STORE_DIR, "logs"),
)


trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=dataset["train"],
    eval_dataset=dataset["test"],
    compute_metrics=compute_metrics,
    tokenizer=tokenizer,
    callbacks=[
        EarlyStoppingCallback(early_stopping_patience=3),
        StopWhenPerfectCallback()
    ]
)


# -----------------------
# 5. Train + Save
# -----------------------
print("🚀 Starting training...")
start_time = time.time()

trainer.train()

end_time = time.time()
training_duration = end_time - start_time
hours = int(training_duration // 3600)
minutes = int((training_duration % 3600) // 60)
seconds = int(training_duration % 60)

print(f"⏱️  Training completed in {hours:02d}:{minutes:02d}:{seconds:02d} ({training_duration:.2f} seconds)")

# Save the best model to store directory
model_path = os.path.join(STORE_DIR, "bert-ner-best")
print(f"💾 Saving model to: {model_path}")
trainer.save_model(model_path)
tokenizer.save_pretrained(model_path)
print(f"✅ Model saved successfully!")
