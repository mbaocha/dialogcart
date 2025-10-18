# Quick Start - Decorator Logging

## ✅ What's Done

1. **Decorator system** added to `logging_config.py`
2. **Applied to** `core/pipeline.py` main functions
3. **Documentation** and examples created

## 🚀 Use It Now

### 1. Apply to Your Functions

```python
from luma.logging_config import log_function_call

@log_function_call()  # INFO level - production
def your_function(self, param1: str, param2: bool):
    return result

@log_function_call(level='DEBUG')  # DEBUG level only
def internal_function(self, data: list):
    return processed
```

### 2. Test It

```bash
cd src
python luma/examples/demo_decorator_logging.py
```

### 3. See the Logs

**Production (INFO):**
```json
{"level":"INFO","message":"your_function() called","param1_length":25,"param2":false}
{"level":"INFO","message":"your_function() completed","duration_ms":45.2}
```

**Debug (DEBUG):**
```json
{"level":"DEBUG","message":"your_function() input","param1":"actual text","param2":false}
{"level":"DEBUG","message":"your_function() output","result":{...}}
```

## 📋 What Gets Logged Automatically

✅ **Function called** with parameter summaries  
✅ **Function completed** with result summary  
✅ **Duration** in milliseconds  
✅ **Full input** (DEBUG mode)  
✅ **Full output** (DEBUG mode)  
✅ **Errors** with full tracebacks  

## 🎯 Where to Apply

### ✅ Apply Decorator To:
- Public API methods
- Main processing functions
- Performance-critical paths

### ❌ Keep Manual Logging For:
- Business logic decisions
- State changes
- Special warnings

## 📚 Full Documentation

- `DECORATOR_LOGGING_GUIDE.md` - Complete guide
- `LOGGING_IMPLEMENTATION_COMPLETE.md` - Full implementation details
- `examples/demo_decorator_logging.py` - Working example

## 🎉 Benefits

Before (10+ lines per function):
```python
def extract(self, sentence: str):
    logger.info("extract() called", extra={...})
    try:
        start = time.time()
        result = ...
        duration = time.time() - start
        logger.info("extract() completed", extra={...})
        return result
    except Exception as e:
        logger.error("extract() failed", exc_info=True)
        raise
```

After (1 line):
```python
@log_function_call()
def extract(self, sentence: str):
    return result
```

**80% less boilerplate code!** 🚀





