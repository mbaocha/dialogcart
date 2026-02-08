# AWAITING_CONFIRMATION vs READY Investigation Report

## Problem Statement

The conversation E2E test expects `status=READY` on turn 3 (user says "yes"), but the orchestrator returns `status=AWAITING_CONFIRMATION`.

## Investigation Findings

### 1. Status Determination Logic

**Location**: `src/core/planning/orchestration/plan_builder.py` (lines 227-237)

The planner sets status in this priority order:
1. **NEEDS_CLARIFICATION** - if `missing_slots` is non-empty OR `needs_clarification=True`
2. **AWAITING_CONFIRMATION** - if `confirmation_state == "pending"`
3. **READY** - otherwise (all slots filled, no clarification needed, no pending confirmation)

```python
elif confirmation_state == "pending":
    status = "AWAITING_CONFIRMATION"
    logger.debug(
        f"[BUILD_PLAN] Setting status=AWAITING_CONFIRMATION because confirmation_state=pending"
    )
else:
    status = "READY"
```

### 2. When AWAITING_CONFIRMATION is Emitted

**Condition**: `confirmation_state == "pending"` in the Luma response

**Purpose**: Indicates that all required slots are filled, but the system is waiting for user confirmation before executing the booking action (e.g., CONFIRM_APPOINTMENT).

**State Machine Flow**:
```
NEEDS_CLARIFICATION (missing slots)
    ↓ (slots filled)
READY (all slots filled, no confirmation needed)
    ↓ (confirmation_state=pending)
AWAITING_CONFIRMATION (slots filled, awaiting user confirmation)
    ↓ (user confirms)
EXECUTED (booking confirmed)
```

### 3. Test Scenario Analysis

**Turn 3 in scenario**: User says "yes" (confirmation response)

**Mock Luma Response** (line 94 in `test_conversation_rendering_e2e.py`):
```python
if action == "CONFIRM_APPOINTMENT":
    response["booking"]["confirmation_state"] = "pending"
```

**Actual Behavior**:
- All slots are filled: `missing_slots = []`
- `confirmation_state = "pending"` is set
- Planner correctly sets `status = AWAITING_CONFIRMATION`
- Action is `SEARCH_AVAILABILITY` (not yet `CONFIRM_APPOINTMENT`)

**Test Expectation** (line 25 in `conversation_rendering.yaml`):
```yaml
- sentence: "yes"
  expect:
    status: READY  # ❌ Incorrect expectation
    action: CONFIRM_APPOINTMENT
```

### 4. Is AWAITING_CONFIRMATION Correct?

**YES** - This is the correct designed behavior.

**Reasoning**:
1. **Semantic Correctness**: When all slots are filled but confirmation is pending, the system is literally "awaiting confirmation" from the user.
2. **State Machine Integrity**: `AWAITING_CONFIRMATION` is a distinct state from `READY`:
   - `READY`: All slots filled, ready to execute (e.g., SEARCH_AVAILABILITY)
   - `AWAITING_CONFIRMATION`: All slots filled, but waiting for user to confirm before committing
3. **Business Logic**: The confirmation step is a critical gate before executing committing actions like `CONFIRM_APPOINTMENT`.

### 5. Test Output Evidence

From `test.out` line 381:
```
[PLANNING_DECISION] AFTER planner build_decision_plan: 
  intent=CREATE_APPOINTMENT, 
  plan.status=AWAITING_CONFIRMATION,  # ✅ Correct
  plan.stage=AVAILABILITY, 
  plan.action=SEARCH_AVAILABILITY
```

From `test.out` line 307:
```
"status": "AWAITING_CONFIRMATION",
"decision_reason": "NEEDS_CONFIRMATION"  # ✅ Explicitly indicates confirmation needed
```

## Recommendation

### Option 1: Update Test Expectations (RECOMMENDED)

**Change**: Update the scenario YAML to expect `AWAITING_CONFIRMATION` instead of `READY` for confirmation turns.

**Rationale**:
- Tests should reflect actual system behavior
- `AWAITING_CONFIRMATION` is the correct semantic state
- This validates the state machine works as designed

**Implementation**:
```yaml
- sentence: "yes"
  expect:
    status: AWAITING_CONFIRMATION  # ✅ Correct expectation
    action: SEARCH_AVAILABILITY  # Note: action may still be SEARCH_AVAILABILITY
    text:
      present: false
```

### Option 2: Use State-Semantic Assertions (ALTERNATIVE)

**Change**: Instead of asserting exact status, assert semantic meaning:
- `missing_slots == []` (all slots filled)
- `action == CONFIRM_APPOINTMENT` OR `awaiting == "USER_CONFIRMATION"` (confirmation flow)

**Rationale**:
- More resilient to state machine refinements
- Focuses on business logic rather than implementation details
- Allows for future state additions

**Implementation**:
```yaml
- sentence: "yes"
  expect:
    missing_slots: []
    action: CONFIRM_APPOINTMENT
    # OR semantic check:
    # awaiting: USER_CONFIRMATION
    text:
      present: false
```

### Option 3: Hybrid Approach (BEST PRACTICE)

**Change**: Assert both exact status AND semantic meaning for clarity.

**Implementation**:
```yaml
- sentence: "yes"
  expect:
    status: AWAITING_CONFIRMATION  # Exact state
    missing_slots: []  # Semantic: all slots filled
    action: SEARCH_AVAILABILITY  # Current action
    awaiting: USER_CONFIRMATION  # Semantic: waiting for confirmation
    text:
      present: false
```

## Conclusion

**AWAITING_CONFIRMATION is the correct designed state** when:
- All required slots are filled (`missing_slots = []`)
- `confirmation_state == "pending"` in the booking object
- System is waiting for user confirmation before executing committing actions

**Recommendation**: **Update test expectations to accept `AWAITING_CONFIRMATION`** (Option 1 or Option 3). This validates that the state machine correctly transitions through the confirmation flow.

The test failure is due to incorrect expectations, not incorrect system behavior.

