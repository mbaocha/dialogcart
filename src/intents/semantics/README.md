# Entity Extraction Pipeline

This module provides a comprehensive entity extraction pipeline that combines multiple processing stages to extract structured entities from natural language sentences.

## Overview

The pipeline processes sentences through the following stages:

1. **NLP Processing & Parameterization** (`nlp_processor.py`)
   - Extracts entities using spaCy and custom entity patterns
   - Creates parameterized sentences with entity tokens (e.g., "producttoken", "brandtoken")

2. **NER Inference** (`ner_inference.py`)
   - Processes parameterized sentences through a trained NER model
   - Returns token-level entity labels and confidence scores

3. **Entity Grouping** (`entity_grouping.py`)
   - Groups related entities into action-centric semantic groups
   - Handles complex multi-entity sentences and context propagation

## Usage

### Basic Usage

```python
from extract_entities import extract_entities

# Extract entities from a sentence
result = extract_entities("Add 2 bags of rice and 1 Gucci bag")

print(f"Status: {result['status']}")
print(f"Parameterized: {result['parameterized_sentence']}")

# Access extracted groups
if result['grouped_entities']['groups']:
    for group in result['grouped_entities']['groups']:
        print(f"Action: {group['action']}")
        print(f"Products: {group['products']}")
        print(f"Brands: {group['brands']}")
        print(f"Quantities: {group['quantities']}")
        print(f"Units: {group['units']}")
```

### Simple Usage

For quick entity extraction without detailed processing information:

```python
from extract_entities import extract_entities_simple

# Returns just the entity groups
groups = extract_entities_simple("Remove 3 bottles of Coca Cola")

for group in groups:
    print(f"{group['action']}: {group['quantities']} {group['units']} of {group['brands']} {group['products']}")
```

### With Confidence Scores

```python
from extract_entities import extract_entities_with_confidence

result = extract_entities_with_confidence("Add 1 kg of beans and 2 cartons of milk")

print(f"Status: {result['status']}")
print(f"Summary: {result['summary']}")
print(f"Confidence scores: {result['confidence_scores']}")
```

## API Reference

### `extract_entities(sentence: str, debug: bool = False) -> Dict[str, Any]`

Main entity extraction function.

**Parameters:**
- `sentence` (str): Input sentence to extract entities from
- `debug` (bool): Enable debug output for troubleshooting

**Returns:**
- `original_sentence`: The input sentence
- `parameterized_sentence`: Sentence with entities replaced by tokens
- `nlp_entities`: Entities extracted by NLP processor
- `hr_entities`: Entities from high-resolution inference
- `grouped_entities`: Final structured entities from grouping layer
- `status`: Overall processing status ("success", "error", "needs_llm_fix", "no_entities_found")
- `notes`: Any processing notes or warnings

### `extract_entities_simple(sentence: str) -> List[Dict[str, Any]]`

Simplified version that returns just the grouped entities.

**Returns:** List of entity group dictionaries

### `extract_entities_with_confidence(sentence: str) -> Dict[str, Any]`

Extract entities with confidence scores and detailed processing information.

**Returns:** Extended result dictionary with confidence scores and processing summary

## Entity Group Structure

Each entity group contains:

```python
{
    "action": "add",                    # Action verb (add, remove, check, etc.)
    "intent": "add",                    # Mapped intent with confidence
    "intent_confidence": 0.95,          # Confidence score for intent mapping
    "brands": ["Coca Cola"],            # Brand names
    "products": ["soda"],               # Product names
    "quantities": ["2"],                # Quantities
    "units": ["bottles"],               # Units of measurement
    "tokens": ["cold"],                 # Additional descriptive tokens
    "group_status": "ok"                # Group processing status
}
```

## Status Codes

- `"success"`: Entities successfully extracted and grouped
- `"error"`: Processing error occurred
- `"needs_llm_fix"`: Entities found but may need LLM processing for disambiguation
- `"no_entities_found"`: No entities could be extracted from the sentence

## Examples

### Example 1: Simple Addition
```python
sentence = "Add 2 bags of rice"
result = extract_entities(sentence)

# Output:
# Status: success
# Parameterized: add 2 unittoken of producttoken
# Groups: [{"action": "add", "products": ["rice"], "quantities": ["2"], "units": ["bags"]}]
```

### Example 2: Complex Multi-Entity
```python
sentence = "Add 2 bags of rice and remove 1 bottle of Coca Cola"
result = extract_entities(sentence)

# Output:
# Status: success
# Groups: [
#   {"action": "add", "products": ["rice"], "quantities": ["2"], "units": ["bags"]},
#   {"action": "remove", "brands": ["Coca Cola"], "products": ["soda"], "quantities": ["1"], "units": ["bottle"]}
# ]
```

### Example 3: Context-Dependent
```python
sentence = "Check if you have Nike shoes"
result = extract_entities(sentence)

# Output:
# Status: success
# Groups: [{"action": "check", "brands": ["Nike"], "products": ["shoes"]}]
```

## Error Handling

The pipeline includes comprehensive error handling:

```python
result = extract_entities("Invalid input")

if result['status'] == 'error':
    print(f"Error: {result['notes']}")
elif result['status'] == 'needs_llm_fix':
    print("Entities found but may need additional processing")
    print(f"Notes: {result['notes']}")
```

## Testing

Run the test suite:

```bash
python test_extract_entities.py
```

Run usage examples:

```bash
python example_usage.py
```

## Dependencies

- `nlp_processor.py`: spaCy-based entity extraction and parameterization
- `ner_inference.py`: NER inference using trained model
- `entity_grouping.py`: Entity grouping and semantic processing

## Notes

- The pipeline is designed to handle e-commerce and shopping-related sentences
- Entity recognition is optimized for products, brands, quantities, and units
- Complex sentences with multiple actions are automatically split into separate groups
- Context propagation helps resolve ambiguous references
