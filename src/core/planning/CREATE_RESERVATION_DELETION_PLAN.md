# CREATE_RESERVATION Deletion Plan

This document marks code that can be safely deleted once CREATE_RESERVATION parity is confirmed.

## Prerequisites

1. ✅ Per-intent policy enablement implemented (`POLICY_INTENTS_ENABLED`)
2. ✅ Parity test helper implemented (`assert_parity`)
3. ✅ Run parity tests with `POLICY_INTENTS_ENABLED=CREATE_RESERVATION` and confirm zero diffs
4. ✅ Delete CREATE_RESERVATION-specific branching

## Code to Delete (After Parity Confirmation)

### 1. CONFIRM_ACTION_MAP Entry for CREATE_RESERVATION

**Location**: `src/core/planning/orchestration/plan_builder.py` line ~368

**Current Code**:
```python
CONFIRM_ACTION_MAP = {
    "CREATE_RESERVATION": "FINALIZE_RESERVATION",  # ← DELETE THIS LINE
    "MODIFY_BOOKING": "APPLY_MODIFICATION",
    "CANCEL_BOOKING": "CONFIRM_CANCELLATION"
}
```

**After Deletion**:
```python
CONFIRM_ACTION_MAP = {
    "MODIFY_BOOKING": "APPLY_MODIFICATION",
    "CANCEL_BOOKING": "CONFIRM_CANCELLATION"
}
```

**Rationale**: Once policy is enabled for CREATE_RESERVATION, `select_next_execution_step` will always return a step (either SEARCH_AVAILABILITY or FINALIZE_RESERVATION based on `requires: [availability_resolved]`). The fallback to CONFIRM_ACTION_MAP should never be hit for CREATE_RESERVATION.

### 2. Generic Comment Update

**Location**: `src/core/planning/orchestration/plan_builder.py` line ~380

**Current Code**:
```python
# CREATE_APPOINTMENT or CREATE_RESERVATION
```

**After Deletion**:
```python
# CREATE_APPOINTMENT (policy-enabled) or CREATE_RESERVATION (policy-enabled)
# Note: Both now use policy, but comment kept for clarity
```

**Note**: This is just a comment update for clarity, not critical deletion.

## Verification Steps

1. **Enable policy for CREATE_RESERVATION only**:
   ```bash
   export POLICY_INTENTS_ENABLED=CREATE_RESERVATION
   export INTENT_POLICY_PLANNER_MODE=on
   ```

2. **Run parity tests**:
   ```bash
   cd src
   python -m pytest core/tests/planning/test_policy_parity.py::TestPolicyParity::test_create_reservation_complete_slots -v
   ```

3. **Run full E2E suite** (if CREATE_RESERVATION scenarios exist):
   ```bash
   python -m core.tests.planning.test_planning
   ```

4. **Verify zero diffs**: All CREATE_RESERVATION scenarios should pass with identical outputs in both modes.

5. **Check logs**: Verify that CREATE_RESERVATION never hits fallback paths (no "fallback_confirm_map" action_branch for CREATE_RESERVATION).

## YAML Configuration

CREATE_RESERVATION YAML already has:
- `FINALIZE_RESERVATION` with `requires: [availability_resolved]` ✅
- `FINALIZE_RESERVATION` with `required_slots: [service_id, date_range]` ✅
- `SEARCH_AVAILABILITY` as exploratory step ✅

Policy should handle all gating logic correctly.

## After Deletion

Once CREATE_RESERVATION entry is removed from CONFIRM_ACTION_MAP:
- Policy becomes the sole source of truth for CREATE_RESERVATION action selection
- Fallback logic will not be used for CREATE_RESERVATION (policy always returns a step)
- Other intents continue using CONFIRM_ACTION_MAP until they're migrated

## Safety

- Keep CONFIRM_ACTION_MAP for other intents (MODIFY_BOOKING, CANCEL_BOOKING)
- Only delete CREATE_RESERVATION entry after parity is confirmed
- Policy mode can be toggled per-intent, so other intents remain unaffected

