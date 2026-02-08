# Core Rendering Layer Analysis

**Status:** READ-ONLY Investigation  
**Date:** 2025-01-27  
**Scope:** Core rendering layer structure, contracts, test expectations, and coupling analysis

---

## 1. Rendering Architecture Overview

### 1.1 File Inventory

The rendering layer consists of the following files:

#### Core Rendering Files
- **`src/core/rendering/__init__.py`** (15 lines)
  - Public API: `RenderSpec`, `render_clarification`, `render`
  - Re-exports from submodules

- **`src/core/rendering/renderer.py`** (75 lines)
  - **Primary responsibility:** High-level rendering orchestration for clarification states
  - **Public entry point:** `render(decision: Dict[str, Any]) -> Optional[str]`
  - **Key behavior:** Detects clarification state, derives reason, renders text, falls back to generic template

- **`src/core/rendering/clarification_renderer.py`** (142 lines)
  - **Primary responsibility:** Template-based clarification text rendering
  - **Public entry point:** `render_clarification(reason: str, slots: Dict[str, Any]) -> RenderSpec`
  - **Key behavior:** Loads YAML templates, validates required fields, performs placeholder substitution
  - **Data structures:**
    - Consumes: `reason` (string), `slots` (dict)
    - Produces: `RenderSpec` (dataclass with `text: str`)

- **`src/core/rendering/mapper/clarification_mapper.py`** (49 lines)
  - **Primary responsibility:** Maps decision state to clarification reason strings
  - **Public entry point:** `derive_clarification_reason(decision: Dict[str, Any]) -> Optional[str]`
  - **Key behavior:** Deterministic mapping from `missing_slots` to reason strings
  - **Mapping rules:**
    - `["time"]` → `"MISSING_TIME"`
    - `["date"]` → `"MISSING_DATE"`
    - `[]` → `"NEEDS_CLARIFICATION"`
    - Multiple/other slots → `"NEEDS_CLARIFICATION"` (generic fallback)

#### Template Files
- **`src/core/rendering/templates/clarifications.yaml`** (15 lines)
  - Template definitions for clarification reasons
  - Structure: `{reason: {template: str, required_fields: List[str]}}`
  - Current templates: `MISSING_TIME`, `MISSING_DATE`, `NEEDS_CLARIFICATION`

- **`src/core/rendering/templates/actions.yaml`** (1 line, empty)
- **`src/core/rendering/templates/terminal.yaml`** (1 line, empty)

### 1.2 Rendering Flow

```
Orchestrator.handle_message()
  ↓
process_luma_response() → decision {plan, facts, intent_name}
  ↓
_inject_rendering_text(result, decision)
  ↓
render(decision)
  ├─ Extract: plan.status, facts.missing_slots, facts.slots
  ├─ Check: is_clarification = (status == "NEEDS_CLARIFICATION" OR len(missing_slots) > 0)
  ├─ derive_clarification_reason(rendering_decision)
  └─ render_clarification(reason, slots) → RenderSpec.text
```

---

## 2. Upstream Interfaces & Contracts

### 2.1 Entry Point: `render(decision: Dict[str, Any])`

**Upstream Layer:** Orchestration (`core.orchestration.orchestrator`)

**Required Fields:**
- `decision["plan"]["status"]` (str): Must be `"NEEDS_CLARIFICATION"` for rendering to occur
- `decision["facts"]["missing_slots"]` (List[str]): List of missing slot names (can be empty `[]`)
- `decision["facts"]["slots"]` (Dict[str, Any]): Slot values for template interpolation

**Optional Fields:**
- `decision["plan"]` (Dict): If missing, defaults to `{}`
- `decision["facts"]` (Dict): If missing, defaults to `{}`

**Implicit Assumptions:**
1. **Status-based rendering:** Rendering only occurs when `plan.status == "NEEDS_CLARIFICATION"` OR `len(missing_slots) > 0`
2. **Missing slots type:** `missing_slots` must be a list (defensive check converts non-list to `[]`)
3. **Slots availability:** Required slot values (e.g., `service` for `MISSING_TIME` template) must exist in `facts.slots`
4. **Template existence:** Template for derived reason must exist in `clarifications.yaml`

**Behavior:**
- Returns `None` if not in clarification state
- Returns `None` if rendering fails (swallows exceptions, falls back to generic template)
- Falls back to `"NEEDS_CLARIFICATION"` generic template if reason derivation fails

### 2.2 Entry Point: `render_clarification(reason: str, slots: Dict[str, Any])`

**Upstream Layer:** `render()` function (internal) or orchestrator (direct calls via `_render_clarification_text`)

**Required Fields:**
- `reason` (str): Must match a key in `clarifications.yaml`
- `slots` (Dict): Must contain all fields listed in template's `required_fields`

**Validation:**
- Raises `KeyError` if template not found
- Raises `ValueError` if required fields missing from slots
- Raises `ValueError` if placeholder in template not found in slots

**No Fallback:** This function fails fast (no fallback logic)

### 2.3 Entry Point: `derive_clarification_reason(decision: Dict[str, Any])`

**Upstream Layer:** `render()` function

**Required Fields:**
- `decision["status"]` (str): Must be `"NEEDS_CLARIFICATION"` (returns `None` otherwise)
- `decision["missing_slots"]` (List[str]): Used for mapping

**Implicit Assumptions:**
1. **Status check:** Only processes `NEEDS_CLARIFICATION` status
2. **Missing slots format:** Expects list, handles non-list by converting to `[]`
3. **Mapping completeness:** Only maps `["time"]` and `["date"]` to specific reasons; all other cases → generic

**Behavior:**
- Returns `None` if status is not `NEEDS_CLARIFICATION`
- Returns generic `"NEEDS_CLARIFICATION"` for multiple missing slots or unknown slots

### 2.4 Decision Object Structure (from `process_luma_response`)

The decision object passed to rendering has the following structure:

```python
decision = {
    "intent_name": str,           # Intent name (e.g., "CREATE_APPOINTMENT")
    "plan": {
        "status": str,            # "READY" | "NEEDS_CLARIFICATION" | "AWAITING_CONFIRMATION"
        "stage": str,             # "AVAILABILITY" | "CONFIRM" | etc.
        "action": str,             # "SEARCH_AVAILABILITY" | "CONFIRM_APPOINTMENT" | etc.
        "missing_slots": List[str], # From plan (may be duplicated in facts)
        "executable_actions": List[str],
        "allowed_actions": List[str],
        "blocked_actions": List[str],
        "awaiting": Optional[str]  # For AWAITING_CONFIRMATION
    },
    "facts": {
        "slots": Dict[str, Any],   # Collected slot values
        "missing_slots": List[str], # Missing required slots (authoritative)
        "context": Dict[str, Any]   # Additional context
    },
    "booking": Dict[str, Any],     # Optional: booking details
    "outcome": Dict[str, Any]      # Optional: pre-built outcome (for NEEDS_CLARIFICATION)
}
```

**Key Architectural Invariants (from orchestrator):**
- `missing_slots` is computed exactly once per turn in session merge
- `missing_slots = []` is VALID and means all required slots are satisfied
- `missing_slots` must be a list (never `None`)

---

## 3. Downstream Expectations

### 3.1 Output Guarantees

**Function: `render(decision)`**
- **Output shape:** `Optional[str]` (rendered text or `None`)
- **Stability:** Deterministic for same input (no randomness)
- **Context sensitivity:** None (pure function, no session state)
- **Branching:**
  - Returns `None` if `plan.status != "NEEDS_CLARIFICATION"` AND `len(missing_slots) == 0`
  - Returns rendered text if clarification detected
  - Falls back to generic template on errors

**Function: `render_clarification(reason, slots)`**
- **Output shape:** `RenderSpec` (dataclass with `text: str`)
- **Stability:** Deterministic (same reason + slots → same text)
- **Context sensitivity:** None
- **Branching:** None (fails fast on errors)

**Function: `derive_clarification_reason(decision)`**
- **Output shape:** `Optional[str]` (reason string or `None`)
- **Stability:** Deterministic
- **Context sensitivity:** None
- **Branching:**
  - Returns `None` if `status != "NEEDS_CLARIFICATION"`
  - Maps `missing_slots` to specific reasons or generic fallback

### 3.2 Integration Points

**Orchestrator Integration:**
- `_inject_rendering_text(result, decision)` calls `render(decision)` and injects result as `result["text"]`
- `_render_clarification_text(decision, slots)` calls `derive_clarification_reason` + `render_clarification` directly
- Rendering is called at multiple points:
  - After NEEDS_CLARIFICATION outcome construction (line 2462, 2495, 2577)
  - After synthesized clarification outcome (line 2775)
  - After AWAITING_CONFIRMATION (line 2495, conditional on missing_slots)

**Response Structure:**
- Rendered text is injected at top-level: `result["text"] = rendered_text`
- Also stored in outcome: `result["outcome"]["rendered_text"] = rendered_text` (for NEEDS_CLARIFICATION)

---

## 4. Test Coverage Mapping

### 4.1 Direct Rendering Tests

#### `test_clarification_rendering.py`
- **What it validates:** Template rendering correctness
- **Assertion type:** Exact structure (expects specific text output)
- **Test cases:**
  - `test_render_clarification_for_missing_time`: Validates `MISSING_TIME` template with `service` slot

#### `test_clarification_mapper.py`
- **What it validates:** Reason derivation logic
- **Assertion type:** Exact mapping (expects specific reason strings)
- **Test cases:**
  - Status checks (returns `None` for non-NEEDS_CLARIFICATION)
  - Slot mapping (`["time"]` → `"MISSING_TIME"`, `["date"]` → `"MISSING_DATE"`)
  - Edge cases (missing field, non-list, multiple slots, other slots)

#### `test_missing_template_error.py`
- **What it validates:** Error handling for missing templates/fields
- **Assertion type:** Exception type and message content
- **Test cases:**
  - Missing template raises `KeyError`
  - Missing required fields raises `ValueError`

### 4.2 E2E Integration Tests

#### `test_conversation_rendering_e2e.py`
- **What it validates:** Rendering in multi-turn conversation flows
- **Assertion type:** Semantic validation (not exact text matching)
- **Test structure:** YAML scenarios (`conversation_rendering.yaml`)
- **Validation logic:**
  - `_assert_rendering_clarification`: Checks text presence and semantic content (mentions missing slots OR generic phrases)
  - `_assert_rendering_terminal`: Checks for confirmation-related words
  - `_assert_rendering_absent`: Validates text is absent/empty for non-clarification states
- **Test scenarios:**
  - `book_service_with_missing_time`: Multi-turn flow with clarification → resolution
  - `generic_clarification_fallback`: Multiple missing slots → generic clarification

**Key Test Expectations:**
- Text must be present for `NEEDS_CLARIFICATION` states
- Text must mention missing slots OR contain generic clarification phrases
- Text must be absent/empty for `READY` and `AWAITING_CONFIRMATION` (unless clarification needed)

### 4.3 Test Dependencies on Upstream Behavior

**Tests that encode policy decisions:**
- E2E tests assume `missing_slots` is correctly computed by session merge
- Tests assume `plan.status` accurately reflects clarification needs
- Tests assume slots are available for template interpolation (e.g., `service` for `MISSING_TIME`)

**Tests that would fail on upstream changes:**
- If `missing_slots` format changes (e.g., becomes dict instead of list)
- If `plan.status` values change (e.g., `"CLARIFY"` instead of `"NEEDS_CLARIFICATION"`)
- If slot names change (e.g., `service_id` instead of `service`)

---

## 5. Coupling & Risk Analysis

### 5.1 Duplication of Logic

**Status Detection Logic:**
- Rendering layer checks: `plan_status == "NEEDS_CLARIFICATION" OR len(missing_slots) > 0`
- This duplicates logic from planning layer (which already sets `status` based on missing slots)
- **Risk:** If planning logic changes, rendering may not align

**Missing Slots Handling:**
- Mapper converts non-list `missing_slots` to `[]` (defensive)
- Orchestrator also has defensive checks for `missing_slots` type
- **Risk:** Multiple normalization points could diverge

### 5.2 Business Rules in Rendering

**Template Selection Logic:**
- Mapper encodes business rule: "only `time` and `date` get specific templates, everything else is generic"
- This is a policy decision that should potentially be configurable
- **Risk:** Adding new slot types (e.g., `duration`, `location`) requires code changes

**Required Fields Validation:**
- Templates define `required_fields` (e.g., `service` for `MISSING_TIME`)
- This assumes certain slots are always available when asking for others
- **Risk:** If slot extraction changes, templates may fail validation

### 5.3 Assumptions About Upstream Behavior

**Plan Status Assumptions:**
- Rendering assumes `plan.status` is authoritative for clarification state
- Also checks `missing_slots` length as fallback
- **Risk:** If status and missing_slots diverge, rendering behavior is ambiguous

**Slot Availability Assumptions:**
- Templates assume slots like `service` are available when asking for `time`
- This assumes partial slot filling (some slots present, others missing)
- **Risk:** If all slots are missing, templates may fail (though generic template handles this)

**Intent Durability:**
- Rendering does not check intent (only status and missing_slots)
- Assumes intent is preserved across clarification turns (handled by orchestrator)
- **Risk:** If intent is cleared during clarification, rendering may still work but context is lost

### 5.4 Regression Risk Hotspots

**High Risk Areas:**

1. **Missing Slots Format Changes**
   - **Location:** `renderer.py:37`, `clarification_mapper.py:33`
   - **Risk:** If `missing_slots` becomes dict or changes structure, defensive checks may not catch all cases
   - **Impact:** Rendering may fail silently (returns `None`) or raise exceptions

2. **Template Field Dependencies**
   - **Location:** `clarification_renderer.py:110-125`
   - **Risk:** If slot names change (e.g., `service` → `service_id`), templates break
   - **Impact:** `ValueError` on missing required fields, rendering fails

3. **Status Value Changes**
   - **Location:** `renderer.py:40-42`, `clarification_mapper.py:29`
   - **Risk:** If planning layer changes status strings, rendering may not trigger
   - **Impact:** Clarification text not rendered when it should be

4. **Dual Clarification Detection**
   - **Location:** `renderer.py:40-43`
   - **Risk:** Logic checks both `plan_status` and `missing_slots` length, which could diverge
   - **Impact:** Inconsistent rendering behavior

5. **Exception Swallowing**
   - **Location:** `renderer.py:67-74`
   - **Risk:** Exceptions are caught and generic template used, masking real errors
   - **Impact:** Template errors may go unnoticed in production

**Medium Risk Areas:**

1. **Template Cache**
   - **Location:** `clarification_renderer.py:20, 36-39`
   - **Risk:** Templates loaded once, changes require restart
   - **Impact:** Template updates not reflected without restart

2. **Generic Fallback Logic**
   - **Location:** `renderer.py:58, 69-71`
   - **Risk:** Multiple fallback layers (reason → generic reason → generic template)
   - **Impact:** Hard to debug which fallback was used

3. **Slot Extraction for Rendering**
   - **Location:** `orchestrator.py:2561-2563, 2759-2761`
   - **Risk:** Multiple code paths extract slots from different locations (`facts.slots` vs `outcome.slots`)
   - **Impact:** Inconsistent slot availability across rendering calls

### 5.5 Areas Where Small Upstream Changes Cause Widespread Test Failures

**Scenario 1: Missing Slots Format Change**
- If `missing_slots` becomes `{"required": [...], "optional": [...]}` instead of `List[str]`
- **Failures:** All mapper tests, E2E tests that check `missing_slots`
- **Files affected:** `test_clarification_mapper.py`, `test_conversation_rendering_e2e.py`

**Scenario 2: Status Value Rename**
- If `"NEEDS_CLARIFICATION"` becomes `"REQUIRES_CLARIFICATION"`
- **Failures:** All rendering tests, E2E tests
- **Files affected:** All rendering tests, orchestrator integration

**Scenario 3: Slot Name Changes**
- If `service` slot becomes `service_id` or `service_name`
- **Failures:** Template rendering tests, E2E tests using `MISSING_TIME`/`MISSING_DATE`
- **Files affected:** `test_clarification_rendering.py`, `test_conversation_rendering_e2e.py`

**Scenario 4: Template Structure Changes**
- If YAML structure changes (e.g., `required_fields` becomes `required_slots`)
- **Failures:** All template rendering tests
- **Files affected:** `test_clarification_rendering.py`, `test_missing_template_error.py`

---

## 6. Summary

### 6.1 Architecture Strengths

- **Separation of concerns:** Rendering is isolated from planning/execution
- **Deterministic behavior:** No randomness or context sensitivity
- **Template-based:** Easy to modify text without code changes
- **Fail-fast validation:** Clear errors for missing templates/fields

### 6.2 Architecture Weaknesses

- **Tight coupling to upstream structure:** Depends on specific field names and formats
- **Business rules in rendering:** Template selection logic encodes policy
- **Exception swallowing:** Errors may be masked by fallback logic
- **Dual clarification detection:** Checks both status and missing_slots, potential for divergence

### 6.3 Recommendations for Future Analysis

1. **Contract Definition:** Define explicit contracts for `decision` object structure
2. **Type Safety:** Consider using TypedDict or dataclasses for decision structure
3. **Configuration:** Move template selection rules to configuration
4. **Error Handling:** Add structured logging for fallback usage
5. **Testing:** Add property-based tests for edge cases (empty slots, malformed decisions)

---

**End of Analysis**


