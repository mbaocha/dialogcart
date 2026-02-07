# Rendering Investigation Report

## Problem
Clarification rendering output (`text` field) is not attached to the final orchestrator response.

## Investigation Findings

### 1. Rendering Function Location
- **Function**: `_inject_rendering_text` (line 136-159 in `orchestrator.py`)
- **Calls**: `rendering.render(decision)` which detects clarification state and renders text
- **Injection Point**: Sets `result["text"] = rendered_text` at top level

### 2. Code Flow Analysis

#### Path A: `planning_only=True` (via `plan_message`)
- **Entry Point**: `handle_message()` → `plan_message()` → `handle_message_legacy(planning_only=True)`
- **Rendering Injection**: Line 2454 - `_inject_rendering_text(result, decision)` 
- **Return Point**: Line 2456 - `return result`
- **Status**: ✅ Rendering IS called here
- **Problem**: `plan_message()` extracts only specific fields (lines 3659-3666) and **strips out `text` field**

#### Path B: `planning_only=False` (normal execution)
- **Entry Point**: `handle_message()` → `plan_message()` → execution logic
- **NEEDS_CLARIFICATION Handling**: Line 2492 - `if plan_status == "NEEDS_CLARIFICATION":`
- **Rendering Injection**: Line 2569 - `_inject_rendering_text(result, decision)`
- **Return Point**: Line 2572 - `return result`
- **Status**: ✅ Rendering IS called here
- **Problem**: This path is only reached when `planning_only=False`, but `handle_message()` always calls `plan_message()` first

### 3. Root Cause

**The issue is in `plan_message()` function (lines 3658-3695):**

```python
# Extract required fields from outcome
planning_result = {
    "intent_name": outcome.get("intent_name", ""),
    "stage": outcome.get("stage"),
    "action": outcome.get("action"),
    "slots": outcome.get("slots", {}),
    "missing_slots": outcome.get("missing_slots", []),
    "status": outcome.get("status")
}
# ... time_constraint extraction ...
return planning_result  # ❌ text field is NOT included
```

**Then in `handle_message()` (line 457-460):**
```python
if not can_execute:
    return {
        "success": True,
        "result": plan  # ❌ This plan comes from plan_message(), which doesn't have text
    }
```

### 4. Decision Object Structure at Rendering Time

At line 2454 and 2569, `decision` object should have:
- `decision["plan"]["status"]` - Plan status (NEEDS_CLARIFICATION, READY, etc.)
- `decision["facts"]["missing_slots"]` - List of missing slots
- `decision["facts"]["slots"]` - Collected slots for template interpolation

### 5. Where Rendering Should Be Injected

**Primary Fix Location**: `plan_message()` function at line 3695
- **Function**: `plan_message()` 
- **Line**: After line 3693 (after time_constraint is added)
- **Action**: Extract `text` from the full result before returning planning_result

**Alternative Fix Location**: `handle_message()` function at line 457
- **Function**: `handle_message()`
- **Line**: Before line 457 (when building return for non-executable case)
- **Action**: Check if `text` exists in the full result from `handle_message_legacy` and include it

### 6. Verification Points

To verify rendering is working:
1. **Line 2454**: Check if `_inject_rendering_text` is called (for planning_only path)
2. **Line 2569**: Check if `_inject_rendering_text` is called (for NEEDS_CLARIFICATION path)
3. **Line 151 in `_inject_rendering_text`**: Check if `render(decision)` returns non-None
4. **Line 152**: Check if `result["text"]` is set
5. **Line 3695 in `plan_message`**: Check if `text` is preserved when extracting planning_result

### 7. Summary

**Answer to Question**: 
- **a) Never called?** ❌ NO - Rendering IS called at lines 2454 and 2569
- **b) Called but return value ignored?** ✅ YES - The `text` field is set in `result` but then stripped out by `plan_message()` 
- **c) Conditionally skipped?** ❌ NO - It's called for both paths

**Exact Function and Line for Fix**:
- **Function**: `plan_message()` 
- **Line**: 3695 (before return statement)
- **Fix**: Extract `text` from `result` (which comes from `handle_message_legacy`) and include it in `planning_result`

