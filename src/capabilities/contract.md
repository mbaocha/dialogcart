# Capability Adapter Contract

## Contract Summary

This document defines the **explicit contract** between DialogCart core and
capability adapters.

## Core → Adapter Contract

### When Core Emits AWAITING_CAPABILITY

Core emits the following when a capability gate is active:

```json
{
  "status": "AWAITING_CAPABILITY",
  "active_capability": "payment",
  "awaiting": "CAPABILITY"
}
```

**Core guarantees:**
- `status` will be `"AWAITING_CAPABILITY"` when capability is active
- `active_capability` will contain the capability name (e.g., `"payment"`)
- `awaiting` will be `"CAPABILITY"` when capability is active
- Core will NOT proceed with execution until capability is satisfied

**Core does NOT:**
- ❌ Know about adapter implementation
- ❌ Manage adapter state
- ❌ Render adapter prompts
- ❌ Call adapter methods directly

## Adapter → Core Contract

### Adapter Response Contract

Adapters return `AdapterResponse` with:

```python
@dataclass
class AdapterResponse:
    completed: bool                # Is capability finished?
    text: Optional[str] = None     # Prompt to show user (optional)
    facts: Dict[str, Any] = {}     # Facts to merge into session
```

**Adapter guarantees:**
- `completed == True` means capability is satisfied
- `facts` contains only booleans/references (no secrets)
- `text` is optional (some steps may be silent)
- `facts` will include `<capability>_satisfied: True` when complete

**Adapter does NOT:**
- ❌ Set global status (`READY`, `NEEDS_CLARIFICATION`)
- ❌ Modify core session fields (`intent_name`, `slots`, `status`)
- ❌ Ask booking questions (date, time, service)
- ❌ Render outside active window

## Fact Contract

### Fact Naming Convention

Facts must follow the pattern:

```
<capability>_satisfied: bool
```

Examples:
- `payment_satisfied: True`
- `kyc_verified: True`
- `consent_given: True`

### Fact Content Rules

**Facts MAY contain:**
- ✅ Booleans (`payment_satisfied: True`)
- ✅ References/IDs (`payment_reference: "txn_12345"`)
- ✅ Non-sensitive metadata (`payment_method: "credit_card"`)

**Facts MUST NOT contain:**
- ❌ Secrets (passwords, tokens, PII)
- ❌ Core session fields (`intent_name`, `slots`, `status`)
- ❌ Intent-specific data (dates, times, services)

### Fact Merging

Facts are merged into session context (not core session state):

```python
# Before merge
session_facts = {
    "slots": {...},
    "missing_slots": [...]
}

# After merge (from adapter)
session_facts = {
    "slots": {...},
    "missing_slots": [...],
    "payment_satisfied": True,  # ← Merged from adapter
    "payment_reference": "txn_12345"
}
```

## State Contract

### Adapter State

Adapters manage **local, temporary state**:

- **Ownership:** Adapter-owned (not core)
- **Lifetime:** Short-lived (scoped to capability session)
- **Storage:** Adapter-specific (in-memory, Redis, etc.)
- **Scope:** `(user_id, capability)` tuple

### Core State

Core manages **persistent session state**:

- **Ownership:** Core-owned
- **Lifetime:** Session-scoped (20-30 minutes TTL)
- **Storage:** Core session store (Redis/in-memory)
- **Scope:** `user_id` only

**Separation:**
- Adapter state is **NOT** part of core session
- Core state is **NOT** accessible to adapters (read-only via context)

## Lifecycle Contract

### Activation

1. Core emits `AWAITING_CAPABILITY` with `active_capability="payment"`
2. Runner detects activation and calls `adapter.start(context)`
3. Adapter returns `AdapterResponse(completed=False, text="...", facts={})`
4. Runner shows `text` to user

### Input Handling

1. User provides input: `"credit card"`
2. Runner calls `adapter.handle_input("credit card", context)`
3. Adapter processes input and returns `AdapterResponse`
4. Runner shows `text` (if any) and merges `facts`

### Completion

1. Adapter returns `AdapterResponse(completed=True, facts={"payment_satisfied": True})`
2. Runner merges `facts` into session context
3. Runner clears `active_capability` (set to None)
4. Core re-evaluates status (reads `payment_satisfied: True` from facts)
5. Core proceeds with execution

### Abort

1. Core detects abort condition (intent change, cancel, timeout)
2. Runner calls `adapter.abort(reason, context)`
3. Adapter cleans up local state
4. Runner clears `active_capability`
5. Core re-evaluates status

## Context Contract

### Context Structure

Adapters receive context dictionary:

```python
context = {
    "user_id": str,              # User identifier
    "session_slots": Dict,        # Current session slots (read-only)
    "session_facts": Dict,        # Current session facts (read-only)
    # ... adapter-specific context
}
```

**Context rules:**
- ✅ Adapters can read context (read-only)
- ❌ Adapters cannot modify context
- ✅ Adapters can add adapter-specific context fields

### Context Access

Adapters access context via method parameters:

```python
def start(self, context: Dict[str, Any]) -> AdapterResponse:
    user_id = context["user_id"]
    slots = context["session_slots"]  # Read-only
    facts = context["session_facts"]  # Read-only
```

## Error Contract

### Adapter Errors

If adapter raises exception:

1. Runner catches and logs error
2. Runner calls `adapter.abort(reason="error", context)`
3. Runner clears `active_capability`
4. Core handles error state (likely `NEEDS_CLARIFICATION`)

### Timeout Errors

If capability times out:

1. Runner calls `adapter.abort(reason="timeout", context)`
2. Runner clears `active_capability`
3. Core re-evaluates status (likely `NEEDS_CLARIFICATION`)

## Validation Contract

### Input Validation

Adapters validate user input:

- ✅ Validate within capability scope
- ✅ Return error prompts if invalid
- ❌ Do NOT validate booking data (date, time, service)

### Fact Validation

Runner validates adapter facts:

- ✅ Check fact naming convention (`<capability>_satisfied`)
- ✅ Check fact content (no secrets, no core fields)
- ❌ Do NOT validate fact semantics (adapter responsibility)

## Testing Contract

### Adapter Testing

Adapters must be testable independently:

```python
def test_adapter():
    adapter = PaymentAdapter()
    context = {"user_id": "test", "session_slots": {}, "session_facts": {}}
    
    # Test start
    response = adapter.start(context)
    assert isinstance(response, AdapterResponse)
    assert response.completed == False
    
    # Test input
    response = adapter.handle_input("credit card", context)
    assert response.completed == False or True
    
    # Test abort
    adapter.abort("test", context)  # Should not raise
```

### Integration Testing

Integration tests verify contract:

```python
def test_capability_integration():
    # Core emits AWAITING_CAPABILITY
    core_response = handle_message("book appointment", user_id="test")
    assert core_response["status"] == "AWAITING_CAPABILITY"
    assert core_response["active_capability"] == "payment"
    
    # Runner activates adapter
    adapter = registry["payment"]
    response = adapter.start(context)
    
    # Adapter returns response
    assert isinstance(response, AdapterResponse)
    assert response.completed == False
    
    # User provides input
    response = adapter.handle_input("credit card", context)
    
    # Adapter completes
    assert response.completed == True
    assert response.facts["payment_satisfied"] == True
```

## Compliance

All adapters must comply with this contract. Non-compliance will result in:
- Integration failures
- State corruption
- Security vulnerabilities

Adapters should be validated against this contract before integration.

