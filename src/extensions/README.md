# Extensions

Non-core behavior lives here — outside the booking kernel (`src/core`).

Core integrates via two hooks in `core/orchestration/api/message.py`:

| Hook | Status | Subpackage | Purpose |
|------|--------|------------|---------|
| Capability gate | `AWAITING_CAPABILITY` | `extensions.capabilities` | Pause booking until a gate is satisfied (payment, KYC) |
| Intent handler | `HANDLER_DELEGATED` | `extensions.handlers` | Answer non-booking intents in one shot (RAG) |

## capabilities (`extensions/capabilities`)

Multi-turn adapters with `start()` / `handle_input()` / `abort()`. Return gate facts
(e.g. `payment_satisfied: True`) that core merges before resuming execution.

Config: `core/config/capabilities.yaml`

See `capabilities/README.md` and `capabilities/contract.md`.

## Handlers (`extensions/handlers`)

Single-turn handlers with `handle()`. Return user-facing text; core does not resume
a booking step afterward.

Config: `core/config/intent_handlers.yaml`

## Bootstrap

```python
from extensions.bootstrap import register_default_extensions

register_default_extensions(organization_id=1)
```

Or register subpackages independently:

```python
from extensions.capabilities.bootstrap import register_default_adapters
from extensions.handlers.bootstrap import register_default_handlers
```
