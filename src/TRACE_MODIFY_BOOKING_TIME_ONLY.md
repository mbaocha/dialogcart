# Trace: modify_booking_time_only Test Failure

## Test Scenario
**Scenario 31: modify_booking_time_only**
- Turn 1: "change my booking to 3pm"
- Turn 2: "booking abc123"

## Expected Behavior
**Turn 1 Expected:**
- Intent: MODIFY_BOOKING
- Status: NEEDS_CLARIFICATION
- Missing slots: `["booking_id", "date"]` (time is provided, so it should NOT be missing)

**Turn 2 Expected:**
- Status: NEEDS_CLARIFICATION  
- Missing slots: `["booking_id"]` (date should still be missing from Turn 1)

## Actual Behavior (from test output)

### Turn 1: "change my booking to 3pm"

**Step 1: RAW_LUMA_RESPONSE (Line 28069)**
```json
{
  "trace": "RAW_LUMA_RESPONSE",
  "intent": {"confidence": 0.95, "name": "MODIFY_BOOKING"},
  "slots": null,  // ❌ PROBLEM: Luma returns null instead of {"time": "15:00"}
  "context": null,
  "text": "change my booking to 3pm"
}
```

**Step 2: Modification Context Detection (Line 28071-28077)**
```
[_compute_effective_collected_slots] raw_slots={}  // Empty because Luma returned null
[_compute_effective_collected_slots] MODIFY_BOOKING: has_time=False, has_date=False
// ❌ PROBLEM: Core detects no time because Luma didn't extract it
```

**Step 3: Required Slot Computation (Line 28103-28112)**
```
[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING path: collected_slots={}, modification_context={'modifying_time': False, 'modifying_date': False}
[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING: modification_context present but ambiguous -> using base_required_slots=['booking_id', 'date', 'time']
[MISSING_SLOTS] compute_missing_slots result: ['booking_id', 'date', 'time']
// ❌ PROBLEM: Core computes missing_slots=['booking_id', 'date', 'time'] 
//    Expected: ['booking_id', 'date'] (time should NOT be missing)
```

**Step 4: Final Result (Line 28628-28642)**
```json
{
  "expected": {
    "status": "NEEDS_CLARIFICATION",
    "missing_slots": ["booking_id", "date"]
  },
  "got": {
    "intent": "MODIFY_BOOKING",
    "status": "NEEDS_CLARIFICATION",
    "missing_slots": ["booking_id", "date", "time"],  // ❌ Extra "time" in missing_slots
    "slots": {}
  }
}
```

### Turn 2: "booking abc123"

**Expected:**
- Missing slots: `["booking_id"]` (date should persist from Turn 1)

**Actual (from Line 28626):**
- Missing slots: `["booking_id", "date"]` (time was incorrectly added in Turn 1)

## Root Cause Analysis

### Primary Issue: Luma Not Extracting Time
**Location:** `RAW_LUMA_RESPONSE` boundary
- **Expected:** Luma should extract `{"time": "15:00"}` from "change my booking to 3pm"
- **Actual:** Luma returns `slots: null`
- **Impact:** Core never receives the time slot, so it can't detect it's a time-only modification

### Secondary Issue: Core's Modification Context Detection
**Location:** `_compute_effective_collected_slots` function (line 1693-1710)
- **Problem:** Modification context detection depends on slots being present
- **Code:**
  ```python
  has_time = "time" in raw_slots and raw_slots.get("time") is not None
  has_date = "date" in raw_slots and raw_slots.get("date") is not None
  ```
- **Result:** Since `raw_slots={}` (empty), both `has_time` and `has_date` are `False`
- **Impact:** Core sets `modification_context = {'modifying_time': False, 'modifying_date': False}`

### Tertiary Issue: Required Slot Computation
**Location:** `compute_missing_slots` in `slot_contract.py` (line 97-151)
- **Problem:** When modification_context is ambiguous (both False), Core falls back to base required slots
- **Code Path:**
  ```python
  if modification_context:
      if has_time and not has_date:
          # Time-only modification
          required_slots = ['booking_id', 'date']  # ✅ Correct
      elif has_date and not has_time:
          # Date-only modification  
          required_slots = ['booking_id', 'time']  # ✅ Correct
      else:
          # Ambiguous or both - use base
          required_slots = ['booking_id', 'date', 'time']  # ❌ Wrong for time-only
  ```
- **Result:** Core uses `['booking_id', 'date', 'time']` instead of `['booking_id', 'date']`

## Execution Flow Divergence

```
1. User: "change my booking to 3pm"
   ↓
2. Luma API Call
   ↓
3. RAW_LUMA_RESPONSE: slots=null  ❌ DIVERGENCE POINT #1
   ↓
4. _compute_effective_collected_slots()
   - raw_slots = {} (empty)
   - has_time = False  ❌ DIVERGENCE POINT #2
   - modification_context = {'modifying_time': False, 'modifying_date': False}
   ↓
5. compute_missing_slots()
   - modification_context is ambiguous (both False)
   - Falls back to base: ['booking_id', 'date', 'time']  ❌ DIVERGENCE POINT #3
   ↓
6. Result: missing_slots=['booking_id', 'date', 'time']
   Expected: missing_slots=['booking_id', 'date']
```

## Where to Fix

### Option 1: Fix Luma (Recommended)
- **File:** Luma extraction/normalization code
- **Fix:** Ensure Luma extracts `{"time": "15:00"}` from "change my booking to 3pm"
- **Why:** This is the root cause - if Luma extracts time correctly, Core will work correctly

### Option 2: Fix Core's Fallback Logic (Workaround)
- **File:** `src/core/orchestration/api/slot_contract.py` (compute_missing_slots)
- **Fix:** When modification_context is ambiguous but intent is MODIFY_BOOKING, check if time/date exist in session slots or use a different heuristic
- **Why:** This is a workaround - Core shouldn't need to guess if Luma extracted correctly

## Verification

To verify the fix:
1. Run: `python -m core.tests.session.test_session 31`
2. Check Turn 1: `missing_slots` should be `["booking_id", "date"]` (not include "time")
3. Check Turn 2: `missing_slots` should be `["booking_id"]` (date persists from Turn 1)

