# Test Organization

This directory contains all tests for the Bulkpot application, organized by type and module.

## Directory Structure

```
tests/
├── README.md                    # This file
├── run_tests.py                 # Test runner script
├── unit/                        # Unit tests
│   ├── test_api/               # API function tests
│   │   ├── test_update_user.py
│   │   └── test_update_user_fix.py
│   ├── test_db/                # Database function tests
│   │   └── test_phone_lookup.py
│   ├── test_utils/             # Utility function tests
│   │   ├── test_float_conversion.py
│   │   └── test_utils_migration.py
│   └── test_agents/            # Agent function tests
├── integration/                 # Integration tests
│   ├── test_graph_update.py    # Agent graph integration
│   └── test_state_save.py      # State persistence tests
└── fixtures/                    # Test data and fixtures
```

## Test Categories

### Unit Tests (`tests/unit/`)
- **test_api/**: Tests for API functions (user, cart, order, etc.)
- **test_db/**: Tests for database operations and models
- **test_utils/**: Tests for utility functions (coreutil, response, etc.)
- **test_agents/**: Tests for agent-specific functionality

### Integration Tests (`tests/integration/`)
- **test_graph_update.py**: Tests agent graph functionality
- **test_state_save.py**: Tests state persistence across components

### Fixtures (`tests/fixtures/`)
- Test data files
- Mock objects
- Sample responses

## Running Tests

### Run All Tests
```bash
cd src
python tests/run_tests.py
```

### Run Specific Test Categories
```bash
# Run only unit tests
python -m pytest tests/unit/

# Run only integration tests
python -m pytest tests/integration/

# Run specific test file
python tests/unit/test_utils/test_float_conversion.py
```

### Run Individual Tests
```bash
# API tests
python tests/unit/test_api/test_update_user.py

# Database tests
python tests/unit/test_db/test_phone_lookup.py

# Utility tests
python tests/unit/test_utils/test_float_conversion.py

# Integration tests
python tests/integration/test_graph_update.py
```

## Test Guidelines

1. **Naming Convention**: All test files should start with `test_`
2. **Import Paths**: Tests use relative imports to access source code
3. **Test Functions**: Should be descriptive and test one specific functionality
4. **Assertions**: Use clear assertions with meaningful error messages
5. **Documentation**: Each test should have a docstring explaining its purpose

## Adding New Tests

1. **Unit Tests**: Place in appropriate `tests/unit/` subdirectory
2. **Integration Tests**: Place in `tests/integration/`
3. **Fixtures**: Place test data in `tests/fixtures/`
4. **Update Runner**: Add new tests to `tests/run_tests.py`

## Test Dependencies

Tests may require:
- Database connection (for DB tests)
- API credentials (for API tests)
- Environment variables (for integration tests)

Ensure proper setup before running tests. 