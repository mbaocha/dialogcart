# Orchestration Layer Tests

## Running Tests

### From project root (`dialogcart/`)

```bash
# E2E test
python3 -m core.tests.orchestration.test_orchestrator_e2e

# Interactive test
python3 -m core.tests.orchestration.test_interactive
```

### From src/ directory

```bash
# E2E test
python3 -m core.tests.orchestration.test_orchestrator_e2e

# Interactive test
python3 -m core.tests.orchestration.test_interactive
```

### Using pytest (from project root or src/)

```bash
# All orchestration tests
pytest src/core/tests/orchestration/

# Specific test file
pytest src/core/tests/orchestration/test_orchestrator_flow.py

# Specific test
pytest src/core/tests/orchestration/test_orchestrator_flow.py::test_resolved_flow_calls_booking_client
```

## Test Files

- **test_orchestrator_flow.py**: Unit tests with mocks (no external dependencies)
- **test_orchestrator_e2e.py**: End-to-end tests (requires Luma and business APIs running)
- **test_interactive.py**: Interactive/manual testing script
- **contracts/test_luma_contracts.py**: Contract validation tests

## Requirements

- E2E and interactive tests require:
  - Luma API running (default: http://localhost:9001)
  - Internal/Business APIs running (default: http://localhost:3000)
  - Environment variables configured (.env file)

