#!/usr/bin/env python3
"""
Quick test to see what the NER model is actually predicting.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from luma.classification import NERModel

# Initialize NER model
print("Loading NER model...")
model = NERModel()

# Test sentence
test_sentence = "add item 1"
print(f"\nInput: '{test_sentence}'")
print("=" * 60)

# Predict
result = model.predict(test_sentence)

print("\nNER Predictions:")
print("-" * 60)
for i, (token, label, score) in enumerate(zip(result.tokens, result.labels, result.scores)):
    print(f"{i+1}. '{token:15}' → {label:15} (confidence: {score:.3f})")

print("\n" + "=" * 60)

# Check for ordinals
ordinals = [tok for tok, lab in zip(result.tokens, result.labels) if "ORDINAL" in lab]
if ordinals:
    print(f"✅ Ordinals detected: {ordinals}")
else:
    print("❌ No ordinals detected")
    print("\n💡 This means the model needs retraining with ordinal examples.")
    print("   Run: python luma/classification/training.py")

