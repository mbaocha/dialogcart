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

### Using the Test Entry Point (Recommended)

The easiest way to run tests is using the `test.py` entry point:

```bash
# Run all tests
python core/tests/test.py

# Run specific category
python core/tests/test.py --category orchestration
python core/tests/test.py --category planning
python core/tests/test.py --category e2e
python core/tests/test.py --category integration
python core/tests/test.py --category execution
python core/tests/test.py --category session
python core/tests/test.py --category workflows
python core/tests/test.py --category intents

# List all available categories
python core/tests/test.py --list

# Pass additional pytest arguments
python core/tests/test.py --category e2e -- -v --tb=short
python core/tests/test.py --category planning -- -k scenario4

# Run E2E tests with real Luma (requires RUN_REAL_LUMA_E2E=true)
RUN_REAL_LUMA_E2E=true python core/tests/test.py --category e2e
```

### Using pytest directly

#### All tests
```bash
pytest src/core/tests/
```

#### By category
```bash
# Orchestration layer tests
pytest src/core/tests/orchestration/

# Planning tests
pytest src/core/tests/planning/

# E2E tests
pytest src/core/tests/e2e/

# Integration tests
pytest src/core/tests/integration/

# Execution tests
pytest src/core/tests/execution/

# Session tests
pytest src/core/tests/session/

# Workflow tests
pytest src/core/tests/workflows/

# Intent tests
pytest src/core/tests/intents/
```

#### Specific test file
```bash
pytest src/core/tests/orchestration/test_orchestrator_flow.py
```

#### E2E tests
```bash
# E2E tests MUST be run with pytest (not directly with python)
# Pytest automatically configures PYTHONPATH via pytest.ini

# Run all E2E tests
pytest src/core/tests/e2e/

# Run specific E2E test
pytest src/core/tests/e2e/test_core_capability_noop_e2e.py

# Note: Direct execution with `python` is NOT supported for E2E tests
# because they rely on pytest's automatic PYTHONPATH configuration
```

#### Interactive tests
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

