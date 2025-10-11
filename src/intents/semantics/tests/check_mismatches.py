#!/usr/bin/env python3
from ner_training_data import modify_cart_examples, check_examples, multi_intent_examples

training_examples = {**modify_cart_examples, **check_examples, **multi_intent_examples}

mismatches = []
for text, labels in training_examples.items():
    tokens = text.split()
    if len(tokens) != len(labels):
        mismatches.append((text, len(tokens), len(labels), tokens, labels))

print(f'Found {len(mismatches)} mismatches')

for text, token_count, label_count, tokens, labels in mismatches[:10]:
    print(f'Text: {text}')
    print(f'Tokens ({token_count}): {tokens}')
    print(f'Labels ({label_count}): {labels}')
    print(f'Difference: {token_count - label_count}')
    print('---')

