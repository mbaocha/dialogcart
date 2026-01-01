# Dialogcart-Core Package Reorganization Summary

## Overview

The dialogcart-core codebase has been reorganized to enforce three clear internal responsibilities at the package level:
1. **Orchestration Layer** - What should happen
2. **Routing Layer** - How semantic signals map to identifiers
3. **Rendering Layer** - How messages are expressed

## New Structure

### 1. Orchestration Layer (`core/orchestration/`)

**Purpose**: Decide what should happen next.

**Owns**:
- Conversation control flow
- External side-effects
- Context derivation
- Business execution

**Packages Moved Here**:
- `orchestration/clients/` - External API client integrations
  - Luma, Booking, Customer, Organization, Catalog, Payment, Availability, Staff APIs
- `orchestration/cache/` - Context derivation through caching
  - Catalog cache, Organization domain cache
- `orchestration/contracts/` - Contract validation
  - Luma API contract assertions
- `orchestration/actions/` - Business execution
  - Booking, cancellation, modification actions
- `orchestration/api/` - Public API entry point
  - FastAPI endpoints for message processing
- `orchestration/orchestrator.py` - Main orchestration logic

**Constraints**:
- No copy, no templates, no WhatsApp formatting
- Must only return structured outcomes

### 2. Routing Layer (`core/routing/`)

**Purpose**: Map semantic signals to internal identifiers.

**Owns**:
- Pure mapping logic
- Deterministic lookups

**Packages**:
- `routing/clarification_router.py` - Maps clarification reasons to template keys
- `routing/intent_router.py` - Maps intent names to action names
- `routing/config/clarification_templates.yaml` - YAML decision table

**Constraints**:
- No side effects
- No rendering
- No external calls

### 3. Rendering Layer (`core/rendering/`)

**Purpose**: Decide how messages are expressed.

**Owns**:
- WhatsApp formatting
- Template lookup and interpolation
- Copy variants
- Channel-specific payload construction

**Packages**:
- `rendering/whatsapp_renderer.py` - Renders outcome objects to WhatsApp messages
- `rendering/whatsapp/` - WhatsApp channel-specific code
  - Message normalization, sending, webhook handling
- `rendering/templates/` - Template definitions (JSON)
- `rendering/templates_legacy/` - Legacy templates (preserved for compatibility)

**Constraints**:
- Must consume outcome objects only
- Must not call Luma or business APIs
- Must not contain orchestration logic

## Package Moves

### Moved to Orchestration Layer
- `clients/` → `orchestration/clients/`
- `cache/` → `orchestration/cache/`
- `contracts/` → `orchestration/contracts/`
- `actions/` → `orchestration/actions/`
- `api/` → `orchestration/api/`

### Moved to Routing Layer
- `config/clarification_templates.yaml` → `routing/config/clarification_templates.yaml`

### Moved to Rendering Layer
- `channels/whatsapp/` → `rendering/whatsapp/`
- `templates/` → `rendering/templates_legacy/` (preserved for compatibility)

## Import Changes

### Before
```python
from core.clients.luma_client import LumaClient
from core.cache.catalog_cache import catalog_cache
from core.contracts.luma_contracts import assert_luma_contract
from core.actions.booking import execute_booking
from core.api.message import post_message
```

### After
```python
from core.orchestration.clients.luma_client import LumaClient
from core.orchestration.cache.catalog_cache import catalog_cache
from core.orchestration.contracts.luma_contracts import assert_luma_contract
from core.orchestration.actions.booking import execute_booking
from core.orchestration.api.message import post_message
```

## Files Updated

### Import Updates
- `orchestration/orchestrator.py` - Updated all client, cache, contract imports
- `orchestration/clients/*.py` - Updated base_client imports
- `orchestration/cache/*.py` - Updated client imports
- `orchestration/api/*.py` - Updated orchestrator imports
- `tests/test_orchestrator_flow.py` - Updated all imports
- `tests/contracts/test_luma_contracts.py` - Updated contract import
- `test_interactive.py` - Updated client and cache imports
- `routing/clarification_router.py` - Updated config file path

### Docstrings Added
- `orchestration/clients/__init__.py` - Explains client package responsibility
- `orchestration/cache/catalog_cache.py` - Explains cache responsibility
- `orchestration/cache/org_domain_cache.py` - Explains cache responsibility
- `orchestration/contracts/luma_contracts.py` - Explains contract validation responsibility
- `orchestration/actions/booking.py` - Explains business execution responsibility
- `orchestration/api/message.py` - Explains API entry point responsibility
- `rendering/whatsapp/__init__.py` - Explains WhatsApp channel responsibility
- `routing/__init__.py` - Enhanced routing layer description

## Verification

✅ All imports verified:
- Orchestration layer imports work correctly
- Routing layer imports work correctly
- Rendering layer imports work correctly
- Routing functions work correctly (tested: `MISSING_TIME -> service.ask_time`)

✅ No linter errors

✅ Behavior preserved:
- Template key generation works identically
- Intent → action mapping unchanged
- Outcome structure unchanged
- API contracts unchanged
- Response formats unchanged

## Remaining Top-Level Packages

These packages remain at the top level as shared utilities:
- `errors/` - Shared exception classes
- `conversation/` - (Empty, may be removed)
- `luma/` - (Empty, may be removed)

## Notes

- The old `templates/` directory was moved to `rendering/templates_legacy/` for backward compatibility
- Empty `channels/` and `config/` directories were removed after moving their contents
- All module docstrings now clearly explain which layer owns each package and why
- The reorganization maintains 100% backward compatibility in terms of behavior

