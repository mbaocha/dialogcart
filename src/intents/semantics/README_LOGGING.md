# NLP Logging Control

This document explains how to control debug logging in the NLP entity extraction pipeline.

## Overview

By default, **all debug logs are disabled** and only the final JSON response will be printed.

## Enabling Debug Logs

To enable debug logs, set the environment variable `DEBUG_NLP=1` before running your script.

### Examples

#### Windows (PowerShell)
```powershell
# Enable debug logs
$env:DEBUG_NLP="1"
python test_classify_interactive.py

# Disable debug logs (default)
$env:DEBUG_NLP="0"
python test_classify_interactive.py
```

#### Windows (Command Prompt)
```cmd
# Enable debug logs
set DEBUG_NLP=1
python test_classify_interactive.py

# Disable debug logs (default)
set DEBUG_NLP=0
python test_classify_interactive.py
```

#### Linux/Mac
```bash
# Enable debug logs
export DEBUG_NLP=1
python test_classify_interactive.py

# Disable debug logs (default)
export DEBUG_NLP=0
python test_classify_interactive.py

# Or run inline
DEBUG_NLP=1 python test_classify_interactive.py
```

## What Gets Logged

When debug logging is **enabled** (`DEBUG_NLP=1`), you'll see:
- Token normalization steps
- Entity extraction details
- NER inference outputs
- Quantity-product alignment process
- Token parameterization steps
- Entity grouping decisions
- Token-to-original value mapping

When debug logging is **disabled** (default), you'll see:
- Only the final JSON response object

## Files Affected

The following files respect the `DEBUG_NLP` environment variable:
- `nlp_processor.py`
- `ner_inference.py`
- `entity_grouping.py`
- `entity_extraction_pipeline.py`

## Example Output

### With DEBUG_NLP=0 (default)
```json
{
  "brands": ["mama gold"],
  "products": ["rice"],
  "variants": [],
  "quantities": [],
  "units": [],
  "osentence": "do you sell mamma gold rice",
  "psentence": "do you sell brandtoken producttoken"
}
```

### With DEBUG_NLP=1
```
normalized:  ['do', 'you', 'sell', 'mamma', 'gold', 'rice']
[DEBUG] Tokens before replacement: ['do', 'you', 'sell', 'mamma', 'gold', 'rice']
[DEBUG] Replacements: [(5, 6, 'producttoken'), (3, 5, 'brandtoken')]
[DEBUG] Parameterized tokens after replacement: ['do', 'you', 'sell', 'brandtoken', 'producttoken']
[DEBUG] Word: do
[DEBUG] Label: B-ACTION
...
```

## Usage in Code

If you're importing these modules in your code, you can also control logging programmatically by setting the environment variable before importing:

```python
import os
os.environ["DEBUG_NLP"] = "1"  # Enable debug logs

# Now import your modules
from nlp_processor import extract_entities_with_parameterization
```

