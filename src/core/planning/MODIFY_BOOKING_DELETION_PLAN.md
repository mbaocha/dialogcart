# MODIFY_BOOKING Deletion Plan

## Status: READY FOR TESTING

## Overview
MODIFY_BOOKING sequencing logic has been encoded in YAML policy. Once parity tests pass, we can delete the override blocks from `plan_builder.py`.

## YAML Policy Encoding

The sequencing is now defined in `intent_policy.yaml`:
- **SEARCH_AVAILABILITY**: Runs when `confirmation_state is None` (to get confirmation)
- **APPLY_MODIFICATION**: Runs when `confirmation_state == "confirmed"`

The policy selector (`select_next_execution_step`) has been updated to:
- Check `confirmation_state` flag for MODIFY_BOOKING
- Select SEARCH_AVAILABILITY when `confirmation_state is None`
- Select APPLY_MODIFICATION when `confirmation_state == "confirmed"`

## Code to Delete

Once parity tests pass, delete the following MODIFY_BOOKING override blocks from `plan_builder.py`:

### 1. Policy selection override (lines ~414-448)
```python
# MODIFY_BOOKING guardrail: Allow APPLY_MODIFICATION on confirmation, otherwise SEARCH_AVAILABILITY first
# CRITICAL: Check confirmation_state FIRST - if confirmed, allow APPLY_MODIFICATION regardless of availability
if intent_name == "MODIFY_BOOKING" and action == "APPLY_MODIFICATION":
    # ... entire block ...
```

### 2. CONFIRM_ACTION_MAP override (lines ~475-504)
```python
# MODIFY_BOOKING guardrail: Allow APPLY_MODIFICATION on confirmation
if intent_name == "MODIFY_BOOKING" and candidate_action == "APPLY_MODIFICATION":
    # ... entire block ...
```

### 3. Commit action override (lines ~519-548)
```python
# MODIFY_BOOKING guardrail: Allow APPLY_MODIFICATION on confirmation
if intent_name == "MODIFY_BOOKING" and commit_action == "APPLY_MODIFICATION":
    # ... entire block ...
```

### 4. Fallback commit blocked override (lines ~559-586)
```python
if intent_name == "MODIFY_BOOKING":
    # MODIFY_BOOKING: Allow APPLY_MODIFICATION on confirmation
    # ... entire block ...
```

### 5. Last resort fallback override (lines ~605-632)
```python
if intent_name == "MODIFY_BOOKING":
    # MODIFY_BOOKING: Allow APPLY_MODIFICATION on confirmation
    # ... entire block ...
```

### 6. CONFIRM_ACTION_MAP entry
```python
CONFIRM_ACTION_MAP = {
    "MODIFY_BOOKING": "APPLY_MODIFICATION",  # DELETE THIS LINE
    "CANCEL_BOOKING": "CONFIRM_CANCELLATION"
}
```

## Testing Requirements

Before deletion:
1. Enable policy for MODIFY_BOOKING: `POLICY_INTENTS_ENABLED=MODIFY_BOOKING`
2. Run parity tests covering:
   - MODIFY_BOOKING with `confirmation_state == "confirmed"` → should select APPLY_MODIFICATION
   - MODIFY_BOOKING with `confirmation_state is None` → should select SEARCH_AVAILABILITY
   - MODIFY_BOOKING with `confirmation_state is None` and `availability_resolved == True` → should still select SEARCH_AVAILABILITY (for confirmation)
3. Run full E2E suite with MODIFY_BOOKING enabled
4. Verify zero diffs in shadow mode

## Notes

- The policy selector now handles `confirmation_state` flag for MODIFY_BOOKING
- SEARCH_AVAILABILITY is defined as an exploratory step in YAML
- APPLY_MODIFICATION requires `availability_resolved` in YAML, but the confirmation_state check takes precedence
- All override blocks should be removed once parity is confirmed

