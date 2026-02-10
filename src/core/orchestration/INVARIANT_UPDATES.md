# Invariant Updates: Structure-Based Assertions

## Summary
Updated all invariants to assert STRUCTURE, not flow. Removed all assertions that:
- Require specific internal field names (e.g., `outcome.active_capability`)
- Require specific paths through `handle_message()`
- Inspect `outcome` in tests

## Changes Made

### 1. `_plan_turn()` Invariants (lines 397-424)
**Before:** Flow-specific checks about planning phase
**After:** Structure checks that plan doesn't contain presentation fields
- ✅ Checks that `plan_result` and `plan` don't contain `text`, `ui_actions`, `ui_hint`
- ✅ No flow-specific assertions

### 2. `_render_turn()` Invariants (lines 518-534)
**Before:** Detailed mutation checks with reference equality
**After:** Simplified structure checks
- ✅ Checks that plan and session state are not mutated (reference equality)
- ✅ No flow-specific assertions

### 3. `_render_turn()` Capability Rendering (lines 491-500)
**Before:** Only checked `render_output.text` (flow-specific)
**After:** Structure checks for `ui_actions`
- ✅ **NEW:** Asserts `render_output.ui_actions is not None` (can be empty list)
- ✅ **NEW:** Asserts `isinstance(render_output.ui_actions, list)`
- ✅ Still checks `render_output.text` (required for backward compatibility)

### 4. `_build_api_response()` Invariants (lines 656-680)
**Before:** Only checked that plan doesn't contain presentation fields
**After:** Structure checks for verbatim mapping
- ✅ **NEW:** Asserts API response reflects `render.ui_actions` verbatim
- ✅ **NEW:** Asserts `response.ui_actions == render_result.ui_actions` (no transformation)
- ✅ Still checks plan doesn't contain presentation fields

### 5. Removed Flow-Specific Assertions

#### Deleted: `outcome.active_capability` checks (lines 3734-3739, 4161-4166)
**Reason:** Requires specific internal field name (`outcome`)
**Replaced with:** Structure check for `render.ui_actions` (lines 3735-3744)

#### Deleted: Test assertions on `outcome` (test file lines 144-148, 233-236)
**Reason:** Tests must not inspect `outcome` (internal structure)
**Replaced with:** Comments documenting that tests don't inspect `outcome`

### 6. Test File Updates (`test_orchestrator_invariants.py`)

#### Updated: `test_rendered_ui_appears_at_api_boundary_capability()`
**Before:**
- Checked for "payment" in text (flow-specific content check)
- Checked `outcome` exists (internal structure inspection)
- Required `ui_actions` to have at least one action (flow-specific)

**After:**
- ✅ **STRUCTURE CHECK 1:** `ui_actions` must be present (can be empty list)
- ✅ **STRUCTURE CHECK 2:** API response reflects `render.ui_actions` verbatim
- ✅ **STRUCTURE CHECK 3:** Text must be present (structure: string, non-empty)
- ✅ **STRUCTURE CHECK 4:** No `outcome` inspection (comment only)

#### Updated: `test_rendered_ui_appears_at_api_boundary_clarification()`
**Before:**
- Checked `outcome` exists (internal structure inspection)

**After:**
- ✅ **STRUCTURE CHECK:** Text must be present (structure: string, non-empty)
- ✅ **STRUCTURE CHECK:** No `outcome` inspection (comment only)

## Key Invariants (Structure-Based)

### 1. Capability Rendering Structure
```python
# In _render_turn():
assert render_output.ui_actions is not None  # Can be empty list
assert isinstance(render_output.ui_actions, list)
```

### 2. API Response Structure
```python
# In _build_api_response():
if render_result.get("ui_actions") is not None:
    assert response.get("ui_actions") is not None
    assert response.get("ui_actions") == render_result.get("ui_actions")  # Verbatim
```

### 3. Test Structure
```python
# In test files:
# - Assert only on top-level API fields (result.get("text"), result.get("ui_actions"))
# - Never assert on result.get("outcome") or outcome fields
# - Structure checks: type, presence, verbatim mapping
```

## Removed Invariants

1. ❌ `outcome.active_capability` must not be None (required internal field name)
2. ❌ Tests checking `outcome` exists (internal structure inspection)
3. ❌ Content-specific checks (e.g., "payment" in text) (flow-specific)
4. ❌ Path-specific checks (e.g., "PATH 1", "PATH 2") (flow-specific)

## Remaining Invariants (All Structure-Based)

1. ✅ Plan structure must not contain presentation fields
2. ✅ Render structure must produce `ui_actions` as a list (can be empty)
3. ✅ API response structure must reflect `render.ui_actions` verbatim
4. ✅ Rendering phase must not mutate plan/session state
5. ✅ Tests assert only on top-level API fields

All invariants now assert STRUCTURE, not flow.

