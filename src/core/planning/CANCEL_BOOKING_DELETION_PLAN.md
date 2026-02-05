# CANCEL_BOOKING Deletion Plan

## Status: READY FOR DELETION

## Overview
CANCEL_BOOKING is a simple intent with a single execution step (CONFIRM_CANCELLATION). The YAML policy already correctly defines it. Once parity tests pass, we can delete the fallback blocks.

## YAML Policy Configuration

The policy is already correctly defined in `intent_policy.yaml`:
- **CONFIRM_CANCELLATION**: Single committing step with `required_slots: [booking_id]`
- No availability requirements
- No sequencing complexity

## Code to Delete

### 1. CONFIRM_ACTION_MAP entry (line ~373)
```python
CONFIRM_ACTION_MAP = {
    "MODIFY_BOOKING": "APPLY_MODIFICATION",
    "CANCEL_BOOKING": "CONFIRM_CANCELLATION"  # DELETE THIS LINE
}
```

### 2. Fallback commit blocked override (lines ~595-597)
```python
elif intent_name == "CANCEL_BOOKING":
    action = "CONFIRM_CANCELLATION"
    action_branch = "fallback_commit_blocked"
```

### 3. Last resort fallback override (lines ~641-643)
```python
elif intent_name == "CANCEL_BOOKING":
    action = "CONFIRM_CANCELLATION"
    action_branch = "fallback_last_resort"
```

### 4. Missing slots handling (line ~378)
The missing slots handling for CANCEL_BOOKING is shared with MODIFY_BOOKING:
```python
if intent_name in ("MODIFY_BOOKING", "CANCEL_BOOKING"):
```
This should remain until MODIFY_BOOKING is also migrated, then it can be updated to only check MODIFY_BOOKING.

## Testing Requirements

Before deletion:
1. Enable policy for CANCEL_BOOKING: `POLICY_INTENTS_ENABLED=CANCEL_BOOKING`
2. Run parity tests:
   - `test_cancel_booking_complete_slots`
   - `test_cancel_booking_missing_slots`
3. Verify zero diffs

## Notes

- CANCEL_BOOKING is the simplest intent - single step, no prerequisites
- Policy selector should handle it correctly via YAML
- All fallback blocks should be removed once parity is confirmed
- Missing slots handling will remain shared with MODIFY_BOOKING until MODIFY_BOOKING is also migrated
