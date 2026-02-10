# Return Paths Migration: Route Through `_build_api_response()`

## Objective
Replace all `return result` paths in `handle_message()` to route through `_build_api_response()`.

## Function Signature
```python
def _build_api_response(
    plan: Dict[str, Any],
    render_result: Dict[str, Any],
    decision: Optional[Dict[str, Any]] = None,
    runner_result: Optional[Any] = None,
    effective_response: Optional[Dict[str, Any]] = None,
    error: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
```

## Key Return Paths Identified

### 1. PATH 1: Capability Rendering Early Return (Line ~3786)
**Current:**
```python
return result  # result has outcome with text, ui_actions, etc.
```

**Needs:**
- Extract `populated_plan` → `plan`
- Extract `render_result` (from CapabilityRenderer)
- Call `_build_api_response(plan, render_result, decision, runner_result, effective_response)`

### 2. PATH 1: Runner Executed Return (Line ~3805)
**Current:**
```python
normalize_response(result)
return result
```

**Needs:**
- Extract plan from `result["outcome"]`
- Extract render_result (may be empty if no rendering)
- Call `_build_api_response(plan, render_result, ...)`

### 3. PATH 1: Other Statuses Return (Line ~3872)
**Current:**
```python
normalize_response(result)
return result
```

**Needs:**
- Extract plan from `result["outcome"]`
- Extract render_result (may be empty)
- Call `_build_api_response(plan, render_result, ...)`

### 4. PATH 2: Capability Rendering Return (Line ~4166)
**Current:**
```python
normalize_response(result)
return result
```

**Needs:**
- Extract plan from `result["outcome"]`
- Extract render_result (from CapabilityRenderer)
- Call `_build_api_response(plan, render_result, ...)`

### 5. PATH 2: AWAITING_* Fallback Return (Line ~4177)
**Current:**
```python
normalize_response(result)
return result
```

**Needs:**
- Extract plan from `result["outcome"]`
- Extract render_result (may be empty)
- Call `_build_api_response(plan, render_result, ...)`

### 6. NEEDS_CLARIFICATION: Outcome Present Return (Line ~4262)
**Current:**
```python
normalize_response(result)
return result
```

**Needs:**
- Extract plan from `result["outcome"]`
- Extract render_result (from clarification rendering)
- Call `_build_api_response(plan, render_result, ...)`

### 7. NEEDS_CLARIFICATION: Synthesized Clarification Return (Line ~4469)
**Current:**
```python
normalize_response(result)
return result
```

**Needs:**
- Extract plan from `result["outcome"]`
- Extract render_result (from clarification rendering)
- Call `_build_api_response(plan, render_result, ...)`

### 8. READY Status Return (Line ~4475+)
**Current:**
```python
normalize_response(result)
return result
```

**Needs:**
- Extract plan from `result["outcome"]`
- Extract render_result (may be empty)
- Call `_build_api_response(plan, render_result, ...)`

### 9. Initial Result Construction (Line ~3043)
**Current:**
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
        "plan": populated_plan,
        "facts": outcome_facts
    }
}
```

**Needs:**
- Use `populated_plan` directly as `plan`
- Create empty `render_result = {}`
- Call `_build_api_response(populated_plan, render_result, decision, None, effective_response)`

### 10. Error Returns
**Current:**
```python
return {
    "success": False,
    "error": "...",
    "message": "..."
}
```

**Needs:**
- Call `_build_api_response(None, {}, None, None, None, error={"error": "...", "message": "..."})`

## Migration Strategy

1. **Extract plan from existing result structures**
   - If `result["outcome"]` exists, extract fields to build `plan` dict
   - If `populated_plan` exists, use it directly

2. **Extract render_result from existing result structures**
   - If `result["outcome"]["text"]` exists, extract to `render_result["text"]`
   - If `result["outcome"]["ui_actions"]` exists, extract to `render_result["ui_actions"]`
   - If `result["outcome"]["ui_hint"]` exists, extract to `render_result["ui_hint"]`

3. **Call `_build_api_response()`**
   - Pass plan, render_result, and other context
   - Remove `normalize_response()` calls (handled by `_build_api_response()`)

4. **Remove `outcome` construction**
   - No longer build `result["outcome"]` dicts
   - All fields go directly to top-level response

## Notes

- `_build_api_response()` does NOT use `outcome` internally
- All fields are mapped explicitly from `plan` and `render_result`
- `status`, `text`, `ui_actions`, `ui_hint` are set ONLY in `_build_api_response()`

