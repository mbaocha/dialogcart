# Luma - Service/Reservation Booking Pipeline

**Version:** 1.0.0  
**Status:** ✅ Production Ready

A clean, typed, modular service/reservation booking system that processes natural language booking requests and extracts structured booking information.

---

## 📖 What is Luma?

Luma is an **NLP-based service/reservation booking pipeline** that processes natural language booking requests and extracts structured information:

**Input:**  
`"book haircut tomorrow at 2pm"`

**Output:**
```json
{
  "stages": {
    "extraction": {
      "business_categories": [{"text": "haircut", "canonical": "haircut"}],
      "dates": [{"text": "tomorrow"}],
      "times": [{"text": "2pm"}]
    },
    "intent": {
      "intent": "CREATE_BOOKING",
      "confidence": "HIGH"
    },
    "semantic": {
      "resolved_booking": {
        "date_mode": "relative",
        "date_refs": ["tomorrow"],
        "time_mode": "exact",
        "time_refs": ["2pm"]
      }
    },
    "calendar": {
      "calendar_booking": {
        "date_range": {"start": "2025-01-15", "end": "2025-01-15"},
        "time_range": {"start_time": "14:00", "end_time": "14:00"},
        "datetime_range": {
          "start": "2025-01-15T14:00:00Z",
          "end": "2025-01-15T14:00:00Z"
        }
      }
    }
  },
  "clarification": {
    "needed": false
  }
}
```

### 🌟 Key Features

- ✅ **Type-Safe** - Typed dataclasses with full IDE support
- ✅ **Modular** - Clean separation of concerns across pipeline stages
- ✅ **Smart** - Intent resolution, semantic resolution, calendar binding
- ✅ **Clarification System** - Template-driven clarification prompts
- ✅ **Tested** - Comprehensive test suite
- ✅ **Production-Ready** - REST API, interactive CLI
- ✅ **Configurable** - Centralized configuration via `config.py`
- ✅ **Timezone-Aware** - Full timezone support for calendar binding

---

## 🚀 Quick Start

### Installation

```bash
cd src
pip install -r luma/requirements.txt

# Download spaCy model
python -m spacy download en_core_web_sm
```

### Basic Usage

```python
from luma.test import run_full_pipeline

# Run complete pipeline
result = run_full_pipeline(
    "book haircut tomorrow at 2pm",
    domain="service",
    timezone="UTC"
)

# Access results
semantic = result["stages"]["semantic"]
calendar = result["stages"]["calendar"]

print(semantic["resolved_booking"]["date_mode"])  # "relative"
print(calendar["calendar_booking"]["time_range"])  # {"start_time": "14:00", ...}
```

### REST API

```bash
# Start server
cd src
python luma/api.py

# Test endpoint
curl -X POST http://localhost:9001/book \
  -H "Content-Type: application/json" \
  -d '{"text": "book haircut tomorrow at 2pm"}' | jq
```

### Interactive CLI

```bash
cd src
python luma/cli/interactive.py

💬 Enter booking request: book haircut tomorrow at 2pm
# See pretty-printed pipeline results
```

---

## 🏗️ Architecture

### Six-Stage Pipeline

```
Input: "book haircut tomorrow at 2pm"
         ↓
┌─────────────────────────────────────────────┐
│  STAGE 1: Entity Extraction                 │
│  ✅ Extracts: services, dates, times        │
│  ✅ Normalizes: "haircut" → canonical        │
│  ✅ Parameterizes: "haircut" → servicetoken  │
└───────────────────┬─────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  STAGE 2: Intent Resolution                  │
│  ✅ Determines: CREATE_BOOKING, QUERY, etc.  │
│  ✅ Confidence: HIGH, MEDIUM, LOW            │
└───────────────────┬─────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  STAGE 3: Structural Interpretation         │
│  ✅ Booking count, service scope            │
│  ✅ Time scope, date scope                  │
│  ✅ Time type (exact, range, window)        │
└───────────────────┬─────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  STAGE 4: Appointment Grouping              │
│  ✅ Groups services with date/time           │
│  ✅ Validates booking structure              │
└───────────────────┬─────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  STAGE 5: Semantic Resolution               │
│  ✅ Resolves: date_mode, time_mode          │
│  ✅ Detects: ambiguity, missing info         │
│  ✅ Generates: clarification requests        │
└───────────────────┬─────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  STAGE 6: Calendar Binding                  │
│  ✅ Converts: relative → ISO-8601 dates     │
│  ✅ Converts: time refs → 24h format         │
│  ✅ Validates: date/time constraints         │
└───────────────────┬─────────────────────────┘
                    ↓
         CalendarBindingResult (Typed)
```

### File Structure

```
src/luma/
├── config.py                   # ⚙️  Central configuration
├── test.py                     # 🧪 Full pipeline test runner
│
├── extraction/                 # 🔵 STAGE 1: Entity Extraction
│   ├── matcher.py              # Main EntityMatcher class
│   ├── entity_loading.py       # Entity catalog loading
│   └── ...
│
├── grouping/                   # 🟡 STAGE 2 & 4: Intent & Grouping
│   ├── reservation_intent_resolver.py  # Intent resolution
│   └── appointment_grouper.py # Appointment grouping
│
├── structure/                  # 🟢 STAGE 3: Structural Analysis
│   └── interpreter.py         # Structural interpretation
│
├── resolution/                 # 🟠 STAGE 5: Semantic Resolution
│   └── semantic_resolver.py   # Semantic resolution & ambiguity detection
│
├── calendar/                   # 🔴 STAGE 6: Calendar Binding
│   ├── calendar_binder.py      # Calendar binding logic
│   └── ...
│
├── clarification/              # 💬 Clarification System
│   ├── reasons.py              # ClarificationReason enum
│   ├── models.py               # Clarification dataclass
│   └── renderer.py             # Template renderer
│
├── cli/                        # 🖥️  Interactive Tools
│   └── interactive.py          # REPL for testing
│
├── api.py                      # 🌐 REST API
└── README.md                   # This file
```

---

## 🎯 Core Components

### 1. EntityMatcher (Stage 1)
Extracts services, dates, times, and durations:

```python
from luma.extraction.matcher import EntityMatcher

matcher = EntityMatcher(domain="service", entity_file="...")
result = matcher.extract_with_parameterization("book haircut tomorrow at 2pm")

print(result["service_families"])  # [{"text": "haircut", ...}]
print(result["dates"])            # [{"text": "tomorrow"}]
print(result["times"])            # [{"text": "2pm"}]
```

### 2. ReservationIntentResolver (Stage 2)
Determines user intent:

```python
from luma.grouping.reservation_intent_resolver import ReservationIntentResolver

resolver = ReservationIntentResolver()
intent, confidence = resolver.resolve_intent(
    "book haircut tomorrow at 2pm",
    extraction_result
)

print(intent)      # "CREATE_BOOKING"
print(confidence)  # "HIGH"
```

### 3. Structural Interpreter (Stage 3)
Analyzes booking structure:

```python
from luma.structure.interpreter import interpret_structure

structure = interpret_structure(psentence, extraction_result)

print(structure.booking_count)  # 1
print(structure.service_scope)  # "single"
print(structure.time_type)      # "exact"
```

### 4. Semantic Resolver (Stage 5)
Resolves semantics and detects ambiguity:

```python
from luma.resolution.semantic_resolver import resolve_semantics

semantic_result = resolve_semantics(grouped_result, extraction_result)

print(semantic_result.resolved_booking["date_mode"])  # "relative"
print(semantic_result.needs_clarification)            # False
```

### 5. Calendar Binder (Stage 6)
Converts to ISO-8601 dates/times:

```python
from luma.calendar.calendar_binder import bind_calendar

calendar_result = bind_calendar(
    semantic_result,
    now=datetime.now(),
    timezone="UTC",
    intent="CREATE_BOOKING"
)

print(calendar_result.calendar_booking["datetime_range"])
# {"start": "2025-01-15T14:00:00Z", "end": "2025-01-15T14:00:00Z"}
```

### 6. Clarification System
Template-driven clarification prompts:

```python
from luma.clarification import render_clarification, Clarification, ClarificationReason

clarification = Clarification(
    reason=ClarificationReason.AMBIGUOUS_TIME_NO_WINDOW,
    data={"time": "2"}
)

message = render_clarification(clarification)
print(message)  # "Do you mean 2am or 2pm?"
```

---

## 🎛️ Configuration

All settings centralized in `config.py`:

```python
from luma import config

# View configuration
print(config.summary())

# Check settings
print(config.API_PORT)               # 9001
print(config.LOG_LEVEL)              # INFO
```

### Environment Variables

```bash
# API
export PORT=9001

# Logging
export LOG_LEVEL=INFO
export LOG_FILE=luma.log

# Debug
export DEBUG_NLP=1
```

---

## 💡 Usage Examples

### Example 1: Simple Booking

```python
from luma.test import run_full_pipeline

result = run_full_pipeline("book haircut tomorrow at 2pm")

calendar = result["stages"]["calendar"]
booking = calendar["calendar_booking"]

print(f"Date: {booking['date_range']['start']}")
print(f"Time: {booking['time_range']['start_time']}")
```

### Example 2: Multi-Service Booking

```python
result = run_full_pipeline(
    "Can I get a haircut and beard trim tomorrow at 1pm?"
)

intent = result["stages"]["intent"]
print(intent["intent"])  # "CREATE_BOOKING"
```

### Example 3: Clarification Needed

```python
result = run_full_pipeline("book haircut saturday at 2")

clarification = result.get("clarification", {})
if clarification.get("needed"):
    print(clarification["message"])
    # "Do you mean 2am or 2pm?"
```

### Example 4: Time Range

```python
result = run_full_pipeline("book massage today around 6ish")

semantic = result["stages"]["semantic"]
resolved = semantic["resolved_booking"]

print(resolved["time_mode"])  # "range" or needs clarification
```

---

## 🧪 Testing

### Run Full Pipeline Test

```bash
cd src
python -m luma.test --examples
```

### Run Unit Tests

```bash
cd src
pytest luma/tests/ -v
```

### Interactive Testing

```bash
python luma/cli/interactive.py
```

---

## 🌐 REST API

### Start Server

```bash
cd src
python luma/api.py
# Server runs on http://localhost:9001
```

### Endpoints

**POST `/book`** - Process booking request
```bash
curl -X POST http://localhost:9001/book \
  -H "Content-Type: application/json" \
  -d '{"text": "book haircut tomorrow at 2pm", "domain": "service"}' | jq
```

**GET `/health`** - Health check  
**GET `/info`** - API information

---

## 📊 Pipeline Stages

### Stage 1: Entity Extraction
- Extracts services, dates, times, durations
- Normalizes entity text
- Parameterizes sentence

### Stage 2: Intent Resolution
- Determines user intent (CREATE_BOOKING, QUERY, etc.)
- Assigns confidence level

### Stage 3: Structural Interpretation
- Analyzes booking count
- Determines service/time/date scope
- Identifies time type (exact, range, window)

### Stage 4: Appointment Grouping
- Groups services with date/time references
- Validates booking structure

### Stage 5: Semantic Resolution
- Resolves date/time modes
- Detects ambiguity
- Generates clarification requests

### Stage 6: Calendar Binding
- Converts relative dates to ISO-8601
- Converts time references to 24h format
- Validates date/time constraints

---

## 🔧 Advanced Features

### Clarification Templates

All clarification messages are template-driven and stored in JSON:

```json
{
  "AMBIGUOUS_TIME_NO_WINDOW": {
    "template": "Do you mean {{time}}am or {{time}}pm?",
    "required_fields": ["time"]
  }
}
```

### Timezone Support

Full timezone awareness for calendar binding:

```python
result = run_full_pipeline(
    "book haircut tomorrow at 2pm",
    timezone="America/New_York"
)
```

### Ambiguity Detection

Automatic detection of:
- Bare weekdays ("saturday")
- Hour-only times ("at 2")
- Fuzzy hours ("around 6ish")
- Missing information

---

## ⚙️ Configuration

### Feature Toggles

```bash
# API
export PORT=9001

# Logging
export LOG_LEVEL=INFO
export LOG_FILE=luma.log
```

### Debug Mode

```bash
export DEBUG_NLP=1
python luma/api.py
# See detailed debug logs
```

---

## 📚 Documentation

| File | Description |
|------|-------------|
| `README.md` | This file - main documentation |
| `NLU_CONTRACT.md` | Luma/Core boundary — intents, operations, facts |
| `test.py` | Full pipeline test runner |
| `api.py` | REST API implementation |
| `cli/interactive.py` | Interactive CLI |

Core availability interaction contract (operation field, utterance mapping): [`../core/orchestration/contracts/AVAILABILITY_INTERACTION_CONTRACT.md`](../core/orchestration/contracts/AVAILABILITY_INTERACTION_CONTRACT.md).

---

## 🐛 Troubleshooting

### Issue: ModuleNotFoundError

**Solution:** Run from `src/` directory
```bash
cd src
python luma/api.py
```

### Issue: spaCy model not found

**Solution:** Download spaCy model
```bash
python -m spacy download en_core_web_sm
```

### Issue: Normalization directory not found

**Solution:** Ensure normalization files exist in `luma/config/data/` (or fallback to `luma/store/normalization/` or `intents/normalization/`)

---

## 🎓 Learn More

### Quick Guides
- 🚀 **Quick Start**: See examples above
- ⚙️ **Configuration**: See `config.py`
- 🌐 **REST API**: See `api.py`
- 🖥️ **CLI Tools**: See `cli/interactive.py`

### For Developers
- 📂 **File Structure**: See Architecture section
- 🧪 **Testing**: Run `python -m luma.test --examples`

---

## 🤝 Contributing

### Running Tests

```bash
cd src
pytest luma/tests/ -v
python -m luma.test --examples
```

### Code Style

```bash
# No linter errors
flake8 luma/
mypy luma/
```

---

## 📝 License

Part of the DialogCart project.

---

## 🎉 Summary

**Luma** is a production-ready service/reservation booking pipeline that:

- ✅ Processes natural language booking requests
- ✅ Extracts services, dates, times with high accuracy
- ✅ Resolves intent and semantics
- ✅ Binds to calendar dates/times
- ✅ Generates clarification prompts when needed
- ✅ REST API and CLI ready
- ✅ Type-safe and well-tested

**Ready for production use!** 🚀

---

**Version:** 1.0.0  
**Last Updated:** 2025-01-15  
**Status:** ✅ Production Ready
