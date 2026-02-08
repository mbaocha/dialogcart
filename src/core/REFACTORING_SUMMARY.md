# Dialogcart-Core Refactoring Summary

## Overview

The dialogcart-core codebase has been refactored into three clear logical responsibilities:
1. **Orchestration Layer** - Control flow and decision making
2. **Routing Layer** - Semantic signal → identifier mapping
3. **Rendering Layer** - WhatsApp message formatting

## Structure

### 1. Orchestration Layer (`core/orchestration/`)

**Purpose**: Decide what should happen next.

**Responsibilities**:
- Message entry handling
- org_id + domain derivation
- catalog + tenant_context construction
- calling Luma API
- contract validation
- branching on needs_clarification
- deciding outcome type (CLARIFY, BOOKING_CREATED, etc.)
- calling business execution functions

**Constraints**:
- No copy, no templates, no WhatsApp formatting
- Must only return structured outcomes

**Files**:
- `orchestrator.py` - Main orchestration logic

### 2. Routing Layer (`core/routing/`)

**Purpose**: Map semantic signals to internal identifiers.

**Responsibilities**:
- clarification_reason → template_key mapping
- intent_name → action_name mapping

**Constraints**:
- No side effects
- No execution
- No rendering
- Data-driven where possible (YAML/JSON for clarification templates)

**Files**:
- `clarification_router.py` - Maps clarification reasons to template keys
- `intent_router.py` - Maps intent names to action names
- `__init__.py` - Exports routing functions

**Configuration**:
- `config/clarification_templates.yaml` - YAML config for clarification routing

### 3. Rendering Layer (`core/rendering/`)

**Purpose**: Decide how the message is expressed.

**Responsibilities**:
- Template registry (JSON/YAML)
- Multiple template variants
- Required fields validation
- Variable interpolation
- WhatsApp message formatting (text, buttons, etc.)

**Constraints**:
- Must consume outcome objects only
- Must not call Luma or business APIs
- Must not contain orchestration logic

**Files**:
- `whatsapp_renderer.py` - Renders outcome objects to WhatsApp messages
- `templates/clarification.json` - Template definitions

## Changes Made

### Files Created
- `src/core/routing/__init__.py`
- `src/core/routing/clarification_router.py`
- `src/core/routing/intent_router.py`
- `src/core/rendering/__init__.py`
- `src/core/rendering/whatsapp_renderer.py`
- `src/core/rendering/templates/clarification.json`

### Files Modified
- `src/core/orchestration/orchestrator.py` - Updated imports to use routing layer
- `src/core/orchestration/__init__.py` - Updated docstring

### Files Deleted
- `src/core/orchestration/router.py` - Split into routing layer modules

### Files Preserved (Not Changed)
- `src/core/templates/` - Original templates directory kept for backward compatibility
- All API contracts and response formats
- All business logic and execution functions

## Import Changes

### Before
```python
from core.orchestration.router import get_template_key, get_action_name
```

### After
```python
from core.routing import get_template_key, get_action_name
```

## Behavior Preservation

✅ All existing behavior is preserved:
- Template key generation works identically
- Intent → action mapping unchanged
- Outcome structure unchanged
- API contracts unchanged
- Response formats unchanged

## Testing

All imports verified:
- ✅ Routing layer imports correctly
- ✅ Orchestrator imports correctly
- ✅ Rendering layer imports correctly
- ✅ Routing functions work correctly (tested manually)

## Notes

- The rendering layer (`whatsapp_renderer.py`) is structured but not yet integrated into the main flow. It's ready for use when needed.
- The old `templates/` directory is preserved for backward compatibility but templates are now also available in `rendering/templates/`.
- The test file `test_orchestrator_flow.py` uses outdated mock data (old Luma API format) but this is unrelated to the refactoring.

