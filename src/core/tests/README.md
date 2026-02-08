# Dialogcart-Core Tests

Tests are organized by layer to match the three-layer responsibility model.

## Structure

```
tests/
├── orchestration/          # Orchestration layer tests
│   ├── test_orchestrator_flow.py      # Orchestrator flow tests
│   ├── test_orchestrator_e2e.py       # End-to-end tests
│   ├── test_interactive.py            # Interactive/manual tests
│   └── contracts/                     # Contract validation tests
│       └── test_luma_contracts.py
├── routing/               # Routing layer tests
│   └── (tests to be added)
└── rendering/             # Rendering layer tests
    └── (tests to be added)
```

## Running Tests

### All tests
```bash
pytest src/core/tests/
```

### By layer
```bash
# Orchestration layer tests
pytest src/core/tests/orchestration/

# Routing layer tests
pytest src/core/tests/routing/

# Rendering layer tests
pytest src/core/tests/rendering/
```

### Specific test file
```bash
pytest src/core/tests/orchestration/test_orchestrator_flow.py
```

### E2E tests (require services running)
```bash
# From project root (dialogcart/)
python3 -m core.tests.orchestration.test_orchestrator_e2e

# Note: Don't use .py extension when running as module
```

### Interactive tests
```bash
# From project root (dialogcart/)
python3 -m core.tests.orchestration.test_interactive
```

## Test Organization

### Orchestration Layer Tests
- **test_orchestrator_flow.py**: Unit tests for orchestrator flow (mocked)
- **test_orchestrator_e2e.py**: End-to-end tests with real API calls
- **test_interactive.py**: Interactive/manual testing utilities
- **contracts/**: Contract validation tests

### Routing Layer Tests
- Tests for `get_template_key()` - clarification routing
- Tests for `get_action_name()` - intent routing
- Tests for config file loading

### Rendering Layer Tests
- Tests for `render_outcome_to_whatsapp()` - outcome rendering
- Tests for template lookup and interpolation
- Tests for WhatsApp message formatting

