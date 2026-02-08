# Dialogcart-Core Package Responsibility Analysis

## Overview

This document classifies all top-level packages in `dialogcart-core` according to the three-layer responsibility model:
1. **Orchestration** - Control flow, context derivation, external calls, execution
2. **Routing** - Pure semantic signal → identifier mapping
3. **Rendering** - WhatsApp UX, templates, message formatting

## Classification Rules

- **Orchestration**: Decides what happens next, calls external systems, performs side effects
- **Routing**: Maps strings/enums/reasons to internal identifiers with no side effects
- **Rendering**: Formats or renders user-facing messages

---

## Top-Level Packages

### ✅ Orchestration Layer

#### `orchestration/`
**Responsibility**: Orchestration  
**Justification**: Main orchestration package containing control flow, decision making, and external system integration.

**Sub-packages**:
- `orchestration/orchestrator.py` - Core orchestration logic (control flow, decision making)
- `orchestration/clients/` - External API client integrations (Luma, Booking, Customer, etc.)
- `orchestration/cache/` - Context derivation through caching (catalog, org domain)
- `orchestration/contracts/` - Contract validation (Luma API response validation)
- `orchestration/actions/` - Business execution (booking, cancellation, modification)
- `orchestration/api/` - Public API entry point (FastAPI endpoints)

**Status**: ✅ Well-organized, all sub-packages correctly belong to orchestration layer.

---

#### `app.py`
**Responsibility**: Orchestration  
**Justification**: Legacy entry point wrapper that calls `orchestrator.handle_message()`. Acts as application bootstrap and environment setup.

**Status**: ✅ Correctly classified, though it's a thin wrapper around orchestration.

---

### ✅ Routing Layer

#### `routing/`
**Responsibility**: Routing  
**Justification**: Pure mapping logic with no side effects. Maps semantic signals (clarification reasons, intent names) to internal identifiers (template keys, action names).

**Sub-packages**:
- `routing/clarification_router.py` - Maps `clarification_reason` → `template_key`
- `routing/intent_router.py` - Maps `intent_name` → `action_name`
- `routing/config/clarification_templates.yaml` - YAML decision table for clarification routing

**Status**: ✅ Well-organized, pure mapping logic with no side effects, no rendering, no external calls.

---

### ✅ Rendering Layer

#### `rendering/`
**Responsibility**: Rendering  
**Justification**: Handles WhatsApp message formatting, template lookup, variable interpolation, and channel-specific payload construction.

**Sub-packages**:
- `rendering/whatsapp_renderer.py` - Renders outcome objects to WhatsApp messages
- `rendering/whatsapp/` - WhatsApp channel-specific code (normalizer, sender, webhook)
- `rendering/templates/` - Template definitions (JSON)
- `rendering/templates_legacy/` - Legacy templates (preserved for compatibility)

**Status**: ✅ Well-organized, all rendering concerns properly isolated.

**Note**: `rendering/templates_legacy/registry.py` contains a mapping function (`get_template_for_reason`) that could be considered routing logic, but it's legacy code preserved for compatibility.

---

### ⚠️ Shared Utilities (Not Layer-Specific)

#### `errors/` (deprecated/removed)
**Responsibility**: N/A (moved to orchestration layer)  
**Justification**: Exception classes have been moved to `orchestration/errors.py` as they are orchestration-layer specific.

**Previous Location**: `core/errors/exceptions.py`  
**Current Location**: `core/orchestration/errors.py`

**Status**: ✅ Moved to orchestration layer. The `errors/` directory is now empty and can be removed.

---

### ❓ Unclear/Empty Packages

#### `conversation/`
**Responsibility**: Unclear  
**Justification**: Empty package with only `__init__.py`. No code, no clear purpose.

**Status**: ⚠️ **Risk**: Empty package with no clear responsibility. Could be:
- Placeholder for future conversation state management (would be Orchestration)
- Legacy package that should be removed
- Misplaced code that should be elsewhere

**Recommendation**: Remove if unused, or document intended purpose if it's a placeholder.

---

#### `luma/`
**Responsibility**: Unclear  
**Justification**: Empty package with only `__init__.py`. No code, no clear purpose.

**Status**: ⚠️ **Risk**: Empty package with no clear responsibility. Could be:
- Placeholder for Luma-specific utilities (would be Orchestration if it calls Luma)
- Legacy package that should be removed
- Confusion with `orchestration/clients/luma_client.py`

**Recommendation**: Remove if unused, or document intended purpose if it's a placeholder.

---

### 📝 Test Infrastructure (Not Layer-Specific)

#### `tests/`
**Responsibility**: Test Infrastructure  
**Justification**: Contains test files for all layers. Not part of the three-layer model.

**Sub-packages**:
- `tests/test_orchestrator_flow.py` - Tests orchestration layer
- `tests/contracts/test_luma_contracts.py` - Tests orchestration contracts
- `tests/routing/` - Empty, placeholder for routing tests
- `tests/integration/` - Empty, placeholder for integration tests

**Status**: ✅ Correctly placed as test infrastructure.

---

#### `test_interactive.py`, `test_orchestrator_e2e.py`, `TESTING.md`
**Responsibility**: Test Infrastructure  
**Justification**: Test scripts and documentation. Not part of the three-layer model.

**Status**: ✅ Correctly placed as test infrastructure.

---

### 📄 Documentation (Not Layer-Specific)

#### `REFACTORING_SUMMARY.md`, `REORGANIZATION_SUMMARY.md`
**Responsibility**: Documentation  
**Justification**: Documentation files. Not part of the three-layer model.

**Status**: ✅ Correctly placed as documentation.

---

## Summary

### ✅ Well-Classified Packages
- `orchestration/` - All sub-packages correctly belong to orchestration
- `routing/` - Pure mapping logic, correctly isolated
- `rendering/` - All rendering concerns properly isolated
- `app.py` - Entry point correctly calls orchestration

### ⚠️ Potential Issues

1. **Empty Packages**:
   - `conversation/` - Empty, unclear purpose
   - `luma/` - Empty, unclear purpose
   - **Risk**: Could lead to confusion about where code belongs

2. **Legacy Code**:
   - `rendering/templates_legacy/registry.py` - Contains routing-like logic (`get_template_for_reason`)
   - **Risk**: Mixing responsibilities in legacy code could confuse future developers

3. **Shared Utilities**:
   - `errors/` - Correctly placed as shared utility, but consider organizing if more shared code emerges

### ✅ Boundary Integrity

**Orchestration → Routing**: ✅ Clean
- Orchestration calls routing functions (`get_template_key`, `get_action_name`)
- No routing code in orchestration

**Orchestration → Rendering**: ✅ Clean
- Orchestration returns structured outcomes
- Rendering consumes outcomes (not yet integrated, but structure is correct)

**Routing → Rendering**: ✅ Clean
- Routing returns identifiers (template keys)
- Rendering uses identifiers to look up templates
- No direct coupling

**Rendering → Orchestration**: ✅ Clean
- Rendering does not call orchestration
- Rendering does not call external APIs

### 📊 Statistics

- **Orchestration packages**: 2 (`orchestration/`, `app.py`)
- **Routing packages**: 1 (`routing/`)
- **Rendering packages**: 1 (`rendering/`)
- **Shared utilities**: 1 (`errors/`)
- **Unclear/empty**: 2 (`conversation/`, `luma/`)
- **Test infrastructure**: 1 (`tests/` + test files)
- **Documentation**: 2 files

### ✅ Overall Assessment

**Status**: ✅ **Well-organized with minor cleanup needed**

The codebase successfully respects the three-layer responsibility model:
- Clear separation between orchestration, routing, and rendering
- No boundary leakage between layers
- Shared utilities properly isolated
- Minor issues with empty packages that should be addressed

**Recommendations**:
1. Remove or document `conversation/` and `luma/` packages
2. Consider deprecating/removing `rendering/templates_legacy/` once migration is complete
3. Monitor `errors/` package - if it grows, consider organizing shared utilities better

