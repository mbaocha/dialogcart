# Intent Services

This directory contains the refactored intent classification services with a unified entry point.

## Structure

```
src/intents/
├── llm_service/           # LLM-only REST service
│   ├── core/             # Refactored LLM logic
│   ├── app.py            # REST API (port 9100)
│   └── cli.py            # Interactive CLI
├── unified_api/          # Unified entry point (Rasa + LLM fallback)
│   ├── app.py            # REST API (port 9000)
│   └── cli.py            # Interactive CLI
├── nlp_intent_service/   # Existing Rasa service (unchanged)
└── llm.py               # Compatibility wrapper
```

## Usage

### 1. LLM Service Only
```bash
# Interactive CLI (same as original llm.py)
python src/intents/llm_service/cli.py

# REST API
python src/intents/llm_service/app.py
# Test: curl -X POST http://localhost:9100/classify -d '{"text": "add rice"}'
```

### 2. Unified API (Rasa + LLM Fallback)
```bash
# Interactive CLI
python src/intents/unified_api/cli.py

# REST API
python src/intents/unified_api/app.py
# Test: curl -X POST http://localhost:9000/classify -d '{"text": "add rice"}'
```

### 3. Original Compatibility
```bash
# Still works exactly like before
python src/intents/llm.py
```

## Services

- **Rasa Service**: `http://localhost:8001` (existing)
- **LLM Service**: `http://localhost:9100` (new)
- **Unified API**: `http://localhost:9000` (new)

## Configuration

Set environment variables:
- `RASA_URL`: Rasa service URL (default: http://localhost:8001)
- `LLM_URL`: LLM service URL (default: http://localhost:9100)
- `OPENAI_API_KEY`: Required for LLM service

## Fallback Logic ok ok

The unified API tries Rasa first, then falls back to LLM if:
- Rasa confidence is "low"
- Rasa returns "NONE" intent
- Rasa service is unavailable

## No Breaking Changes

- `python src/intents/llm.py` works exactly as before
- All existing functionality preserved
- New REST endpoints added
- Modular structure for easier maintenance