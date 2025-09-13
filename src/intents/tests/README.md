# Validator Integration Tests

This directory contains integration tests for the validator system.

## Files

- `test_validator_integration.py` - Comprehensive pytest integration tests for validator
- `run_validator_integration_test.py` - Simple test runner for validator (no pytest required)
- `test_slot_memory_integration.py` - Comprehensive pytest integration tests for slot memory
- `run_slot_memory_integration_test.py` - Simple test runner for slot memory (no pytest required)
- `test_slot_memory_full_workflow.py` - Full workflow pytest test with 200 scenarios
- `run_slot_memory_full_workflow_test.py` - Full workflow test runner with 200 scenarios
- `modify_cart_100_scenarios.jsonl` - Test scenarios for modify_cart intent
- `modify_cart_200_scenarios.jsonl` - Extended test scenarios
- `test_modify_cart_bulk.py` - Bulk testing functionality

## Running the Tests

### Option 1: Using pytest (Recommended)

```bash
# Install pytest if not already installed
pip install pytest

# Run validator integration tests
pytest src/intents/tests/test_validator_integration.py -v

# Run slot memory integration tests
pytest src/intents/tests/test_slot_memory_integration.py -v

# Run full workflow slot memory tests
pytest src/intents/tests/test_slot_memory_full_workflow.py -v

# Run all integration tests
pytest src/intents/tests/ -v

# Run with more detailed output
pytest src/intents/tests/test_validator_integration.py -v -s
```

### Option 2: Using the simple test runners

```bash
# Run validator tests without pytest
python src/intents/tests/run_validator_integration_test.py

# Run slot memory tests without pytest
python src/intents/tests/run_slot_memory_integration_test.py

# Run full workflow slot memory test with 200 scenarios
python src/intents/tests/run_slot_memory_full_workflow_test.py
```

### Option 3: Using the CLI script (Legacy functionality)

```bash
# Run the original validator test functionality
python src/intents/core/run_validator_test.py
```

## Test Coverage

### Validator Integration Tests
1. **Scenario Loading** - Loading test scenarios from JSONL files
2. **Verb Extraction** - Testing that 'clear' is not extracted when using filtered synonyms
3. **Quantity Extraction** - Extracting numeric quantities from text
4. **Product Extraction** - Extracting product names from text
5. **Validation Workflow** - Complete validation workflow with mocked API responses
6. **Statistics Collection** - Validation statistics and failure tracking
7. **File Output** - Writing detailed results to JSON files

### Slot Memory Integration Tests
1. **Basic Operations** - Slot creation, updates, and retrieval
2. **Cross-Intent Memory** - Products remembered across different intents
3. **Contextual Updates** - Using 'it' references for product updates
4. **Cart Action Memory** - Cart state and action tracking
5. **Shopping List Accumulation** - Building shopping lists across intents
6. **Quantity Extraction** - Various quantity formats and parsing
7. **Conversation Tracking** - Turn counting and intent history
8. **Memory Persistence** - Data persistence across multiple calls
9. **Sender Isolation** - Multiple users with separate memory
10. **Edge Cases** - Error handling and boundary conditions

## Key Features

### Validator Tests
- **No 'clear' contamination**: Tests ensure that 'clear' is not extracted when using modify_cart intent synonyms
- **Mocked API calls**: Tests use mocked Rasa API responses for reliable testing
- **Comprehensive coverage**: Tests cover all major validator functionality

### Slot Memory Tests
- **Cross-intent memory**: Tests memory persistence across different conversation intents
- **Contextual understanding**: Tests ability to understand 'it' references
- **Multi-user support**: Tests isolation between different users
- **Real-world scenarios**: Tests realistic conversation flows

### General
- **Flexible execution**: Can be run with or without pytest
- **Comprehensive coverage**: Tests cover all major functionality

## Expected Results

When running the tests, you should see:

### Validator Tests
- ✅ All core functionality tests pass
- ✅ No 'clear' verb extraction in modify_cart contexts
- ✅ Proper verb, quantity, and product extraction
- ✅ Successful validation workflow execution

### Slot Memory Tests
- ✅ Cross-intent product memory working
- ✅ Contextual updates using 'it' references
- ✅ Cart action state tracking
- ✅ Shopping list accumulation
- ✅ Multi-user memory isolation
- ✅ Conversation turn tracking

## Troubleshooting

If you encounter import errors:

1. Make sure you're running from the project root directory
2. Ensure the `src` directory is in your Python path
3. Install required dependencies: `pip install -r requirements.txt`

If you encounter API connection errors:

- This is expected in test environments
- The tests use mocked API responses
- Real API testing requires a running Rasa service

## File Structure

```
src/intents/tests/
├── README.md                              # This file
├── test_validator_integration.py          # Pytest validator integration tests
├── run_validator_integration_test.py      # Simple validator test runner
├── test_slot_memory_integration.py        # Pytest slot memory integration tests
├── run_slot_memory_integration_test.py    # Simple slot memory test runner
├── modify_cart_100_scenarios.jsonl        # Test scenarios (100 cases)
├── modify_cart_200_scenarios.jsonl        # Test scenarios (200 cases)
└── test_modify_cart_bulk.py               # Bulk testing functionality
```
