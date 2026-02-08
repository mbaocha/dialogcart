# Capabilities Module

## Overview

The capabilities module provides an adapter interface for external capabilities
(payment, KYC, consent, verification, etc.) that integrate with DialogCart core
via the capability gate mechanism.

## Architecture

```
┌─────────────┐
│   Core      │  Emits: status="AWAITING_CAPABILITY", active_capability="payment"
└──────┬──────┘
       │
       ▼
┌─────────────────────┐
│ Capability Runner   │  Routes to adapter, manages adapter lifecycle
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│ Capability Adapter  │  Handles capability-specific logic
│ (e.g., Payment)     │  Returns: completed, text, facts
└─────────────────────┘
```

## Adapter Interface

See `base.py` for the complete interface definition.

### Key Methods

1. **`start(context)`** - Called on first activation
2. **`handle_input(user_input, context)`** - Called for each user message
3. **`abort(reason, context)`** - Called on interruption

### Response Contract

Adapters return `AdapterResponse` with:
- `completed: bool` - Whether capability is finished
- `text: Optional[str]` - Prompt to show user (optional)
- `facts: Dict[str, Any]` - Facts to merge into session

## Adapter Rules

### ✅ Adapters MAY:

- **Ask capability-specific questions**
  - "How do I complete this capability?"
  - "Please enter payment method"
  - "Please verify your identity"

- **Manage local, temporary state**
  - State is adapter-owned
  - Short-lived (scoped to capability session)
  - Stored separately from core session

- **Resolve to boolean gate facts**
  ```json
  {
    "payment_satisfied": true,
    "kyc_verified": true,
    "consent_given": true
  }
  ```

### ❌ Adapters MUST NOT:

- **Ask booking questions**
  - ❌ "What date would you like?"
  - ❌ "Which service do you want?"
  - ❌ "What time works for you?"

- **Set global status**
  - ❌ Cannot set `status = "READY"` or `"NEEDS_CLARIFICATION"`
  - ✅ Core controls status based on facts

- **Touch core state directly**
  - ❌ Cannot modify `intent_name`, `slots`, `status`
  - ✅ Can only return facts for merging

- **Render outside active window**
  - ❌ Cannot render when `active_capability` is None
  - ✅ Only render when core emits `AWAITING_CAPABILITY`

- **Persist core session data**
  - ❌ Cannot write to core session store
  - ✅ Can manage adapter-local state only

## Adapter State Handling

### Local State Rules

Adapters may maintain **internal state**, but:

- **State is adapter-owned**
  - Stored separately from core session
  - Adapter-specific storage mechanism (in-memory, Redis, etc.)

- **State is short-lived**
  - Scoped to `(user_id, capability)` tuple
  - Cleared when capability completes or aborts

- **State is NOT part of core session**
  - Core session contains: `intent_name`, `slots`, `missing_slots`, `status`, `active_capability`
  - Adapter state is separate and opaque to core

### State Storage

Adapters choose their own storage mechanism:

- **In-memory** (for simple adapters)
  ```python
  _state: Dict[str, Dict[str, Any]] = {}  # {user_id: {capability_state}}
  ```

- **Redis** (for distributed adapters)
  ```python
  redis.set(f"adapter:{capability}:{user_id}", state_json, ex=3600)
  ```

- **Database** (for persistent adapters)
  ```python
  db.save_adapter_state(user_id, capability, state)
  ```

## Capability Runner Contract

The capability runner is a thin orchestrator that:

1. **Detects capability activation**
   ```python
   if core.status == "AWAITING_CAPABILITY":
       active_capability = core.active_capability  # e.g., "payment"
   ```

2. **Routes to adapter**
   ```python
   adapter = registry[active_capability]
   if first_activation:
       response = adapter.start(context)
   else:
       response = adapter.handle_input(user_input, context)
   ```

3. **Manages adapter lifecycle**
   - Persists adapter-local state
   - Merges `AdapterResponse.facts` into session context
   - Clears `active_capability` when `completed == True`

4. **Handles abort scenarios**
   ```python
   if intent_changed or user_cancelled:
       adapter.abort(reason, context)
       clear_active_capability()
   ```

### Runner Responsibilities

- ✅ Route user input to adapter
- ✅ Persist adapter-local state
- ✅ Merge `AdapterResponse.facts` back into session
- ✅ Clear `active_capability` when `completed == True`
- ✅ Handle abort scenarios (intent change, cancel, timeout)

### Core is NOT Involved

- ❌ Core does NOT know about adapter implementation
- ❌ Core does NOT manage adapter state
- ❌ Core does NOT render adapter prompts
- ✅ Core only emits `AWAITING_CAPABILITY` and reads facts

## Integration Flow

### 1. Core Emits Capability Gate

```json
{
  "status": "AWAITING_CAPABILITY",
  "active_capability": "payment",
  "awaiting": "CAPABILITY"
}
```

### 2. Runner Activates Adapter

```python
adapter = registry["payment"]
response = adapter.start(context={
    "user_id": "user123",
    "session_slots": {...},
    "session_facts": {...}
})
```

### 3. Adapter Returns Response

```python
AdapterResponse(
    completed=False,
    text="Please select payment method: credit card or PayPal",
    facts={}
)
```

### 4. User Provides Input

```
User: "credit card"
```

### 5. Runner Routes to Adapter

```python
response = adapter.handle_input("credit card", context)
# Returns: AdapterResponse(completed=False, text="Enter card number", facts={"payment_method": "credit_card"})
```

### 6. Adapter Completes

```python
response = adapter.handle_input("4111 1111 1111 1111", context)
# Returns: AdapterResponse(completed=True, text="Payment confirmed", facts={"payment_satisfied": True})
```

### 7. Runner Merges Facts

```python
session_facts["payment_satisfied"] = True
clear_active_capability()  # Set to None
```

### 8. Core Proceeds

Core reads `payment_satisfied: True` from facts and proceeds with execution.

## Example Adapter Facts

### Payment Adapter

```json
{
  "payment_satisfied": true,
  "payment_method": "credit_card",
  "payment_reference": "txn_12345",
  "payment_amount": 100.00
}
```

### KYC Adapter

```json
{
  "kyc_verified": true,
  "kyc_level": "basic",
  "kyc_reference": "kyc_abc123"
}
```

### Consent Adapter

```json
{
  "consent_given": true,
  "consent_type": "marketing",
  "consent_timestamp": "2024-01-15T10:30:00Z"
}
```

## Capability Completion

When an adapter returns `completed=True`, the capability runner should:

1. Merge `AdapterResponse.facts` into session context
2. Clear `active_capability` from session (set to None)
3. Allow core to proceed (core will re-evaluate status based on facts)

Core will then:
- Re-run planning with updated facts
- Check if capability gate is satisfied (e.g., `payment_satisfied == True`)
- Proceed to next step or emit new `AWAITING_CAPABILITY` if another capability is needed

## Error Handling

### Adapter Errors

If an adapter raises an exception:

1. Runner should catch and log the error
2. Runner should call `adapter.abort(reason="error", context)`
3. Runner should clear `active_capability`
4. Core will handle the error state (likely `NEEDS_CLARIFICATION`)

### Timeout Handling

If a capability times out:

1. Runner should call `adapter.abort(reason="timeout", context)`
2. Runner should clear `active_capability`
3. Core will re-evaluate status (likely `NEEDS_CLARIFICATION`)

## Testing Adapters

Adapters should be tested independently of core:

```python
def test_payment_adapter():
    adapter = PaymentAdapter()
    
    # Test start
    response = adapter.start(context={"user_id": "test"})
    assert response.completed == False
    assert "payment" in response.text.lower()
    
    # Test input handling
    response = adapter.handle_input("credit card", context)
    assert response.completed == False
    assert response.facts["payment_method"] == "credit_card"
    
    # Test completion
    response = adapter.handle_input("4111 1111 1111 1111", context)
    assert response.completed == True
    assert response.facts["payment_satisfied"] == True
```

## Next Steps

1. Implement capability runner (orchestrator)
2. Implement registry for adapter lookup
3. Implement example adapters (payment, KYC, etc.)
4. Add integration tests with core

