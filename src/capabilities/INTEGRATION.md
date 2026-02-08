# Capability Runner Integration Guide

## Overview

The Capability Runner is a thin orchestrator that sits **outside core** and drives
adapters when core emits `status == "AWAITING_CAPABILITY"`.

## Integration Point

The runner interposes between user input and core:

```
User Input
    ↓
Core (handle_message)
    ↓
IF status == "AWAITING_CAPABILITY"
    ↓
Capability Runner
    ↓
Adapter (start/handle_input)
    ↓
Runner Result
    ↓
IF passthrough == True
    ↓
Core (continue normal flow)
ELSE
    ↓
Show adapter text, wait for next input
```

## Usage Example

### 1. Initialize Runner

```python
from capabilities import CapabilityRunner, register_adapter
from capabilities.adapters.payment import PaymentAdapter

# Register adapters
payment_adapter = PaymentAdapter()
register_adapter(payment_adapter)

# Initialize runner
runner = CapabilityRunner()
```

### 2. Integrate with Core Handler

```python
def handle_user_message(user_input: str, user_id: str):
    # Step 1: Call core
    core_outcome = core.handle_message(
        text=user_input,
        user_id=user_id
    )
    
    # Step 2: Check if capability is active
    if core_outcome["status"] == "AWAITING_CAPABILITY":
        # Step 3: Route to runner
        context = {
            "user_id": user_id,
            "session_slots": core_outcome.get("slots", {}),
            "session_facts": core_outcome.get("facts", {})
        }
        
        runner_result = runner.handle(
            user_input=user_input,
            core_outcome=core_outcome,
            context=context
        )
        
        # Step 4: Process runner result
        if runner_result.passthrough:
            # Adapter completed or no capability active
            if runner_result.facts:
                # Merge facts into session
                merge_facts_into_session(user_id, runner_result.facts)
                # Clear active_capability
                clear_active_capability(user_id)
                # Re-enter core with same input
                return handle_user_message(user_input, user_id)
            else:
                # No capability active, return core outcome
                return core_outcome
        else:
            # Adapter is active, show prompt
            return {
                "status": "AWAITING_CAPABILITY",
                "text": runner_result.text,
                "active_capability": runner_result.active_capability
            }
    else:
        # Normal core flow
        return core_outcome
```

### 3. Handle Abort Scenarios

```python
def handle_intent_change(user_id: str, new_intent: str):
    # Get current core outcome
    core_outcome = get_current_core_outcome(user_id)
    
    # Abort capability if active
    if core_outcome.get("status") == "AWAITING_CAPABILITY":
        context = {
            "user_id": user_id,
            "session_slots": core_outcome.get("slots", {}),
            "session_facts": core_outcome.get("facts", {})
        }
        
        runner.abort(
            reason="intent_change",
            core_outcome=core_outcome,
            context=context
        )
        
        # Clear active_capability
        clear_active_capability(user_id)
```

## Runner Result Handling

### Passthrough = True

**When:**
- Adapter completed (`completed == True`)
- No capability active
- Adapter error occurred

**Action:**
- If `facts` is set → merge into session, clear `active_capability`, re-enter core
- If `facts` is None → return core outcome (normal flow)

### Passthrough = False

**When:**
- Adapter is active (`completed == False`)

**Action:**
- Show `text` to user (if any)
- Wait for next user input
- Do NOT send input to core yet

## Fact Merging

When adapter completes, merge facts into session context:

```python
def merge_facts_into_session(user_id: str, facts: Dict[str, Any]):
    session = get_session(user_id)
    session_facts = session.get("facts", {})
    
    # Merge adapter facts
    session_facts.update(facts)
    
    # Save session
    save_session(user_id, session)
```

**Important:**
- Facts are merged into `session.facts`, not `session.slots`
- Core will read facts on next turn
- Facts must follow naming convention: `<capability>_satisfied: True`

## State Management

### Adapter State

Adapter state is managed by runner and stored separately from core session:

```python
# Runner uses InMemoryStateStore by default
runner = CapabilityRunner()

# For distributed deployments, use custom store
from capabilities.runner import CapabilityRunner
from my_store import RedisStateStore

runner = CapabilityRunner(state_store=RedisStateStore())
```

### Core Session

Core session is managed by core (unchanged):

```python
# Core session structure (unchanged)
session = {
    "intent_name": "CREATE_APPOINTMENT",
    "slots": {...},
    "missing_slots": [...],
    "status": "AWAITING_CAPABILITY",
    "active_capability": "payment"  # Set by core, cleared by runner
}
```

## Error Handling

### Adapter Errors

If adapter raises exception:

1. Runner catches and logs error
2. Runner calls `adapter.abort(reason="error", context)`
3. Runner clears adapter state
4. Runner returns `passthrough=True` (no facts)
5. Core handles error state (likely `NEEDS_CLARIFICATION`)

### Registry Errors

If adapter not registered:

1. Runner logs error
2. Runner returns `passthrough=True` (no facts)
3. Core proceeds normally (capability gate not enforced)

## Testing

### Unit Test Runner

```python
def test_runner_first_activation():
    runner = CapabilityRunner()
    register_adapter(MockPaymentAdapter())
    
    core_outcome = {
        "status": "AWAITING_CAPABILITY",
        "active_capability": "payment"
    }
    context = {"user_id": "test", "session_slots": {}, "session_facts": {}}
    
    result = runner.handle(
        user_input=None,
        core_outcome=core_outcome,
        context=context
    )
    
    assert result.passthrough == False
    assert result.text is not None
    assert result.active_capability == "payment"
```

### Integration Test

```python
def test_capability_flow():
    # Setup
    runner = CapabilityRunner()
    register_adapter(PaymentAdapter())
    
    # First activation
    result = runner.handle(None, core_outcome, context)
    assert result.passthrough == False
    assert "payment" in result.text.lower()
    
    # User input
    result = runner.handle("credit card", core_outcome, context)
    assert result.passthrough == False
    
    # Completion
    result = runner.handle("4111 1111 1111 1111", core_outcome, context)
    assert result.passthrough == True
    assert result.facts["payment_satisfied"] == True
    assert result.active_capability is None
```

## Custom State Store

For distributed deployments, implement custom state store:

```python
from capabilities.runner import CapabilityRunner

class RedisStateStore:
    def __init__(self, redis_client):
        self.redis = redis_client
    
    def get(self, user_id: str, capability: str) -> Optional[Dict[str, Any]]:
        key = f"adapter:{capability}:{user_id}"
        data = self.redis.get(key)
        return json.loads(data) if data else None
    
    def set(self, user_id: str, capability: str, state: Dict[str, Any]) -> None:
        key = f"adapter:{capability}:{user_id}"
        self.redis.setex(key, 3600, json.dumps(state))  # 1 hour TTL
    
    def delete(self, user_id: str, capability: str) -> None:
        key = f"adapter:{capability}:{user_id}"
        self.redis.delete(key)

# Use custom store
runner = CapabilityRunner(state_store=RedisStateStore(redis_client))
```

## Best Practices

1. **Register adapters at startup**
   ```python
   def initialize_capabilities():
       register_adapter(PaymentAdapter())
       register_adapter(KycAdapter())
       register_adapter(ConsentAdapter())
   ```

2. **Handle abort on intent change**
   ```python
   if intent_changed:
       runner.abort("intent_change", core_outcome, context)
   ```

3. **Merge facts immediately on completion**
   ```python
   if runner_result.passthrough and runner_result.facts:
       merge_facts_into_session(user_id, runner_result.facts)
       clear_active_capability(user_id)
   ```

4. **Use distributed state store in production**
   ```python
   runner = CapabilityRunner(state_store=RedisStateStore(redis))
   ```

