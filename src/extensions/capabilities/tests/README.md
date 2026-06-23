# Capabilities Tests

Self-contained tests for the capabilities package.

## Running Tests

From the project root:

```bash
python src/capabilities/tests/test_noop_adapter.py
```

Or using pytest (if available):

```bash
pytest src/capabilities/tests/
```

## Test Structure

- `test_noop_adapter.py` - Tests for the noop adapter and runner integration

## Test Coverage

1. **Direct Adapter Test** - Validates adapter interface and behavior
2. **Runner Integration** - Tests runner routing to adapter and fact merging
3. **Missing Adapter Handling** - Tests graceful handling when adapter not registered
4. **Passthrough Behavior** - Tests runner passthrough when no capability active

## Dependencies

Tests are self-contained and do not require:
- External services (Redis, HTTP APIs)
- Core orchestration layer
- Luma services

Tests only require the capabilities package itself.

