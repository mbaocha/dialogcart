# Session Persistence Audit: Control-Plane Fields

> **Status (2026-06):** Implementation moved to `core/session/persist.py`. Session merge
> re-exports from `core/orchestration/api/session_merge.py`. Note: persist collapses
> `AWAITING_CAPABILITY` and `AWAITING_CONFIRMATION` into session `NEEDS_CLARIFICATION`
> (see `persist.py` status mapping). Line references below are stale.

## Overview

This document audits `build_session_state_from_outcome()` to ensure control-plane fields from the outcome are correctly handled during session persistence. It identifies which fields may legally be read from outcome and which are intentionally not persisted.

## Control-Plane Fields in Outcome

From `build_outcome_from_decision()` (orchestrator.py), the outcome dictionary contains:

### Core Fields (Always Present)
- `intent_name`: Intent name
- `status`: Planning status (READY, NEEDS_CLARIFICATION, AWAITING_CONFIRMATION, AWAITING_CAPABILITY)
- `slots`: Collected slot values
- `missing_slots`: Missing required slots
- `facts`: Facts container (slots, missing_slots, context, capability facts)
- `plan`: Plan object with status, stage, action

### Control-Plane Fields (Conditional)
- `active_capability`: Active capability name (e.g., "payment") when capability is active
- `awaiting`: What the system is awaiting (from plan)
- `executable_actions`: List of actions that can be executed
- `blocked_actions`: List of blocked actions
- `allowed_actions`: List of allowed actions
- `stage`: Current planning stage (e.g., "AVAILABILITY", "CONFIRM")
- `action`: Current action (e.g., "SEARCH_AVAILABILITY", "CONFIRM_APPOINTMENT")

## Fields That May Legally Be Read From Outcome

### ✅ Status: READ FROM OUTCOME (with normalization)
**Location:** `build_session_state_from_outcome()` line 2375-2383

**Current Implementation:**
- Reads from `outcome_status` parameter (derived from `outcome.status`)
- **FIXED:** Preserves `AWAITING_CAPABILITY` (no longer converts to `NEEDS_CLARIFICATION`)
- Normalizes `NEEDS_CLARIFICATION` and `AWAITING_CONFIRMATION` to `NEEDS_CLARIFICATION`
- Maps all other statuses to `READY`

**Rationale:** Status is a control-plane field that determines session lifecycle. It must be read from outcome to reflect current planning state.

**Legal to read:** ✅ YES

### ✅ active_capability: READ FROM OUTCOME (with fallback)
**Location:** `build_session_state_from_outcome()` line 2476-2483

**Current Implementation:**
- **FIXED:** Prioritizes `outcome.active_capability` over `previous_session_state.active_capability`
- Falls back to `previous_session_state.active_capability` only if outcome doesn't have it
- Clears when `payment_satisfied=True` in facts (line 2488-2494)

**Rationale:** When a capability returns `completed=False`, the outcome is the source of truth for `active_capability`. It must be preserved to maintain capability state across turns.

**Legal to read:** ✅ YES

### ✅ Facts: READ FROM OUTCOME (merged with previous)
**Location:** `build_session_state_from_outcome()` line 2113-2129

**Current Implementation:**
- Reads `outcome.facts` and merges with `previous_session_state.facts`
- New facts from outcome override old facts from session
- Ensures capability facts (e.g., `payment_satisfied`) persist

**Rationale:** Facts are first-class, durable session state. They must be read from outcome to capture capability completion facts and other turn-level state.

**Legal to read:** ✅ YES

### ✅ Intent Name: READ FROM OUTCOME (with precedence)
**Location:** `build_session_state_from_outcome()` line 2165-2227

**Current Implementation:**
- Priority 1: `outcome.intent_name` (if durable)
- Priority 2: `outcome.plan.intent_name` (if durable)
- Priority 3: `previous_session_state.intent_name` (if durable)

**Rationale:** Intent name determines session scope and slot filtering. Outcome is the primary source, with fallback to previous session for continuity.

**Legal to read:** ✅ YES

## Fields That Are NOT Persisted (Intentionally)

### ❌ awaiting: NOT PERSISTED
**Location:** Not present in `session_state` construction (line 2526-2534, 2542-2549)

**Rationale:** `awaiting` is a transient planning field that indicates what the system is waiting for. It's recomputed each turn based on current state and doesn't need to persist.

**Legal to read:** ❌ NO (not needed for persistence)

### ❌ executable_actions: NOT PERSISTED
**Location:** Not present in `session_state` construction

**Rationale:** `executable_actions` is a transient planning field that indicates which actions can be executed with current slots. It's recomputed each turn based on current state and policy.

**Legal to read:** ❌ NO (not needed for persistence)

### ❌ blocked_actions: NOT PERSISTED
**Location:** Not present in `session_state` construction

**Rationale:** `blocked_actions` is a transient planning field that indicates which actions are blocked. It's recomputed each turn based on current state and policy.

**Legal to read:** ❌ NO (not needed for persistence)

### ❌ allowed_actions: NOT PERSISTED
**Location:** Not present in `session_state` construction

**Rationale:** `allowed_actions` is a transient planning field that indicates which actions are allowed. It's recomputed each turn based on current state and policy.

**Legal to read:** ❌ NO (not needed for persistence)

### ❌ stage: NOT PERSISTED (but logged)
**Location:** Logged in `SESSION_SAVE_DEBUG` (line 2584-2589) but not in `session_state`

**Rationale:** `stage` is a transient planning field that indicates current planning stage. It's recomputed each turn and doesn't need to persist. However, it's logged for debugging.

**Legal to read:** ❌ NO (not needed for persistence, but may be read for logging)

### ❌ action: NOT PERSISTED (but logged)
**Location:** Logged in `SESSION_SAVE_DEBUG` (line 2584-2589) but not in `session_state`

**Rationale:** `action` is a transient planning field that indicates current action. It's recomputed each turn and doesn't need to persist. However, it's logged for debugging.

**Legal to read:** ❌ NO (not needed for persistence, but may be read for logging)

## Summary: Fields That May Legally Be Read From Outcome

| Field | Read from Outcome? | Persisted? | Notes |
|-------|-------------------|-----------|-------|
| `status` | ✅ YES | ✅ YES | Normalized (AWAITING_CAPABILITY preserved) |
| `active_capability` | ✅ YES | ✅ YES | Prioritized over previous_session_state |
| `facts` | ✅ YES | ✅ YES | Merged with previous_session_state |
| `intent_name` | ✅ YES | ✅ YES | With precedence rules (durable intents only) |
| `slots` | ⚠️ INDIRECT | ✅ YES | Read from `merged_luma_response`, fallback to `outcome.slots` |
| `missing_slots` | ❌ NO | ✅ YES | Recomputed from persisted slots (not from outcome) |
| `awaiting` | ❌ NO | ❌ NO | Transient planning field |
| `executable_actions` | ❌ NO | ❌ NO | Transient planning field |
| `blocked_actions` | ❌ NO | ❌ NO | Transient planning field |
| `allowed_actions` | ❌ NO | ❌ NO | Transient planning field |
| `stage` | ❌ NO | ❌ NO | Transient planning field (logged only) |
| `action` | ❌ NO | ❌ NO | Transient planning field (logged only) |

## Bugs Fixed

### Bug 1: Status Normalization (FIXED)
**Issue:** `AWAITING_CAPABILITY` was converted to `NEEDS_CLARIFICATION` during persistence.

**Fix:** Preserve `AWAITING_CAPABILITY` as a distinct status (line 2378-2379).

**Impact:** Capability state is now correctly preserved across turns.

### Bug 2: active_capability Not Read From Outcome (FIXED)
**Issue:** `active_capability` was only read from `previous_session_state`, ignoring `outcome.active_capability`.

**Fix:** Prioritize `outcome.active_capability` over `previous_session_state.active_capability` (line 2479-2483).

**Impact:** When a capability returns `completed=False`, `active_capability` is now correctly preserved.

## Recommendations

1. **Documentation:** Add inline comments explaining why transient fields (`awaiting`, `executable_actions`, etc.) are not persisted.

2. **Invariant:** Add an assertion that `awaiting`, `executable_actions`, `blocked_actions`, and `allowed_actions` are never read from outcome for persistence purposes.

3. **Testing:** Add regression tests to ensure:
   - `AWAITING_CAPABILITY` status is preserved
   - `active_capability` is read from outcome when present
   - Transient fields are not accidentally persisted

## Conclusion

The audit confirms that:
- ✅ Control-plane fields that need to persist (`status`, `active_capability`, `facts`, `intent_name`) are correctly read from outcome
- ✅ Transient planning fields (`awaiting`, `executable_actions`, etc.) are intentionally not persisted
- ✅ Recent fixes ensure `AWAITING_CAPABILITY` and `active_capability` are correctly preserved

No additional bugs were found. The function correctly distinguishes between persistent control-plane fields and transient planning fields.



