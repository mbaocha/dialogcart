# Date Normalization Investigation

## Problem
9 test failures where relative dates (e.g., "tomorrow", "this friday") are being normalized to absolute dates (e.g., "2026-01-14") when tests expect the original relative strings to be preserved.

## Date Normalization Flow

### 1. Normalization Point: `luma/app/resolve_service.py`

**Location**: Lines 1461-1643 (facts.dates normalization) and multiple slots["date"] assignments

**Key Normalization Sites**:
- Line 1960: `slots["date"] = dates_list[0]` (from calendar_binder normalized dates)
- Line 1976: `slots["date"] = start_str.split("T")[0]` (from datetime_range)
- Line 2141: `slots["date"] = bound_date.strftime("%Y-%m-%d")` (from _bind_single_date)
- Line 2252: `slots["date"] = dates_list[0]` (from date_range normalization)
- Line 2653: `slots["date"] = dates_list[0]` (reservation dates)
- Line 2708: `slots["date"] = date_str` (from datetime_range)

**Normalization Rule** (Line 1588):
```python
# DATE NORMALIZATION RULE: Always normalize dates to ISO, never store raw tokens
```

### 2. Flow to Orchestrator

**Path**: `luma_response.slots` → `process_luma_response` → `facts.slots`

**Location**: `core/orchestration/nlu/luma_response_processor.py:1498`
```python
facts = {
    "slots": slots,  # slots comes from luma_response.slots (already normalized)
    "missing_slots": missing_slots,
    "context": effective_context
}
```

### 3. Usage in Execution

**Availability Search** (`SEARCH_AVAILABILITY`):
- **REQUIRES**: Absolute dates (ISO format) for API calls
- **Location**: `core/orchestration/execution/dispatcher.py:_execute_service_availability`
- Uses: `slots.get("date")` directly in availability client call

**Confirmation/Follow-up**:
- **REQUIRES**: Original relative strings for user-facing display
- **Location**: Planning outcome, session persistence, test assertions
- Uses: `facts.slots.date` in planning outcome

## Invariant

**Availability paths REQUIRE absolute dates** (for API execution)
**Confirmation/follow-up paths REQUIRE original relative strings** (for user-facing display)

## Design Split (Proposed)

1. **`facts.slots.date`** → User-facing, preserve original relative string
2. **`internal_time_constraint.start/end`** → Normalized, executable (already exists)
3. **`plan.slots.date`** → For execution, use normalized from time_constraint
4. **`facts.slots.date`** → For display/persistence, use original from date_refs

## Instrumentation Points

### Added logging at:

1. **`luma/app/resolve_service.py`** - When `slots["date"]` is set:
   - ✅ Line 1960: UNKNOWN_INTENT date normalization from binder
   - ✅ Line 1976: UNKNOWN_INTENT date extraction from datetime_range
   - ✅ Line 2141: UNKNOWN_INTENT date normalization via _bind_single_date
   - Logs: original date_ref, normalized ISO date, intent, stage (if available)

2. **`core/orchestration/nlu/luma_response_processor.py`** - When building facts:
   - ✅ Line 1513: Date value in facts.slots
   - Logs: date value in slots, whether it's ISO format, intent, plan.stage, plan.action

3. **`core/orchestration/execution/dispatcher.py`** - When using date for execution:
   - ✅ Line 412: Date value used for SEARCH_AVAILABILITY
   - Logs: date value used, whether normalized, action type

## Next Steps

1. ✅ Add instrumentation to track normalization
2. Run tests to collect normalization traces
3. Identify which code paths need absolute vs relative
4. Design split between user-facing (relative) and internal (absolute)
5. Implement preservation of original date_refs alongside normalized dates
