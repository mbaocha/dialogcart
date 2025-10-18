# Decorator-Based Logging Guide

## Overview

Luma API uses **decorator-based logging** for clean, consistent function logging without boilerplate code.

## How It Works

### Without Decorator (Old Way)
```python
def extract(self, sentence: str, force_llm: bool = False):
    logger.info("extract() called", extra={
        'sentence_length': len(sentence),
        'force_llm': force_llm
    })
    
    try:
        start_time = time.time()
        result = self._do_extraction(sentence)
        duration = (time.time() - start_time) * 1000
        
        logger.info("extract() completed", extra={
            'duration_ms': duration,
            'status': result.status,
            'groups_count': len(result.groups)
        })
        
        logger.debug("extract() output", extra={'result': result.to_dict()})
        
        return result
    except Exception as e:
        logger.error("extract() failed", exc_info=True)
        raise
```

**Problems:**
- 10+ lines of logging code
- Easy to forget
- Hard to maintain consistency
- Clutters business logic

### With Decorator (New Way)
```python
@log_function_call()
def extract(self, sentence: str, force_llm: bool = False):
    result = self._do_extraction(sentence)
    return result
```

**Benefits:**
- ✅ 1 line decorator
- ✅ Automatic input/output/timing logging
- ✅ Consistent everywhere
- ✅ Clean business logic

## Usage Examples

### Basic Usage (INFO level)
```python
from luma.logging_config import log_function_call

@log_function_call()
def extract(self, sentence: str, force_llm: bool = False):
    """Automatically logs input summary and output."""
    return result
```

**Logs produced:**
```json
{"level":"INFO","message":"extract() called","sentence_length":25,"force_llm":false}
{"level":"INFO","message":"extract() completed","duration_ms":45.2,"status":"success","groups_count":2}
```

### DEBUG Level (More Detail)
```python
@log_function_call(level='DEBUG')
def _internal_process(self, tokens: list):
    """Only logs when LOG_LEVEL=DEBUG."""
    return processed
```

**Logs (only in DEBUG mode):**
```json
{"level":"DEBUG","message":"_internal_process() called","tokens_count":10}
{"level":"DEBUG","message":"_internal_process() input","tokens":["add","2","kg","rice"]}
{"level":"DEBUG","message":"_internal_process() completed","duration_ms":12.3}
{"level":"DEBUG","message":"_internal_process() output","result":{...}}
```

### Selective Logging
```python
# Log input but not output
@log_function_call(log_args=True, log_result=False)
def process_tokens(self, tokens: list):
    return processed

# Log output but not input
@log_function_call(log_args=False, log_result=True)
def generate_summary(self):
    return summary

# Only log timing (no input/output)
@log_function_call(log_time=True, log_args=False, log_result=False)
def expensive_operation(self):
    return result
```

### Custom Truncation
```python
# Truncate long strings at 100 chars
@log_function_call(truncate_at=100)
def process_long_text(self, text: str):
    return result
```

## What Gets Logged

### Automatic Summaries (INFO level)

The decorator automatically creates smart summaries:

**For arguments:**
- Strings: `{param}_length` = length
- Lists/Dicts: `{param}_count` = count
- Primitives: Include actual value
- Example: `sentence_length=25, force_llm=false`

**For results:**
- Objects with `.status`: Extract status
- Objects with `.groups`: Count groups
- Objects with `.route`: Include route
- Dicts: Extract common keys
- Lists: Include count
- Example: `status=success, groups_count=2, route=rule`

**Timing:**
- Always includes `duration_ms` (unless `log_time=False`)

### Full Details (DEBUG level)

When `LOG_LEVEL=DEBUG`:
- Full input parameters (truncated if long)
- Full result object (as dict if possible)
- All intermediate steps

## When to Use

### ✅ Use Decorators For:

**Public API methods:**
```python
@log_function_call()
def extract(self, sentence: str):
    ...

@log_function_call()
def extract_dict(self, sentence: str):
    ...
```

**Main processing functions:**
```python
@log_function_call(level='DEBUG')
def _extract_with_luma(self, sentence: str):
    ...

@log_function_call(level='DEBUG')
def group_entities(self, tokens, labels):
    ...
```

**Performance-critical paths:**
```python
@log_function_call(log_time=True)
def predict(self, sentence: str):
    ...
```

### ❌ Use Manual Logging For:

**Business logic decisions:**
```python
def decide_route(self, result):
    if needs_llm:
        logger.info("Falling back to LLM", extra={'reason': reason})
        return 'llm'
    return 'rule'
```

**State changes:**
```python
def initialize(self):
    logger.info("Loading NER model")
    self.model = load_model()
    logger.info("Model loaded successfully")
```

**Warnings and errors:**
```python
def validate(self, data):
    if len(data) == 0:
        logger.warning("Empty data received")
```

## Architecture Pattern

### Layer 1: API Layer (Manual)
```python
# api.py - Keep manual for request tracking
@app.route("/extract", methods=["POST"])
def extract():
    request_id = g.request_id
    logger.info("Processing request", extra={'request_id': request_id})
    # ... existing manual logging ...
```

### Layer 2: Pipeline Layer (Decorator)
```python
# core/pipeline.py
class EntityExtractionPipeline:
    @log_function_call()  # Auto-logs input/output
    def extract(self, sentence: str, force_llm: bool = False):
        # Manual logging for decisions
        if self.should_use_llm():
            logger.info("Using LLM extraction")
        return result
    
    @log_function_call(level='DEBUG')
    def _extract_with_luma(self, sentence: str):
        return result
```

### Layer 3: Component Layer (Decorator)
```python
# grouping/grouper.py
@log_function_call(level='DEBUG')
def group_entities(tokens, labels):
    return grouped_result
```

### Layer 4: Utility Layer (No logging)
```python
# Skip decorators for simple utilities
def normalize_text(text):
    return text.lower().strip()
```

## Testing the Decorator

Run the demo script:
```bash
cd src
python luma/examples/demo_decorator_logging.py
```

**INFO level (default):**
```bash
LOG_LEVEL=INFO python luma/examples/demo_decorator_logging.py
```

**DEBUG level (full details):**
```bash
LOG_LEVEL=DEBUG python luma/examples/demo_decorator_logging.py
```

## Implementation Details

### How It Works

1. **Function Call**: Decorator intercepts call
2. **Prepare Summary**: Extract arg lengths/counts
3. **Log Entry**: Log function called with summary
4. **Execute**: Run actual function with timing
5. **Prepare Result**: Extract status/counts from result
6. **Log Exit**: Log completion with result summary
7. **DEBUG Logs**: If enabled, log full input/output

### Error Handling

If function raises exception:
```json
{
  "level": "ERROR",
  "message": "extract() failed after 45.2ms",
  "error_type": "ValueError",
  "error_message": "Invalid input",
  "duration_ms": 45.2,
  "exception": "Traceback..."
}
```

### Truncation

Long strings/lists are automatically truncated:
- Strings > 500 chars: Truncated to 500 + '...'
- Lists > 10 items: Only first 10 + ['...']
- Configurable with `truncate_at` parameter

## Best Practices

1. **Use INFO for public APIs** - User-facing methods
2. **Use DEBUG for internals** - Implementation details
3. **Combine with manual logging** - For business logic
4. **Keep truncate_at reasonable** - Balance detail vs size
5. **Don't log sensitive data** - Decorator respects this

## Comparison

| Approach | Lines of Code | Consistency | Maintainability |
|----------|---------------|-------------|-----------------|
| **Manual logging** | 10-15 per function | Variable | Hard |
| **Decorator** | 1 per function | Perfect | Easy |

**Code reduction:** ~90% less logging boilerplate!

## Summary

✅ **Use decorators** for automatic, consistent logging  
✅ **Use manual logging** for business logic and decisions  
✅ **Combine both** for complete observability  

The decorator provides **professional-grade logging** with **minimal code**! 🚀





