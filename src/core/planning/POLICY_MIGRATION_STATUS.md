# Policy Planner Migration Status

This document tracks the incremental migration of planning logic from `plan_builder.py` to `intent_policy.yaml`.

## Migration Strategy

1. **Parity Testing**: Run scenarios twice (mode=off vs mode=on) and assert equality
2. **Incremental Migration**: Move intent-specific logic to YAML, remove branching from plan_builder.py
3. **Verification**: Ensure parity tests pass before removing code

## Completed

### 1. Parity Test Suite ✅
- Created `test_policy_parity.py` that runs scenarios in both modes
- Tests assert equality for: `status`, `action`, `missing_slots`, `intent_name`
- Supports testing individual scenarios and full followup scenario suites
- Added `assert_parity()` helper method for easy testing

### 2. Per-Intent Policy Enablement ✅
- Added `POLICY_INTENTS_ENABLED` environment variable (comma-separated list)
- If intent not in set → force mode=off behavior regardless of global mode
- Allows incremental migration: enable policy for one intent at a time
- Example: `POLICY_INTENTS_ENABLED=CREATE_APPOINTMENT`

### 3. CREATE_APPOINTMENT Migration ✅ COMPLETE
- **Removed**: Special branching that blocked CONFIRM_APPOINTMENT when `availability_resolved=False` and `missing_slots != []`
- **Deleted**: CREATE_APPOINTMENT entry from CONFIRM_ACTION_MAP (policy is now sole source of truth)
- **YAML Handles**: 
  - `CONFIRM_APPOINTMENT` has `requires: [availability_resolved]` - policy checks this
  - `CONFIRM_APPOINTMENT` has `required_slots: [service_id, date, time]` - policy checks planning completeness
- **Result**: Policy-based selection now handles CREATE_APPOINTMENT correctly via YAML
- **Status**: ✅ **MIGRATION COMPLETE** - All tests pass, code deleted, policy is sole source of truth

## In Progress

### 3. CREATE_RESERVATION Migration ✅ COMPLETE
- **Status**: ✅ **MIGRATION COMPLETE** - Parity tests pass, code deleted
- **Deleted**: CREATE_RESERVATION entry from CONFIRM_ACTION_MAP (policy is now sole source of truth)
- **YAML Configuration**: ✅ Already has `requires: [availability_resolved]` for CONFIRM_RESERVATION
- **Tests**: ✅ Parity tests pass with `enabled_intents="CREATE_RESERVATION"`
- **Result**: Policy-based selection now handles CREATE_RESERVATION correctly via YAML

### 4. CANCEL_BOOKING Migration ✅ COMPLETE
- **Status**: ✅ **MIGRATION COMPLETE** - Code deleted, policy is now sole source of truth
- **YAML Configuration**: ✅ Already has `CONFIRM_CANCELLATION` with `required_slots: [booking_id]`
- **Tests**: ✅ Parity tests pass with `enabled_intents="CANCEL_BOOKING"`
- **Complexity**: Simple - no availability requirements, single execution step
- **Deleted**: 
  - CANCEL_BOOKING entry from CONFIRM_ACTION_MAP
  - Fallback blocks for CANCEL_BOOKING (commit_blocked and last_resort)
- **Result**: Policy-based selection now handles CANCEL_BOOKING correctly via YAML

## In Progress

### 5. MODIFY_BOOKING Migration ✅ COMPLETE
- **Status**: ✅ **MIGRATION COMPLETE** - Code deleted, policy is now sole source of truth
- **YAML Configuration**: ✅ 
  - Added `FETCH_BOOKING` as exploratory step
  - Added `SEARCH_AVAILABILITY` as exploratory step (runs when `confirmation_state is None`)
  - `APPLY_MODIFICATION` requires `availability_resolved` and `confirmation_state_confirmed`
- **Policy Selector**: ✅ Updated to check `confirmation_state` flag for MODIFY_BOOKING
- **Tests**: ✅ Parity tests added for MODIFY_BOOKING with various confirmation_state values
- **Deleted**: 
  - All MODIFY_BOOKING override blocks from plan_builder.py (policy selection, CONFIRM_ACTION_MAP, commit action, fallback blocks)
  - MODIFY_BOOKING entry from CONFIRM_ACTION_MAP
- **Result**: Policy-based selection now handles MODIFY_BOOKING correctly via YAML

## In Progress

### 6. MODIFY_RESERVATION Migration ✅ COMPLETE
- **Status**: ✅ **MIGRATION COMPLETE** - Code deleted, policy is now sole source of truth
- **YAML Configuration**: ✅ 
  - Added `FETCH_BOOKING` as exploratory step
  - Added `SEARCH_AVAILABILITY` as exploratory step (runs when `confirmation_state is None`)
  - `APPLY_MODIFICATION` requires `availability_resolved` and `confirmation_state_confirmed`
- **Policy Selector**: ✅ Updated to check `confirmation_state` flag for MODIFY_RESERVATION
- **Tests**: ✅ Parity tests added for MODIFY_RESERVATION with various confirmation_state values
- **Deleted**: 
  - No MODIFY_RESERVATION override blocks found (no special handling was needed)
- **Result**: Policy-based selection now handles MODIFY_RESERVATION correctly via YAML

### 7. CONFIRM_ACTION_MAP Removal
- Once all intents are parity-clean, remove the CONFIRM_ACTION_MAP fallback
- Policy should be the sole source of truth

## Testing

### Run Parity Tests

Run all parity tests:
```bash
cd src
python -m pytest core/tests/planning/test_policy_parity.py -v
```

Run with specific intent:
```bash
python -m pytest core/tests/planning/test_policy_parity.py::TestPolicyParity::test_create_appointment_complete_slots_availability_resolved -v
```

### Enable Policy for CREATE_APPOINTMENT Only

```bash
export POLICY_INTENTS_ENABLED=CREATE_APPOINTMENT
export INTENT_POLICY_PLANNER_MODE=on
python -m pytest core/tests/planning/test_policy_parity.py -v
```

### Run Full E2E Suite

```bash
export POLICY_INTENTS_ENABLED=CREATE_APPOINTMENT
export INTENT_POLICY_PLANNER_MODE=on
python -m core.tests.planning.test_planning
```

Verify zero diffs for CREATE_APPOINTMENT scenarios.

## YAML Structure

The `intent_policy.yaml` file defines:
- **planning.required_slots**: Slots needed for planning completeness
- **execution.{action}.requires**: Prerequisites (e.g., `[availability_resolved]`)
- **execution.{action}.required_slots**: Step-specific slot requirements
- **execution step ordering**: Steps are evaluated in YAML order

Policy selection logic (in `select_next_execution_step`):
1. Iterates through execution steps in YAML order
2. For committing steps: checks planning.required_slots completeness
3. For exploratory steps: checks step-specific required_slots
4. Checks `requires` prerequisites (e.g., availability_resolved)
5. Returns first matching step

## Notes

- The migration is incremental - we keep fallback logic until parity is proven
- Mode=shadow allows comparison without changing behavior
- Mode=on uses policy decisions with fallback to current if policy returns None
- Once all intents pass parity, we can remove fallbacks and make YAML the sole source of truth

