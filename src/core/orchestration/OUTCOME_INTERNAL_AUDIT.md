# Outcome Internal Audit: Fields Written and API Expectations

> **Status (2026-06):** Partially implemented. `build_outcome_from_decision()` exists in
> `orchestrator.py`. Public API still exposes `outcome` in `MessageResponse`. Capability
> runner consolidated to `core/orchestration/api/capability_boundary.py` (invoked from
> `message.py` only). Line references below predate orchestrator/turn_planner split.

## Objective
Identify all places where:
1. Fields are written to `outcome` during planning/rendering
2. Those fields are expected at API level (tests, API endpoints, session persistence)

## Current State: `outcome` is EXTERNAL
- `outcome` is included in `MessageResponse` schema (line 62 in `message.py`)
- Tests assert on `result["outcome"]["status"]`, `result["outcome"]["active_capability"]`, etc.
- API endpoint preserves `outcome` in response (lines 257-274 in `message.py`)
- `outcome` contains control-plane state that clients need

## Target State: `outcome` is INTERNAL
- `outcome` may exist during planning/rendering (internal artifact)
- `outcome` MUST NOT be returned directly or partially
- No consumer outside `handle_message()` may rely on `outcome`
- All API fields must be at top level

---

## 1. Fields Written to `outcome` in `orchestrator.py`

### 1.1 Control-Plane Fields (Status, Intent, Slots, Facts)

#### Location: `build_outcome_from_decision()` (lines 162-183)
```python
outcome = {
    "intent_name": decision.get("intent_name", ""),
    "status": plan.get("status", "NEEDS_CLARIFICATION"),
    "stage": plan.get("stage"),
    "action": plan.get("action"),
    "plan": plan_obj,
    "slots": slots,
    "missing_slots": missing_slots,
    "blocked_actions": plan.get("blocked_actions", []),
    "allowed_actions": plan.get("allowed_actions", []),
    "awaiting": plan.get("awaiting"),
    "facts": facts
}
if plan.get("active_capability"):
    outcome["active_capability"] = plan.get("active_capability")
```
**Fields Written:**
- `intent_name`
- `status`
- `stage`
- `action`
- `plan`
- `slots`
- `missing_slots`
- `blocked_actions`
- `allowed_actions`
- `awaiting`
- `facts`
- `active_capability` (conditional)

#### Location: `_build_response()` (lines 603-634)
```python
outcome = {
    "intent_name": intent_name,
    "status": plan_status,
    "stage": plan.get("stage"),
    "action": plan.get("action"),
    "slots": plan.get("slots", {}),
    "missing_slots": plan.get("missing_slots", []),
    "facts": plan.get("facts", {}),
    "plan": plan
}
if plan.get("active_capability"):
    outcome["active_capability"] = plan["active_capability"]
if runner_result and runner_result.facts:
    outcome["facts"].update(runner_result.facts)
```
**Fields Written:**
- `intent_name`
- `status`
- `stage`
- `action`
- `slots`
- `missing_slots`
- `facts` (merged with runner_result.facts)
- `plan`
- `active_capability` (conditional)

#### Location: `handle_message()` - PATH 1 Capability Rendering (lines 3488-3490)
```python
if populated_plan.get("active_capability"):
    result["outcome"]["active_capability"] = populated_plan["active_capability"]
```
**Fields Written:**
- `active_capability`

#### Location: `handle_message()` - PATH 1 Capability Rendering (lines 3700-3703)
```python
result["outcome"]["text"] = render_result.text
result["outcome"]["ui_actions"] = render_result.ui_actions
result["outcome"]["ui_hint"] = runner_result.ui_hint
result["outcome"]["ui_payload"] = runner_result.ui_payload
```
**Fields Written:**
- `text` (presentation field)
- `ui_actions` (presentation field)
- `ui_hint` (presentation field)
- `ui_payload` (presentation field)

#### Location: `handle_message()` - PATH 1 Capability Facts Merge (lines 3718-3722)
```python
if runner_result.facts:
    if "facts" not in result["outcome"]:
        result["outcome"]["facts"] = {}
    if not isinstance(result["outcome"]["facts"], dict):
        result["outcome"]["facts"] = {}
    result["outcome"]["facts"].update(runner_result.facts)
```
**Fields Written:**
- `facts` (merged with runner_result.facts)

#### Location: `handle_message()` - Initial Result Construction (lines 3043-3056)
```python
result = {
    "success": True,
    "outcome": {
        "intent_name": intent_name,
        "stage": stage,
        "action": action,
        "missing_slots": missing_slots,
        "slots": outcome_slots,
        "status": plan_status,
        "plan": populated_plan,
        "facts": outcome_facts
    }
}
```
**Fields Written:**
- `intent_name`
- `stage`
- `action`
- `missing_slots`
- `slots`
- `status`
- `plan`
- `facts`

#### Location: `handle_message()` - READY Status (lines 4046-4056)
```python
result = {
    "success": True,
    "outcome": {
        "status": "READY",
        "intent_name": intent_name,
        **planning_outcome,
        "facts": facts,
        "plan": plan
    }
}
```
**Fields Written:**
- `status`
- `intent_name`
- `facts`
- `plan`
- Plus fields from `planning_outcome` (slots, missing_slots, executable_actions)

#### Location: `handle_message()` - NEEDS_CLARIFICATION (lines 2093-2094, 2207-2208, 2326-2327)
```python
outcome = {
    "intent_name": session_intent_str,
    "stage": session_stage,
    ...
}
```
**Fields Written:**
- `intent_name`
- `stage`
- (other clarification fields)

### 1.2 Presentation Fields (Text, UI Actions)

#### Location: `_build_response()` (lines 627-634)
```python
if render_result.get("text"):
    outcome["text"] = render_result["text"]
if render_result.get("ui_actions"):
    outcome["ui_actions"] = render_result["ui_actions"]
if render_result.get("ui_hint"):
    outcome["ui_hint"] = render_result["ui_hint"]
if render_result.get("ui_payload"):
    outcome["ui_payload"] = render_result["ui_payload"]
```
**Fields Written:**
- `text` (presentation field)
- `ui_actions` (presentation field)
- `ui_hint` (presentation field)
- `ui_payload` (presentation field)

#### Location: `handle_message()` - PATH 1 Capability Rendering (lines 3700-3703)
```python
result["outcome"]["text"] = render_result.text
result["outcome"]["ui_actions"] = render_result.ui_actions
result["outcome"]["ui_hint"] = runner_result.ui_hint
result["outcome"]["ui_payload"] = runner_result.ui_payload
```
**Fields Written:**
- `text` (presentation field)
- `ui_actions` (presentation field)
- `ui_hint` (presentation field)
- `ui_payload` (presentation field)

---

## 2. Fields Expected at API Level (Tests)

### 2.1 Test: `test_core_capability_payment_e2e.py`

#### Assertions on `outcome`:
- Line 228-234: `outcome1 = result1.get("outcome")`, `assert outcome1 is not None`
- Line 233: `status1 = outcome1.get("status")`
- Line 234: `active_capability1 = outcome1.get("active_capability")`
- Line 238-240: `facts1 = outcome1.get("facts", {})`
- Line 267: `assert outcome1.get("active_capability") == "payment"`
- Line 294: `"session_facts": outcome1.get("facts", {})`
- Line 332: `assert outcome1.get("active_capability") == "payment"`
- Line 361: `core_outcome=outcome1`
- Line 433: `"session_facts": outcome1.get("facts", {})`
- Line 457: `core_outcome=outcome1`
- Line 532-533: `outcome3 = result3.get("outcome")`
- Line 538: `facts3 = outcome3.get("facts", {})`
- Line 540: `assert "payment_satisfied" in facts3`
- Line 568-569: `outcome4 = result4.get("outcome")`
- Line 572: `facts4 = outcome4.get("facts", {})`
- Line 574: `assert "payment_satisfied" in facts4`
- Line 582: `active_capability4 = outcome4.get("active_capability")`
- Line 583: `assert active_capability4 is None`
- Line 587: `status4 = outcome4.get("status")`
- Line 588: `assert status4 != "AWAITING_CAPABILITY"`
- Line 707-712: `outcome = result.get("outcome")`, `outcome_status = outcome.get("status")`, `outcome_active_capability = outcome.get("active_capability")`

**Fields Expected:**
- `status`
- `active_capability`
- `facts` (including `payment_satisfied`)
- `outcome` dict itself (not None)

### 2.2 Test: `test_orchestrator_invariants.py`

#### Assertions on `outcome`:
- Line 145-148: `outcome = result.get("outcome")`, `assert outcome is not None`
- Line 233-236: `outcome = result.get("outcome")`, `assert outcome is not None`

**Fields Expected:**
- `outcome` dict itself (not None)
- Note: This test explicitly does NOT assert on `outcome.text` or `outcome.ui_actions` (good!)

### 2.3 Test: `test_orchestrator_e2e.py`

#### Assertions on `outcome`:
- Line 317-325: `outcome = result.get("outcome", {})`, `outcome.get("type")`, `outcome.get("booking_code")`, `outcome.get("status")`, `outcome.get("template_key")`, `outcome.get("data")`
- Line 354: `"outcome_type": result.get("outcome", {}).get("type", "UNKNOWN")`

**Fields Expected:**
- `type`
- `booking_code`
- `status`
- `template_key`
- `data`

### 2.4 Test: `test_non_core_intent_passthrough.py`

#### Assertions on `outcome`:
- Line 37-41: `assert result["outcome"]["status"] == "NON_CORE_INTENT"`, `assert result["outcome"]["intent_name"] == "PAYMENT"`, `assert "facts" in result["outcome"]`, `assert "slots" in result["outcome"]["facts"]`
- Line 137-138: `assert result["outcome"]["intent_name"] == "PAYMENT"`, `assert result["outcome"]["status"] == "NON_CORE_INTENT"`
- Line 151-152: `assert result["outcome"]["intent_name"] == "BOOKING_INQUIRY"`, `assert result["outcome"]["status"] == "NON_CORE_INTENT"`
- Line 165-166: `assert result["outcome"]["intent_name"] == "AVAILABILITY"`, `assert result["outcome"]["status"] == "NON_CORE_INTENT"`

**Fields Expected:**
- `status`
- `intent_name`
- `facts` (including `slots`)

### 2.5 Test: `test_interactive.py`

#### Assertions on `outcome`:
- Line 244-261: `outcome = result.get("outcome", {})`, `outcome.get("type")`, `outcome.get("booking_code")`, `outcome.get("status")`, `outcome.get("template_key")`, `outcome.get("data")`, `outcome.get("booking")`

**Fields Expected:**
- `type`
- `booking_code`
- `status`
- `template_key`
- `data`
- `booking`

---

## 3. Fields Expected at API Level (API Endpoints)

### 3.1 `message.py` - `post_message()` endpoint

#### Reads from `outcome`:
- Line 142-143: `outcome = result.get("outcome")`, `if outcome and isinstance(outcome, dict) and outcome.get("status") == "AWAITING_CAPABILITY"`
- Line 149: `"session_facts": outcome.get("facts", {})`
- Line 196: `result["outcome"] = outcome` (mutates outcome)
- Line 241-242: `if result.get("text") is None and result.get("outcome", dict()).get("text") is not None: result["text"] = result["outcome"]["text"]`
- Line 257-274: `internal_outcome = result.get("outcome")`, creates `response_outcome` copy, preserves `outcome.text`
- Line 297-301: `MessageResponse(outcome=response_outcome, ...)`
- Line 309-321: Logs `response.outcome.keys()`, `response.outcome.get("text")`

**Fields Expected:**
- `status` (for capability routing)
- `facts` (for capability context)
- `text` (promoted to top level)
- `outcome` dict itself (included in MessageResponse schema)

#### Schema Definition:
- Line 59-64: `class MessageResponse(BaseModel): outcome: Optional[dict] = None`

**Fields Expected:**
- `outcome` dict (entire dict is in schema)

---

## 4. Fields Expected at API Level (Session Persistence)

### 4.1 `session_merge.py` - `build_session_state_from_outcome()`

#### Reads from `outcome`:
- Function signature: `build_session_state_from_outcome(outcome, outcome_status, merged_luma_response, previous_session_state, user_id)`
- Reads: `outcome.get("intent_name")`, `outcome.get("status")`, `outcome.get("stage")`, `outcome.get("action")`, `outcome.get("slots")`, `outcome.get("missing_slots")`, `outcome.get("facts")`, `outcome.get("plan")`, `outcome.get("active_capability")`

**Fields Expected:**
- `intent_name`
- `status`
- `stage`
- `action`
- `slots`
- `missing_slots`
- `facts`
- `plan`
- `active_capability`

---

## 5. Summary: Fields Written vs. Expected

### 5.1 Control-Plane Fields (Must Move to Top Level)

| Field | Written To `outcome` | Expected at API Level | Current Location |
|-------|---------------------|----------------------|------------------|
| `status` | ✅ Yes | ✅ Yes (tests, API, session) | `outcome.status` |
| `active_capability` | ✅ Yes | ✅ Yes (tests, API) | `outcome.active_capability` |
| `intent_name` | ✅ Yes | ✅ Yes (tests, API, session) | `outcome.intent_name` |
| `stage` | ✅ Yes | ✅ Yes (tests, session) | `outcome.stage` |
| `action` | ✅ Yes | ✅ Yes (tests, session) | `outcome.action` |
| `slots` | ✅ Yes | ✅ Yes (tests, session) | `outcome.slots` |
| `missing_slots` | ✅ Yes | ✅ Yes (tests, session) | `outcome.missing_slots` |
| `facts` | ✅ Yes | ✅ Yes (tests, API, session) | `outcome.facts` |
| `plan` | ✅ Yes | ✅ Yes (tests, session) | `outcome.plan` |
| `awaiting` | ✅ Yes | ❓ Unknown | `outcome.awaiting` |
| `blocked_actions` | ✅ Yes | ❓ Unknown | `outcome.blocked_actions` |
| `allowed_actions` | ✅ Yes | ❓ Unknown | `outcome.allowed_actions` |

### 5.2 Presentation Fields (Already Promoted, But Also in `outcome`)

| Field | Written To `outcome` | Expected at API Level | Current Location |
|-------|---------------------|----------------------|------------------|
| `text` | ✅ Yes | ✅ Yes (top level) | `outcome.text` + `result.text` |
| `ui_actions` | ✅ Yes | ✅ Yes (top level) | `outcome.ui_actions` + `result.ui_actions` |
| `ui_hint` | ✅ Yes | ✅ Yes (top level) | `outcome.ui_hint` + `result.ui_hint` |
| `ui_payload` | ✅ Yes | ❓ Unknown | `outcome.ui_payload` |

### 5.3 Execution Fields (Legacy, May Not Be Needed)

| Field | Written To `outcome` | Expected at API Level | Current Location |
|-------|---------------------|----------------------|------------------|
| `type` | ❓ Unknown | ✅ Yes (tests) | `outcome.type` |
| `booking_code` | ❓ Unknown | ✅ Yes (tests) | `outcome.booking_code` |
| `template_key` | ❓ Unknown | ✅ Yes (tests) | `outcome.template_key` |
| `data` | ❓ Unknown | ✅ Yes (tests) | `outcome.data` |
| `booking` | ❓ Unknown | ✅ Yes (tests) | `outcome.booking` |

---

## 6. Critical Dependencies

### 6.1 API Endpoint (`message.py`)
- **Line 62**: `MessageResponse` schema includes `outcome: Optional[dict] = None`
- **Line 142-143**: Reads `outcome.get("status")` for capability routing
- **Line 149**: Reads `outcome.get("facts")` for capability context
- **Line 196**: Mutates `result["outcome"]` (capability completion)
- **Line 241-242**: Reads `outcome.get("text")` to promote to top level
- **Line 257-274**: Creates `response_outcome` copy, preserves `outcome.text`
- **Line 297-301**: Includes `outcome` in `MessageResponse`

### 6.2 Session Persistence (`session_merge.py`)
- **Function**: `build_session_state_from_outcome(outcome, ...)`
- **Reads**: All control-plane fields from `outcome`
- **Writes**: Session state from `outcome` fields

### 6.3 Tests
- **Payment E2E**: 20+ assertions on `outcome` fields
- **Orchestrator E2E**: Reads `outcome.type`, `outcome.booking_code`, etc.
- **Non-core intent**: Asserts on `outcome.status`, `outcome.intent_name`
- **Interactive**: Reads `outcome.type`, `outcome.booking_code`, etc.

---

## 7. Migration Strategy (Not Implemented Yet)

### 7.1 Phase 1: Identify All Consumers
- ✅ Complete (this audit)

### 7.2 Phase 2: Move Control-Plane Fields to Top Level
- Move `status`, `active_capability`, `intent_name`, `stage`, `action`, `slots`, `missing_slots`, `facts`, `plan` to top level
- Update `MessageResponse` schema to include these fields
- Update `build_session_state_from_outcome()` to read from top level
- Update tests to assert on top level

### 7.3 Phase 3: Remove `outcome` from API
- Remove `outcome` from `MessageResponse` schema
- Remove `outcome` from API endpoint logic
- Keep `outcome` as internal-only artifact in `handle_message()`

### 7.4 Phase 4: Update Tests
- Replace all `result["outcome"]["field"]` with `result["field"]`
- Remove all assertions on `outcome` dict itself

---

## 8. Files Requiring Changes

### 8.1 Core Orchestration
- `src/core/orchestration/orchestrator.py` - Remove `outcome` from return, move fields to top level
- `src/core/orchestration/api/message.py` - Remove `outcome` from schema, read from top level
- `src/core/orchestration/api/session_merge.py` - Update to read from top level instead of `outcome`

### 8.2 Tests
- `src/core/tests/e2e/test_core_capability_payment_e2e.py` - 20+ assertions to update
- `src/core/tests/orchestration/test_orchestrator_e2e.py` - Update assertions
- `src/core/tests/orchestration/test_non_core_intent_passthrough.py` - Update assertions
- `src/core/tests/orchestration/test_interactive.py` - Update assertions
- `src/core/tests/orchestration/test_orchestrator_invariants.py` - Already correct (doesn't assert on outcome fields)

---

## 9. Risk Assessment

### 9.1 High Risk
- **Session Persistence**: `build_session_state_from_outcome()` reads all fields from `outcome`
- **Payment E2E Test**: 20+ assertions on `outcome` fields
- **API Schema**: `MessageResponse` includes `outcome` in schema

### 9.2 Medium Risk
- **Capability Routing**: API endpoint reads `outcome.get("status")` for capability routing
- **Capability Context**: API endpoint reads `outcome.get("facts")` for capability context
- **Text Promotion**: API endpoint reads `outcome.get("text")` to promote to top level

### 9.3 Low Risk
- **Presentation Fields**: Already promoted to top level, but also exist in `outcome`
- **Legacy Fields**: `type`, `booking_code`, `template_key` may be legacy and not needed

---

## 10. Next Steps

1. **Do NOT fix yet** - This audit is complete
2. **Review with team** - Confirm which fields are truly needed at API level
3. **Create migration plan** - Detailed steps for moving fields to top level
4. **Update schema** - Add top-level fields to `MessageResponse`
5. **Update tests** - Replace `outcome` assertions with top-level assertions
6. **Remove `outcome`** - Make `outcome` internal-only



