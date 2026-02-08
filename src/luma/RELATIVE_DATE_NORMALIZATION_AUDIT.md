# Relative Date Normalization Audit

## Summary

This audit confirms that relative date expressions (e.g., "tomorrow", "next friday", "this weekend", "next week") are parsed and normalized into concrete ISO dates (YYYY-MM-DD) using `LUMA_TEST_NOW`.

## Code Paths Responsible for Relative Date Normalization

### 1. Entry Point: `resolve_service.py`
- **Location**: `src/luma/app/resolve_service.py:484-504`
- **Function**: Reads `LUMA_TEST_NOW` environment variable
- **Purpose**: Provides deterministic `now` datetime for testing
- **Output**: `now` datetime passed to pipeline

```python
# Allow override via LUMA_TEST_NOW environment variable (for deterministic testing)
test_now = os.getenv("LUMA_TEST_NOW")
if test_now:
    now = datetime.fromisoformat(now_str)
```

### 2. Date Normalization: `calendar_binder.py`
- **Location**: `src/luma/calendar/calendar_binder.py:990-1077`
- **Function**: `_bind_single_date(date_str: str, now: datetime, tz: Any) -> Optional[datetime]`
- **Purpose**: Converts relative date strings to concrete ISO dates
- **Logic**:
  1. Checks relative date offsets via `_get_relative_date_offsets()` (line 1057-1061)
  2. Computes `bound_date = now + timedelta(days=offset_days)`
  3. Returns `bound_date.strftime("%Y-%m-%d")` (ISO format)
- **Relative Date Mapping**: `_get_relative_date_offsets()` → loads from config (e.g., "tomorrow" = +1 day)

### 3. Date Range Normalization: `calendar_binder.py`
- **Location**: `src/luma/calendar/calendar_binder.py:839-987`
- **Function**: `_bind_dates(date_refs: list, date_mode: str, now: datetime, tz: Any) -> Optional[Dict[str, str]]`
- **Purpose**: Converts date references to date ranges with ISO dates
- **Logic**:
  - For single_day: calls `_bind_single_date()` and returns `{"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}`
  - For range: calls `_bind_single_date()` for start/end dates
  - For flexible (week/weekend): computes Monday-Sunday or Saturday-Sunday using `now.weekday()` (lines 930-984)

### 4. Week/Weekend Special Handling: `calendar_binder.py`
- **Location**: `src/luma/calendar/calendar_binder.py:924-984`
- **Function**: `_bind_dates()` with `date_mode == DateMode.FLEXIBLE`
- **Purpose**: Handles "this week"/"next week" and "this weekend"/"next weekend"
- **Logic**:
  - Computes days until Monday/Saturday using `now.weekday()`
  - Adds 7 days for "next" modifier
  - Returns `{"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}`

### 5. Weekday Handling: `calendar_binder.py`
- **Location**: `src/luma/calendar/calendar_binder.py:1025-1054`
- **Function**: `_bind_single_date()` weekday pattern matching
- **Purpose**: Handles "this friday", "next monday", bare "friday"
- **Logic**:
  - Computes `days_ahead = (target_weekday - today_weekday) % 7`
  - Adds 7 days for "next" modifier
  - Returns `target_date.strftime("%Y-%m-%d")`

### 6. UNKNOWN Intent Date Normalization: `resolve_service.py`
- **Location**: `src/luma/app/resolve_service.py:2388-2504`
- **Function**: Date normalization for UNKNOWN intents (standalone temporal inputs)
- **Purpose**: Normalizes date_refs to ISO dates for facts extraction
- **Logic**: Reuses `_bind_single_date()` and `_bind_dates()` from calendar_binder

## Tests Validating Relative Dates

### 1. Booking Scenarios: `booking_scenarios.py`
- **Location**: `src/luma/tests/booking_scenarios.py`
- **Test Setup**: `TEST_NOW = "2026-01-13T10:00:00Z"` (Wednesday)
- **Validation**: All relative dates are expected as ISO dates in `facts.dates[]`
- **Examples**:
  - `"tomorrow"` → `"dates": ["2026-01-14"]` (line 194, 239, etc.)
  - `"next friday"` → `"dates": ["2026-01-23"]` (line 239)
  - `"this weekend"` → `"dates": ["2026-01-17", "2026-01-18"]` (line 1247)
  - `"next week"` → `"dates": ["2026-01-19", "2026-01-25"]` (line 1236)

### 2. Test Infrastructure: `test_luma.py`
- **Location**: `src/luma/tests/test_luma.py:21-32`
- **Setup**: Sets `os.environ["LUMA_TEST_NOW"] = TEST_NOW`
- **Validation**: Tests assert `facts.dates[]` contains ISO date strings

## Confirmation: No Semantic Logic

✅ **Pure Syntax-to-ISO Conversion**: Relative date normalization is purely syntactic:
- `_bind_single_date()` computes `now + offset` (no business logic)
- `_bind_dates()` wraps single date binding (no validation logic)
- Week/weekend handling uses `weekday()` arithmetic (no semantic interpretation)
- Weekday handling uses modulo arithmetic (no contextual reasoning)

✅ **No Semantic Dependencies**: Date normalization does NOT depend on:
- Intent classification
- Booking readiness
- Missing slots
- Clarification logic
- Business rules

✅ **Stateless**: Normalization depends only on:
- Input date string (syntax)
- `now` datetime (from `LUMA_TEST_NOW`)
- Timezone

## Protected Invariant

**INVARIANT**: Relative date expressions MUST be normalized to ISO date format (YYYY-MM-DD) using `LUMA_TEST_NOW` as the reference point. This is a pure syntactic transformation with no semantic interpretation.

## Files/Functions Summary

| File | Function | Line Range | Purpose |
|------|----------|------------|---------|
| `resolve_service.py` | `LUMA_TEST_NOW` parsing | 484-504 | Read test datetime |
| `calendar_binder.py` | `_bind_single_date()` | 990-1077 | Convert relative→ISO |
| `calendar_binder.py` | `_bind_dates()` | 839-987 | Convert date refs→ISO range |
| `calendar_binder.py` | `_get_relative_date_offsets()` | 86-88 | Load offset mappings |
| `entity_loading.py` | `load_relative_date_offsets()` | 1378-1400 | Parse config offsets |
| `booking_scenarios.py` | Test cases | Various | Validate ISO dates |
| `test_luma.py` | Test setup | 21-32 | Set `LUMA_TEST_NOW` |


