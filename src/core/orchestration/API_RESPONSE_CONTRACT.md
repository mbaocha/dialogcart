# Core API Response Contract

## Canonical API Response Shape

The `handle_message()` function returns a dictionary that MUST conform to this contract at the API boundary.

### Response Structure

```python
{
    # REQUIRED: Top-level success flag
    "success": bool,
    
    # REQUIRED: Control-plane state (EXTERNAL - exposed in API)
    "outcome": {
        # Control-plane fields (durable state)
        "status": str,  # "READY" | "NEEDS_CLARIFICATION" | "AWAITING_CAPABILITY" | "AWAITING_CONFIRMATION"
        "intent_name": str,
        "stage": Optional[str],
        "action": Optional[str],
        "slots": Dict[str, Any],
        "missing_slots": List[str],
        "facts": Dict[str, Any],  # Always present, never None
        "plan": {
            "status": str,
            "stage": Optional[str],
            "action": Optional[str],
            "intent_name": str,
            ...
        },
        "active_capability": Optional[str],  # Present when status == "AWAITING_CAPABILITY"
        "awaiting": Optional[str],
        "blocked_actions": List[str],
        "allowed_actions": List[str],
        
        # DEPRECATED: Presentation fields in outcome (should be at top level)
        # These are promoted to top level by normalize_response() but may remain in outcome for backward compatibility
        "text": Optional[str],  # DEPRECATED: Use top-level "text"
        "ui_actions": Optional[List[Dict]],  # DEPRECATED: Use top-level "ui_actions"
        "ui_hint": Optional[str],  # DEPRECATED: Use top-level "ui_hint"
    },
    
    # PRESENTATION FIELDS (top-level, guaranteed when present in outcome)
    "text": Optional[str],  # Rendered user-facing text (promoted from outcome.text)
    "ui_actions": Optional[List[Dict]],  # Structured UI actions (promoted from outcome.ui_actions)
    "ui_hint": Optional[str],  # Template hint (promoted from outcome.ui_hint)
    
    # ERROR FIELDS (only present when success == False)
    "error": Optional[str],
    "message": Optional[str],
    
    # INTERNAL FIELDS (not exposed in API, for orchestration only)
    "_merged_luma_response": Optional[Dict],  # Internal: merged Luma response for session building
    "_decision": Optional[Dict],  # Internal: decision object for plan_message access
}
```

### Field Classification

#### Control-Plane Fields (in `outcome`)
- **Purpose**: Durable state that persists across turns
- **Location**: `result["outcome"]`
- **Examples**: `status`, `active_capability`, `intent_name`, `slots`, `missing_slots`, `facts`, `plan`
- **Persistence**: These fields are saved to session and loaded on next turn

#### Presentation Fields (top-level)
- **Purpose**: User-facing UI elements (text, buttons, links)
- **Location**: `result["text"]`, `result["ui_actions"]`, `result["ui_hint"]`
- **Examples**: Rendered text, payment link buttons, clarification prompts
- **Persistence**: NOT persisted - regenerated each turn
- **Promotion**: Automatically promoted from `outcome` to top level by `normalize_response()`

#### Internal Fields (not exposed)
- **Purpose**: Orchestration-only data, not part of API contract
- **Location**: `result["_merged_luma_response"]`, `result["_decision"]`
- **Examples**: Merged Luma responses, decision objects
- **Persistence**: NOT persisted, NOT exposed in API

### Decision: `outcome` is EXTERNAL

**Rationale:**
- `outcome` is explicitly included in `MessageResponse` schema (line 62 in `message.py`)
- Tests assert on `result["outcome"]["status"]`, `result["outcome"]["active_capability"]`, etc.
- API endpoint preserves `outcome` in response (lines 257-274 in `message.py`)
- `outcome` contains control-plane state that clients need to make decisions

**Conclusion**: `outcome` is **EXTERNAL** - it is part of the public API contract.

## Guaranteed Fields at API Boundary

### Always Present (when `success == True`)
- `success: bool`
- `outcome: dict` (with at least `status`, `intent_name`, `slots`, `missing_slots`, `facts`)

### Conditionally Present
- `text: str` - Present when:
  - Capability renderer produces text (`status == "AWAITING_CAPABILITY"` and `ui_hint` present)
  - Clarification rendering succeeds (`status == "NEEDS_CLARIFICATION"`)
  - Planning outcome includes text (`plan.text` is set)
  
- `ui_actions: List[Dict]` - Present when:
  - Capability renderer produces UI actions (`status == "AWAITING_CAPABILITY"` and `ui_hint` present)
  
- `ui_hint: str` - Present when:
  - Capability renderer is invoked (`status == "AWAITING_CAPABILITY"` and `ui_hint` present)

- `active_capability: str` - Present when:
  - `status == "AWAITING_CAPABILITY"`

- `error: str` - Present when:
  - `success == False`

- `message: str` - Present when:
  - `success == False` OR `text` is present (promoted to `message` in API endpoint)

## All Return Paths in `handle_message()`

### 1. Planning Failure (Line 548)
```python
return {
    "success": False,
    "error": plan.get("error", "planning_failed"),
    "message": plan.get("message", "Planning failed"),
    "plan": plan
}
```
**Violations**: None (error path, minimal contract)

### 2. PATH 1: Capability Rendering Early Return (Line 3332)
```python
normalize_response(result)
return result
```
**Contract**: ✅ Compliant (normalize_response ensures presentation fields are promoted)

### 3. PATH 1: Runner Executed Return (Line 3351)
```python
normalize_response(result)
return result
```
**Contract**: ✅ Compliant

### 4. PATH 1: Other Statuses Return (Line 3418)
```python
normalize_response(result)
return result
```
**Contract**: ✅ Compliant

### 5. PATH 2: Capability Rendering Return (Line 3712)
```python
normalize_response(result)
return result
```
**Contract**: ✅ Compliant

### 6. PATH 2: AWAITING_* Fallback Return (Line 3723)
```python
normalize_response(result)
return result
```
**Contract**: ✅ Compliant

### 7. NEEDS_CLARIFICATION: Outcome Present Return (Line 3808)
```python
normalize_response(result)
return result
```
**Contract**: ✅ Compliant

### 8. NEEDS_CLARIFICATION: Error Return (Line 3810)
```python
return {
    "success": False,
    "error": decision["error"],
    "message": decision.get("message", "An error occurred")
}
```
**Violations**: None (error path, minimal contract)

### 9. NEEDS_CLARIFICATION: Synthesized Clarification Return (Line 4015)
```python
normalize_response(result)
return result
```
**Contract**: ✅ Compliant

### 10. READY Status Return (Line 4075)
```python
normalize_response(result)
return result
```
**Contract**: ✅ Compliant

### 11. Unexpected Status Return (Line 4079)
```python
return {
    "success": False,
    "error": "internal_error",
    "message": f"Unexpected plan status: {plan_status}"
}
```
**Violations**: None (error path, minimal contract)

### 12. Non-Core Intent Return (Line 4096)
```python
return _handle_non_core_intent(effective_response, decision, user_id)
```
**Contract**: ⚠️ **VIOLATION** - `_handle_non_core_intent()` returns:
```python
{
    "success": True,
    "outcome": {
        "status": "NON_CORE_INTENT",
        "intent_name": intent_name,
        "facts": facts,
    }
}
```
**Issue**: Missing `normalize_response()` call, no presentation fields promotion

### 13. Final Return (Line 5007)
```python
return planning_result
```
**Contract**: ⚠️ **VIOLATION** - Returns `planning_result` (different structure) instead of `result` dict

## Response Construction Points

### 1. Initial Result Construction (Line 3019)
```python
result = {
    "success": True,
    "outcome": {
        "intent_name": intent_name,
        "stage": stage,
        "action": action,
        "missing_slots": missing_slots,
        "slots": outcome_slots,
        "status": plan_status,
        ...
    }
}
```
**Contract**: ✅ Compliant (basic structure)

### 2. Capability Outcome Construction (Line 3462)
```python
result = {
    "success": True,
    "outcome": outcome_dict
}
```
**Contract**: ✅ Compliant (outcome_dict is built from decision)

### 3. READY Outcome Construction (Line 4046)
```python
result = {
    "success": True,
    "outcome": {
        "status": "READY",
        "intent_name": intent_name,
        **planning_outcome,
        "facts": facts,
        "plan": plan
    }
}
```
**Contract**: ✅ Compliant

### 4. Planning Result Construction (Line 4926)
```python
planning_result = {
    "intent_name": outcome.get("intent_name", ""),
    "intent": outcome.get("intent_name", ""),
    "stage": stage,
    "action": action,
    "slots": outcome.get("slots", {}),
    "missing_slots": outcome.get("missing_slots", []),
    "status": outcome.get("status"),
    ...
}
```
**Contract**: ⚠️ **VIOLATION** - Different structure (not `{"success": True, "outcome": {...}}`)

## Identified Violations

### Violation 1: Non-Core Intent Return (Line 4096)
**Issue**: `_handle_non_core_intent()` does not call `normalize_response()` before returning
**Impact**: Presentation fields (`text`, `ui_actions`, `ui_hint`) may not be promoted to top level
**Fix**: Call `normalize_response(result)` before return in `_handle_non_core_intent()`

### Violation 2: Final Return Uses `planning_result` (Line 5007)
**Issue**: Returns `planning_result` (different structure) instead of `result` dict
**Impact**: Inconsistent response shape, may break API contract
**Fix**: Ensure final return uses `result` dict with `normalize_response(result)` call

### Violation 3: Missing `normalize_response()` in Error Paths
**Issue**: Some error returns (lines 548, 3810, 4079) don't call `normalize_response()`
**Impact**: Low (error paths don't have presentation fields), but inconsistent
**Fix**: Add `normalize_response(result)` before error returns (idempotent, safe)

## Contract Enforcement

### Current Enforcement
- `normalize_response()` is called before most returns (10/13 paths)
- Presentation fields are promoted from `outcome` to top level
- Control-plane fields remain in `outcome`

### Missing Enforcement
- No runtime assertion that response shape matches contract
- No validation that `outcome` is always present when `success == True`
- No validation that presentation fields are promoted when present in `outcome`

### Recommended Assertions

Add to `normalize_response()` or create `_assert_response_contract()`:

```python
def _assert_response_contract(result: Dict[str, Any]) -> None:
    """Assert that result conforms to API response contract."""
    assert "success" in result, "Response must have 'success' field"
    
    if result.get("success"):
        assert "outcome" in result, "Successful response must have 'outcome' field"
        assert isinstance(result["outcome"], dict), "outcome must be a dict"
        
        outcome = result["outcome"]
        assert "status" in outcome, "outcome must have 'status' field"
        assert "intent_name" in outcome, "outcome must have 'intent_name' field"
        assert "slots" in outcome, "outcome must have 'slots' field"
        assert "missing_slots" in outcome, "outcome must have 'missing_slots' field"
        assert "facts" in outcome, "outcome must have 'facts' field"
        
        # If outcome has presentation fields, they must be at top level
        if outcome.get("text") is not None:
            assert result.get("text") is not None, "If outcome.text exists, result.text must exist"
        if outcome.get("ui_actions") is not None:
            assert result.get("ui_actions") is not None, "If outcome.ui_actions exists, result.ui_actions must exist"
        if outcome.get("ui_hint") is not None:
            assert result.get("ui_hint") is not None, "If outcome.ui_hint exists, result.ui_hint must exist"
    else:
        assert "error" in result, "Failed response must have 'error' field"
```

## Summary

- **Total Return Paths**: 13
- **Compliant Paths**: 10 (with `normalize_response()`)
- **Violations**: 3
  1. Non-core intent return (missing `normalize_response()`)
  2. Final return uses `planning_result` (different structure)
  3. Error paths (missing `normalize_response()`, low priority)

- **Decision**: `outcome` is **EXTERNAL** (part of public API contract)
- **Guaranteed Fields**: `success`, `outcome` (with `status`, `intent_name`, `slots`, `missing_slots`, `facts`)
- **Conditional Fields**: `text`, `ui_actions`, `ui_hint` (promoted from `outcome` when present)



