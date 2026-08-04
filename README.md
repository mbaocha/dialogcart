# DialogCart

**DialogCart** is a conversational AI platform for handling service bookings, reservations, and payments through natural language interactions. The system processes user messages, extracts intent and entities, manages conversation state, and integrates with external capabilities like payment processing.

---

## 📋 Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Components](#components)
  - [Core](#core)
  - [Luma](#luma)
  - [Capabilities](#capabilities)
- [Quick Start](#quick-start)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [Configuration](#configuration)

---

## 🎯 Overview

DialogCart enables businesses to handle customer interactions through conversational interfaces (e.g., WhatsApp, web chat). The system:

- **Understands** natural language booking requests
- **Extracts** structured information (services, dates, times)
- **Manages** conversation state and context
- **Plans** actions based on collected information
- **Integrates** with external capabilities (payment, KYC, etc.)
- **Renders** responses in appropriate formats

### Example Flow

**User:** `"I want to book a haircut tomorrow at 2pm"`

**System Processing:**
1. Extracts: service="haircut", date="tomorrow", time="2pm"
2. Resolves intent: `CREATE_BOOKING`
3. Plans: Collect missing slots → Execute booking → Request payment
4. Responds: `"Great! I've booked your haircut for tomorrow at 2pm. Would you like to pay the deposit now?"`

---

## 🏗️ Architecture

DialogCart follows a **three-layer architecture** with clear separation of concerns:

```
┌─────────────────────────────────────────────────────────────┐
│                    User Interface Layer                      │
│              (WhatsApp, Web Chat, API Gateway)               │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                         Core Layer                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ Orchestration│  │   Planning   │  │   Routing    │     │
│  │              │  │              │  │              │     │
│  │ • Session    │  │ • Policy    │  │ • Intent     │     │
│  │ • State      │  │ • Slots      │  │ • Action     │     │
│  │ • Workflow   │  │ • Gates      │  │ • Template   │     │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘     │
│         │                 │                 │              │
│         └─────────────────┴─────────────────┘              │
│                           │                                 │
│  ┌────────────────────────▼────────────────────────┐       │
│  │            Rendering Layer                       │       │
│  │  • Outcome Rendering  • Clarification Templates │       │
│  │  • Capability Rendering  • System Messages      │       │
│  └─────────────────────────────────────────────────┘       │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                      Luma (NLU Layer)                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  Extraction  │  │   Intent     │  │   Semantic    │     │
│  │              │  │   Resolution │  │   Resolution  │     │
│  │ • Entities   │  │ • Intent     │  │ • Ambiguity   │     │
│  │ • Dates      │  │ • Confidence │  │ • Clarify     │     │
│  │ • Times      │  │              │  │ • Calendar    │     │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘     │
│         │                 │                 │              │
│         └─────────────────┴─────────────────┘              │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Capabilities Layer                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   Payment    │  │     KYC      │  │   Consent    │     │
│  │   Adapter    │  │   Adapter    │  │   Adapter    │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **User Message** → Core receives text input
2. **NLU Processing** → Core calls Luma to extract entities and intent
3. **Session Management** → Core retrieves/updates conversation state
4. **Planning** → Core determines what actions can be executed
5. **Capability Gates** → Core checks if external capabilities are needed
6. **Rendering** → Core formats response based on outcome
7. **Response** → User receives formatted message

---

## 🧩 Components

### Core

**Location:** `src/core/`

The **Core** layer is the orchestration engine that manages conversation flow, state, and decision-making. It does **NOT** execute actions—it only plans and returns structured outcomes.

#### Key Responsibilities

- **Orchestration** (`orchestration/`): Main message handling, session management, workflow coordination
- **Planning** (`planning/`): Policy-based planning, slot collection, capability gate evaluation, handler mapping
- **Rendering** (`rendering/`): Outcome rendering, clarification templates, capability prompts

#### Main Entry Point

```python
from core.orchestration.orchestrator import handle_message

result = handle_message(
    text="book haircut tomorrow at 2pm",
    user_id="user123",
    organization_id=1,
    session_store=session_store,  # Optional
    luma_client=luma_client,      # Optional (defaults to HTTP client)
    # ... other optional clients
)

# Result structure:
{
    "success": True,
    "outcome": {
        "intent_name": "CREATE_BOOKING",
        "status": "READY" | "NEEDS_CLARIFICATION" | "AWAITING_CAPABILITY",
        "slots": {"service": "haircut", "date": "tomorrow", "time": "2pm"},
        "missing_slots": [],
        "executable_actions": ["execute_booking"],
        "plan": {
            "status": "READY",
            "stage": "execution",
            "action": "execute_booking"
        }
    }
}
```

#### Core Principles

- **Planning Only**: Core never executes actions—it returns `executable_actions` for the execution layer
- **Stateless Planning**: Core is stateless; session state is managed externally
- **Capability Gates**: Core emits `AWAITING_CAPABILITY` when external capabilities are needed
- **Template-Driven**: All user-facing text comes from YAML templates

#### Key Modules

| Module | Purpose |
|--------|----------|
| `orchestration/orchestrator.py` | Main message handler, orchestrates full flow |
| `planning/planner/` | Policy-based planning engine |
| `planning/policy/` | Handler mapping, base intents, legacy intent→action |
| `rendering/` | Template-based response rendering |
| `orchestration/nlu/` | NLU client integration (`LumaClient` HTTP to `src/nlu`) |

---

### NLU

**Location:** `src/nlu/`

**NLU** is the production Natural Language Understanding service. It replaces the legacy rule-based `src/luma` pipeline with a two-stage SLM extractor plus calendar binding, and exposes the same `/resolve` HTTP contract Core already uses.

#### Pipeline Stages

1. **Stage 1** (`stages/stage1/`): Intent classification (lightweight LLM call)
2. **Stage 2** (`stages/stage2/`): Slot extraction via per-intent-group dispatchers
3. **Normalisation** (`pipeline.py`): Rule-based post-processing (dates, times, aliases, booking_id)
4. **Calendar Binding** (`calendar/`): Converts relative dates/times to ISO-8601

#### Usage

```bash
# Start NLU API server (default port 9002)
cd src
python -m nlu.api
# or from repo root: python run.py

# Process utterance
curl -X POST http://localhost:9002/resolve \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user123", "text": "book haircut tomorrow at 2pm", "domain": "service"}'
```

Core calls this service via `LumaClient` using `LUMA_BASE_URL` (default `http://localhost:9002`).

#### Key Features

- **Same `/resolve` contract** Core already consumes
- **SLM extraction** via Anthropic Haiku (stage1 + stage2)
- **Calendar binding** for ISO dates/times
- **Docker**: `src/nlu/Dockerfile` and `src/nlu/docker-compose.yml`

**Legacy:** `src/luma/` remains in the tree for reference but is not the production NLU.

---

### Extensions

**Location:** `src/extensions/`

Non-core behavior lives outside the booking kernel in two subpackages:

| Subpackage | Hook | Purpose |
|------------|------|---------|
| `extensions/capabilities` | `AWAITING_CAPABILITY` | Multi-turn capability gates (payment, KYC) |
| `extensions/handlers` | `HANDLER_DELEGATED` | Single-shot intent handlers (RAG) |

**See `src/extensions/README.md` for overview.**

#### capabilities (capability adapters)

**Location:** `src/extensions/capabilities/`

Gate adapters are invoked when Core emits `AWAITING_CAPABILITY`. They pause booking until a gate fact is satisfied (e.g. `payment_satisfied: True`).

```python
from extensions.capabilities.base import CapabilityAdapter, AdapterResponse

class PaymentAdapter(CapabilityAdapter):
    def start(self, context: Dict[str, Any]) -> AdapterResponse:
        """Called on first activation"""
        return AdapterResponse(
            completed=False,
            text="Please select payment method: credit card or PayPal",
            facts={}
        )
    
    def handle_input(self, user_input: str, context: Dict[str, Any]) -> AdapterResponse:
        """Called for each user message"""
        # Process user input
        return AdapterResponse(
            completed=True,
            text="Payment confirmed",
            facts={"payment_satisfied": True}
        )
    
    def abort(self, reason: str, context: Dict[str, Any]) -> None:
        """Called on interruption"""
        pass
```

#### Integration Flow

1. **Core emits capability gate:**
   ```json
   {
     "status": "AWAITING_CAPABILITY",
     "active_capability": "payment"
   }
   ```

2. **Capability runner activates adapter:**
   ```python
   adapter = registry["payment"]
   response = adapter.start(context)
   ```

3. **User provides input:**
   ```
   User: "credit card"
   ```

4. **Adapter processes input:**
   ```python
   response = adapter.handle_input("credit card", context)
   # Returns: AdapterResponse(completed=True, facts={"payment_satisfied": True})
   ```

5. **Core proceeds:**
   - Core reads `payment_satisfied: True` from facts
   - Core re-plans and proceeds to next step

#### Available Adapters

- **NoOp Adapter** (`adapters/noop.py`): Test adapter that always completes
- **Payment Adapter** (`adapters/payment.py`): Payment processing integration

**See `src/extensions/capabilities/README.md` for detailed documentation.**

#### Handlers (intent handlers)

**Location:** `src/extensions/handlers/`

Single-shot handlers answer non-booking intents (RAG) when Core emits `HANDLER_DELEGATED`.

```python
from extensions.handlers.base import IntentHandler, HandlerResponse
```

Config: `core/planning/policy/intent_handlers.yaml`

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- pip
- Anthropic API key for NLU SLM extraction (`ANTHROPIC_API_KEY`)

### Installation

```bash
# Clone repository
git clone <repository-url>
cd dialogcart

# Install dependencies
pip install -r src/requirements.txt
pip install -r src/nlu/requirements.txt
```

### Basic Usage

```python
from core.orchestration.orchestrator import handle_message

# Handle a user message
result = handle_message(
    text="book haircut tomorrow at 2pm",
    user_id="user123",
    organization_id=1
)

print(result["outcome"]["intent_name"])  # "CREATE_BOOKING"
print(result["outcome"]["status"])        # "READY" or "NEEDS_CLARIFICATION"
print(result["outcome"]["slots"])         # {"service": "haircut", ...}
```

### Running the Demo

```bash
cd src
python demo.py
```

---

## 🧪 Testing

### Test Structure

Tests are organized by component:

```
src/
├── core/tests/          # Core layer tests
│   ├── planning/        # Plan contract (primary gate)
│   ├── execution/       # Mock booking flows (primary gate)
│   ├── harness/         # Shared runners/clients (not a pytest category)
│   └── …                # orchestration, session, rendering, smoke, …
├── nlu/tests/           # Production NLU pipeline tests
└── extensions/…/tests/  # Capability / handler tests
```

### Running Tests

#### Core Tests

```bash
# Run all core tests
cd src
python core/tests/test.py

# Run specific category
python core/tests/test.py --category planning
python core/tests/test.py --category execution
python core/tests/test.py --category orchestration

# Using pytest directly
pytest src/core/tests/
pytest src/core/tests/orchestration/
python core/tests/test.py --category planning
RUN_REAL_LUMA_E2E=true python core/tests/test.py --category smoke
```

#### NLU Tests

```bash
# Run production NLU tests
pytest src/nlu/tests/

# Or from src/
cd src
pytest nlu/tests/
```

#### Capability Tests

```bash
# Run capability adapter tests
cd src
pytest capabilities/tests/
```

#### E2E Tests with Real Luma

```bash
# Set environment variable
export RUN_REAL_LUMA_E2E=true

# Run E2E tests
python core/tests/test.py --category e2e
```

### Interactive Testing

```bash
# Core interactive testing
cd src
python -m core.tests.orchestration.test_interactive

# Start production NLU (default http://localhost:9002)
python -m nlu.api
# or from repo root: python run.py
```

---

## 📁 Project Structure

```
dialogcart/
├── src/
│   ├── core/                    # Core orchestration layer
│   │   ├── orchestration/       # Main orchestrator, session management
│   │   ├── planning/            # Policy-based planning
│   │   ├── rendering/          # Template-based rendering
│   │   │   └── templates/       # All rendering templates (YAML + JSON)
│   │   ├── policy/              # Intent policies
│   │   ├── config/              # Configuration files
│   │   └── tests/               # Core tests
│   │
│   ├── nlu/                     # Production NLU service
│   │   ├── stages/              # Stage1 intent + stage2 slot extractors
│   │   ├── calendar/            # Calendar binding
│   │   ├── clarification/       # Clarification reason enums
│   │   ├── config/              # Configuration
│   │   ├── pipeline.py          # NLUPipeline
│   │   ├── api.py               # REST API (port 9002)
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml
│   │   └── tests/               # NLU tests
│   │
│   ├── luma/                    # Legacy NLU (not production)
│   │
│   ├── extensions/              # Capability / handler extensions
│   │
│   ├── app.py                   # Legacy Lambda handler
│   ├── router.py                # Legacy router
│   └── demo.py                  # Demo script
│
├── run.py                       # Start NLU with fixed test date
├── tests/                       # Root-level tests
├── pytest.ini                    # Pytest configuration
└── README.md                    # This file
```

---

## ⚙️ Configuration

### Environment Variables

```bash
# Core Configuration
ORGANIZATION_ID=1
DOMAIN=service

# NLU service (HTTP; env name kept for compatibility)
LUMA_BASE_URL=http://localhost:9002
LOG_LEVEL=INFO

# Session Store (optional)
SESSION_STORE_TYPE=memory  # or 'redis', 'database'

# Capabilities
CAPABILITY_REGISTRY_ENABLED=true
```

### Configuration Files

- **Core Policies**: `src/core/config/intent_policy.yaml`, `dialog_policy.yaml`
- **NLU Config**: `src/nlu/config/`
- **Core Rendering Templates**: `src/core/rendering/templates/*.yaml` (outcomes, clarifications, capabilities, system)

---

## 📚 Documentation

### Component Documentation

- **Core**: See `src/core/` module docstrings
- **NLU**: See `src/nlu/` (pipeline + `api.py`)
- **Legacy Luma**: `src/luma/README.md` (reference only)
- **Capabilities**: See `src/extensions/README.md`

### Key Concepts

- **Planning vs Execution**: Core only plans; execution is handled separately
- **Capability Gates**: External capabilities are invoked via gates
- **Session State**: Managed externally; Core is stateless
- **Template-Driven**: All user-facing text comes from YAML templates

---

## 🔧 Development

### Code Style

- Follow PEP 8
- Use type hints
- Write docstrings for public functions
- Run linters: `flake8`, `mypy`

### Adding a New Capability

1. Create adapter in `src/capabilities/adapters/`
2. Implement `CapabilityAdapter` interface
3. Register in `src/capabilities/registry.py`
4. Add tests in `src/capabilities/tests/`

### Adding a New Intent

1. Define intent in `src/core/config/intent_policy.yaml`
2. Add planning logic in `src/core/planning/`
3. Add handler mapping in `src/core/planning/policy/` if needed
4. Add rendering templates in `src/core/rendering/templates/`

---

## 🤝 Contributing

1. Create a feature branch
2. Write tests for new functionality
3. Ensure all tests pass
4. Submit a pull request

---

## 🎉 Summary

**DialogCart** is a conversational AI platform with a production-oriented architecture designed for handling service bookings, reservations, and payments through natural language interactions. The system:

- Processes natural language booking requests
- Manages conversation state and context
- Plans actions based on collected information
- Integrates with external capabilities
- Renders responses in appropriate formats
- Includes comprehensive test coverage and documentation


