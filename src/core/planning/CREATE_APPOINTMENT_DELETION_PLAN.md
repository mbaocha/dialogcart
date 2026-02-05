# CREATE_APPOINTMENT Deletion Plan

This document marks code that can be safely deleted once CREATE_APPOINTMENT parity is confirmed.

## Prerequisites

1. ✅ Per-intent policy enablement implemented (`POLICY_INTENTS_ENABLED`)
2. ✅ Parity test helper implemented (`assert_parity`)
3. ✅ Run full E2E suite with `POLICY_INTENTS_ENABLED=CREATE_APPOINTMENT` and confirm zero diffs
4. ✅ Delete CREATE_APPOINTMENT-specific branching

## Code to Delete (After Parity Confirmation)

### 1. CONFIRM_ACTION_MAP Entry for CREATE_APPOINTMENT

**Location**: `src/core/planning/orchestration/plan_builder.py` line ~367

**Current Code**:
```python
CONFIRM_ACTION_MAP = {
    "CREATE_APPOINTMENT": "CONFIRM_APPOINTMENT",  # ← DELETE THIS LINE
    "CREATE_RESERVATION": "CONFIRM_RESERVATION",
    "MODIFY_BOOKING": "APPLY_MODIFICATION",
    "CANCEL_BOOKING": "CONFIRM_CANCELLATION"
}
```

**After Deletion**:
```python
CONFIRM_ACTION_MAP = {
    "CREATE_RESERVATION": "CONFIRM_RESERVATION",
    "MODIFY_BOOKING": "APPLY_MODIFICATION",
    "CANCEL_BOOKING": "CONFIRM_CANCELLATION"
}
```

**Rationale**: Once policy is enabled for CREATE_APPOINTMENT, `select_next_execution_step` will always return a step (either SEARCH_AVAILABILITY or CONFIRM_APPOINTMENT based on `requires: [availability_resolved]`). The fallback to CONFIRM_ACTION_MAP should never be hit for CREATE_APPOINTMENT.

### 2. Fallback Logic Branch (if needed)

**Location**: `src/core/planning/orchestration/plan_builder.py` lines ~472-516

**Note**: The fallback logic itself should remain (for other intents), but CREATE_APPOINTMENT should never reach it once policy is enabled. If parity tests show CREATE_APPOINTMENT never hits the fallback, no code deletion needed here - the policy path will be taken instead.

## Verification Steps

1. **Enable policy for CREATE_APPOINTMENT only**:
   ```bash
   export POLICY_INTENTS_ENABLED=CREATE_APPOINTMENT
   export INTENT_POLICY_PLANNER_MODE=on
   ```

2. **Run parity tests**:
   ```bash
   cd src
   python -m pytest core/tests/planning/test_policy_parity.py::TestPolicyParity::test_create_appointment_complete_slots_availability_resolved -v
   python -m pytest core/tests/planning/test_policy_parity.py::TestPolicyParity::test_all_followup_scenarios_create_appointment -v
   ```

3. **Run full E2E suite**:
   ```bash
   python -m core.tests.planning.test_planning
   ```

4. **Verify zero diffs**: All CREATE_APPOINTMENT scenarios should pass with identical outputs in both modes.

5. **Check logs**: Verify that CREATE_APPOINTMENT never hits fallback paths (no "fallback_confirm_map" or "fallback_availability_blocked" action_branch for CREATE_APPOINTMENT).

## After Deletion

Once CREATE_APPOINTMENT entry is removed from CONFIRM_ACTION_MAP:
- Policy becomes the sole source of truth for CREATE_APPOINTMENT action selection
- Fallback logic will not be used for CREATE_APPOINTMENT (policy always returns a step)
- Other intents continue using CONFIRM_ACTION_MAP until they're migrated

## Safety

- Keep CONFIRM_ACTION_MAP for other intents (CREATE_RESERVATION, MODIFY_BOOKING, CANCEL_BOOKING)
- Only delete CREATE_APPOINTMENT entry after parity is confirmed
- Policy mode can be toggled per-intent, so other intents remain unaffected

