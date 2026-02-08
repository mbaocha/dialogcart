# Dialogcart-Core Tests Directory Analysis

## Current Test Structure

```
tests/
├── __init__.py
├── test_orchestrator_flow.py          # ✅ Orchestration Layer
├── contracts/
│   ├── __init__.py
│   └── test_luma_contracts.py         # ✅ Orchestration Layer (contracts)
├── routing/
│   └── __init__.py                     # ❌ Empty - No routing tests
└── integration/
    └── __init__.py                     # ❌ Empty - No integration tests
```

Also at top level:
- `test_orchestrator_e2e.py` - ✅ Orchestration Layer (E2E tests)
- `test_interactive.py` - ✅ Orchestration Layer (interactive/manual tests)

## Test Classification by Layer

### ✅ Orchestration Layer Tests

#### `tests/test_orchestrator_flow.py`
**Layer**: Orchestration  
**What it tests**:
- `handle_message()` orchestrator function
- Resolved booking flow (calls booking client)
- Partial booking flow (clarification with template_key)
- Contract violation handling
- Luma upstream error handling
- Success=false handling
- Unsupported intent handling

**Imports**:
```python
from core.orchestration.orchestrator import handle_message
from core.orchestration.clients.luma_client import LumaClient
from core.orchestration.clients.booking_client import BookingClient
from core.orchestration.clients.customer_client import CustomerClient
from core.orchestration.clients.catalog_client import CatalogClient
```

**Test Functions**:
- `test_resolved_flow_calls_booking_client()` - Tests full booking creation flow
- `test_partial_flow_returns_template_key()` - Tests clarification flow
- `test_contract_violation_raises_and_handled()` - Tests contract validation
- `test_luma_error_handled()` - Tests error handling
- `test_success_false_returns_error()` - Tests Luma error responses
- `test_unsupported_intent_returns_error()` - Tests unsupported intent handling

---

#### `tests/contracts/test_luma_contracts.py`
**Layer**: Orchestration (specifically `orchestration/contracts/`)  
**What it tests**:
- `assert_luma_contract()` function
- Contract validation rules
- Success=true requires intent.name
- needs_clarification=true requires clarification_reason
- RESOLVED state validation
- Valid resolved and partial bookings

**Imports**:
```python
from core.orchestration.contracts.luma_contracts import assert_luma_contract
from core.orchestration.errors import ContractViolation
```

**Test Functions**:
- `test_success_requires_intent_name()` - Validates intent.name requirement
- `test_needs_clarification_false_requires_resolved()` - Validates RESOLVED state
- `test_needs_clarification_true_requires_reason()` - Validates clarification_reason
- `test_resolved_requires_datetime_range_start()` - Validates datetime_range structure
- `test_valid_resolved_booking()` - Valid valid case
- `test_valid_partial_booking()` - Valid clarification case

**Note**: One test (`test_needs_clarification_false_requires_resolved`) appears to be testing old API format (expects `booking_state=RESOLVED`). The current API doesn't use `booking_state` field.

---

#### `test_orchestrator_e2e.py` (top level)
**Layer**: Orchestration  
**What it tests**:
- End-to-end orchestrator tests with real API calls
- Integration with Luma API
- Integration with business APIs

**Imports**:
```python
from core.orchestration.orchestrator import handle_message
```

**Status**: E2E test script, runs against real services.

---

#### `test_interactive.py` (top level)
**Layer**: Orchestration  
**What it tests**:
- Interactive/manual testing of orchestrator
- Helper functions for testing

**Imports**:
```python
from core.orchestration.clients.catalog_client import CatalogClient
from core.orchestration.cache.catalog_cache import catalog_cache
from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.cache.org_domain_cache import org_domain_cache
```

**Status**: Manual/interactive test script.

---

### ❌ Routing Layer Tests

#### `tests/routing/`
**Status**: **EMPTY** - No tests exist  
**What should be tested**:
- `get_template_key()` function in `routing/clarification_router.py`
  - Mapping clarification_reason to template_key
  - Domain substitution in template keys
  - Fallback behavior for unknown reasons
  - Template pattern validation
- `get_action_name()` function in `routing/intent_router.py`
  - Mapping intent_name to action_name
  - Handling unsupported intents (returns None)
- Config file loading (`routing/config/clarification_templates.yaml`)
  - Valid YAML loading
  - Invalid YAML handling
  - Missing config file handling
  - Missing {domain} placeholder warnings

**Recommended test file**: `tests/routing/test_clarification_router.py`, `tests/routing/test_intent_router.py`

---

### ❌ Rendering Layer Tests

#### No rendering tests exist
**Status**: **MISSING** - No tests exist for rendering layer  
**What should be tested**:
- `render_outcome_to_whatsapp()` function in `rendering/whatsapp_renderer.py`
  - CLARIFY outcome rendering
  - BOOKING_CREATED outcome rendering
  - BOOKING_CANCELLED outcome rendering
  - Template lookup by template_key
  - Variable interpolation ({{variable}} syntax)
  - Required fields validation
  - Missing template handling
  - Invalid outcome type handling
- Template registry loading
  - JSON template file loading
  - Invalid JSON handling
  - Missing template file handling
- WhatsApp channel code (`rendering/whatsapp/`)
  - Normalizer functions
  - Sender functions
  - Webhook handling

**Recommended test file**: `tests/rendering/test_whatsapp_renderer.py`, `tests/rendering/test_templates.py`

---

## Summary

### Current Test Coverage

| Layer | Test Files | Status |
|-------|-----------|--------|
| **Orchestration** | 4 files | ✅ Well tested |
| **Routing** | 0 files | ❌ **No tests** |
| **Rendering** | 0 files | ❌ **No tests** |

### Test Count by Layer

- **Orchestration**: 12+ test functions across 4 test files
- **Routing**: 0 test functions
- **Rendering**: 0 test functions

## Recommendations

### 1. Organize Tests by Layer

**Current structure** (mixed):
```
tests/
├── test_orchestrator_flow.py          # Orchestration
├── contracts/                         # Orchestration (sub-package)
├── routing/                           # Empty
└── integration/                       # Empty
```

**Recommended structure** (layer-based):
```
tests/
├── orchestration/
│   ├── __init__.py
│   ├── test_orchestrator_flow.py
│   ├── contracts/
│   │   ├── __init__.py
│   │   └── test_luma_contracts.py
│   ├── clients/
│   │   └── (future client tests)
│   └── cache/
│       └── (future cache tests)
├── routing/
│   ├── __init__.py
│   ├── test_clarification_router.py
│   ├── test_intent_router.py
│   └── test_config_loading.py
├── rendering/
│   ├── __init__.py
│   ├── test_whatsapp_renderer.py
│   ├── test_templates.py
│   └── whatsapp/
│       └── (future WhatsApp channel tests)
└── integration/
    ├── __init__.py
    └── (future E2E tests)
```

### 2. Create Missing Tests

#### Priority 1: Routing Layer Tests

**File**: `tests/routing/test_clarification_router.py`
```python
def test_get_template_key_maps_reason_to_key():
    """Test that clarification reasons map to template keys."""
    
def test_get_template_key_substitutes_domain():
    """Test domain substitution in template keys."""
    
def test_get_template_key_fallback_for_unknown_reason():
    """Test fallback to {domain}.clarify for unknown reasons."""
    
def test_get_template_key_with_invalid_pattern():
    """Test handling of invalid template patterns."""
```

**File**: `tests/routing/test_intent_router.py`
```python
def test_get_action_name_maps_intent_to_action():
    """Test that intent names map to action names."""
    
def test_get_action_name_returns_none_for_unsupported():
    """Test that unsupported intents return None."""
```

#### Priority 2: Rendering Layer Tests

**File**: `tests/rendering/test_whatsapp_renderer.py`
```python
def test_render_clarify_outcome():
    """Test rendering CLARIFY outcome to WhatsApp message."""
    
def test_render_booking_created_outcome():
    """Test rendering BOOKING_CREATED outcome."""
    
def test_render_booking_cancelled_outcome():
    """Test rendering BOOKING_CANCELLED outcome."""
    
def test_render_template_interpolation():
    """Test variable interpolation in templates."""
    
def test_render_required_fields_validation():
    """Test validation of required template fields."""
    
def test_render_missing_template_fallback():
    """Test fallback when template is missing."""
```

### 3. Move Existing Tests

1. Move `test_orchestrator_flow.py` → `tests/orchestration/test_orchestrator_flow.py`
2. Move `tests/contracts/` → `tests/orchestration/contracts/`
3. Keep E2E and interactive tests at top level (or move to `tests/integration/`)

### 4. Update Import Paths

After reorganization, update all test imports:
- `from core.orchestration...` (no change needed)
- `from core.routing...` (no change needed)
- `from core.rendering...` (no change needed)

## Verification

✅ **Verified**: All current tests are for the orchestration layer  
❌ **Missing**: Tests for routing layer  
❌ **Missing**: Tests for rendering layer  

The tests directory structure currently reflects orchestration-only testing, which aligns with the development focus but leaves routing and rendering layers untested.

