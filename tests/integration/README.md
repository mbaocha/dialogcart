# Validator Integration Tests

This directory contains integration tests for the validator system.

## Files

- `test_validator_integration.py` - Comprehensive pytest integration tests
- `run_validator_integration_test.py` - Simple test runner (no pytest required)

## Running the Tests

### Option 1: Using pytest (Recommended)

```bash
# Install pytest if not already installed
pip install pytest

# Run the integration tests
pytest tests/integration/test_validator_integration.py -v

# Run with more detailed output
pytest tests/integration/test_validator_integration.py -v -s
```

### Option 2: Using the simple test runner

```bash
# Run without pytest
python tests/run_validator_integration_test.py
```

### Option 3: Using the CLI script (Legacy functionality)

```bash
# Run the original validator test functionality
python src/intents/core/run_validator_test.py
```

## Test Coverage

The integration tests cover:

1. **Scenario Loading** - Loading test scenarios from JSONL files
2. **Verb Extraction** - Testing that 'clear' is not extracted when using filtered synonyms
3. **Quantity Extraction** - Extracting numeric quantities from text
4. **Product Extraction** - Extracting product names from text
5. **Validation Workflow** - Complete validation workflow with mocked API responses
6. **Statistics Collection** - Validation statistics and failure tracking
7. **File Output** - Writing detailed results to JSON files

## Key Features

- **No 'clear' contamination**: Tests ensure that 'clear' is not extracted when using modify_cart intent synonyms
- **Mocked API calls**: Tests use mocked Rasa API responses for reliable testing
- **Comprehensive coverage**: Tests cover all major validator functionality
- **Flexible execution**: Can be run with or without pytest

## Expected Results

When running the tests, you should see:

- ✅ All core functionality tests pass
- ✅ No 'clear' verb extraction in modify_cart contexts
- ✅ Proper verb, quantity, and product extraction
- ✅ Successful validation workflow execution

## Troubleshooting

If you encounter import errors:

1. Make sure you're running from the project root directory
2. Ensure the `src` directory is in your Python path
3. Install required dependencies: `pip install -r requirements.txt`

If you encounter API connection errors:

- This is expected in test environments
- The tests use mocked API responses
- Real API testing requires a running Rasa service
