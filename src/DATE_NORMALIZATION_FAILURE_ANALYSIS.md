# Date Normalization Failure Analysis

## Summary
**9 test failures** all related to date normalization: tests expect relative date strings (e.g., "tomorrow", "this friday") but get normalized ISO dates (e.g., "2026-01-14", "2026-01-16").

## Failure Pattern

### All Failures Follow Same Pattern:
- **Expected**: `"date": "tomorrow"` or `"date": "this friday"` (relative string)
- **Got**: `"date": "2026-01-14"` or `"date": "2026-01-16"` (normalized ISO date)
- **Location**: `outcome.slots.date` in planning response
- **Stage**: All failures occur during planning phase (not execution)

## Specific Failures

### 1. `service_to_date_to_time` (Turn 2)
- **Expected**: `"date": "tomorrow"`
- **Got**: `"date": "2026-01-14"`
- **Context**: User says "tomorrow" in turn 2, date gets normalized to ISO

### 2. `service_to_date_to_time_massage` (Turn 2)
- **Expected**: `"date": "this friday"`
- **Got**: `"date": "2026-01-16"`
- **Context**: User says "this friday" in turn 2, date gets normalized to ISO

### 3. `service_to_date_to_time_facial` (Turn 2)
- **Expected**: `"date": "next monday"`
- **Got**: `"date": "2026-01-19"` (likely)
- **Context**: Similar pattern

### 4. `service_to_time_to_date` (Turn 2)
- **Expected**: Relative date string
- **Got**: Normalized ISO date
- **Context**: Time provided first, then date

### 5. `service_to_date_to_time_reverse_order` (Turn 2)
- **Expected**: Relative date string
- **Got**: Normalized ISO date
- **Context**: Different slot collection order

### 6. `all_slots_in_one_turn` (Turn 1)
- **Expected**: `"date": "tomorrow"`
- **Got**: `"date": "2026-01-14"`
- **Context**: All slots provided in single turn, date still normalized

### 7. `reservation_range_followup` (Turn 2)
- **Expected**: `"date_range": "march 10 to 15"`
- **Got**: `"date_range": "march 10 to 15"` ✅ (This one passes - date_range preserved)
- **Note**: This test actually passes! Date ranges are preserved as strings.

### 8. `reservation_date_range_not_applied_to_service` (Turn 2)
- **Expected**: Relative date string
- **Got**: Normalized ISO date
- **Context**: Reservation date range handling

### 9. `weak_luma_response_preserves_session` (Turn 1)
- **Expected**: Relative date string
- **Got**: Normalized ISO date
- **Context**: Weak Luma response handling

## Root Cause

1. **Normalization happens in Luma** (`luma/app/resolve_service.py`):
   - Relative dates like "tomorrow" are normalized to ISO format (e.g., "2026-01-14")
   - This happens when `slots["date"]` is set from calendar binder output

2. **Normalized dates flow to orchestrator**:
   - `luma_response.slots.date` contains normalized ISO date
   - `process_luma_response` copies this to `facts.slots.date`
   - Planning outcome uses `facts.slots.date` as `outcome.slots.date`

3. **Tests assert on user-facing format**:
   - Tests expect original relative strings in `outcome.slots.date`
   - But system provides normalized ISO dates

## Key Insight

**The invariant is violated**:
- **Availability execution** needs absolute dates (ISO format) ✅ (currently working)
- **Planning outcome/user-facing** needs relative strings ❌ (currently broken)

## Solution Design

### Preserve Original Date Strings

1. **Store both formats**:
   - `facts.slots.date` → Original relative string (for user-facing)
   - `time_constraint.start/end` → Normalized ISO date (for execution)

2. **Modify normalization points**:
   - When normalizing in `resolve_service.py`, preserve original `date_ref` in `slots.date`
   - Store normalized date in `time_constraint` only

3. **Update execution layer**:
   - Use `time_constraint.start` for availability API calls (already done)
   - Keep `facts.slots.date` as original string for display

## Next Steps

1. ✅ Instrumentation added to track normalization
2. ✅ Failure analysis complete
3. ⏭️ Modify normalization to preserve original date_refs
4. ⏭️ Update execution to use time_constraint instead of slots.date
5. ⏭️ Verify tests pass with preserved relative dates

