# Dialogcart-Core Tests

## Layout

```
core/tests/
  harness/          # shared clients, runners (not collected by pytest)
  mocks/            # mock API payloads
  scenarios/
    execution/      # mock-booking YAML
    smoke/          # real-Luma YAML (RUN_REAL_LUMA_E2E)
  planning/         # PR gate — plan contract (65 tests)
  execution/        # mock booking + commit flows
  smoke/            # unified YAML smoke runner
  e2e/              # REST API tests (POST /api/message via TestClient)
  orchestration/    # unit tests
  session/
  rendering/
  routing/
  intents/
  workflows/
  test.py           # entry point
```

## Commands

```bash
# Planning (requires Luma for 29 scenario tests)
python core/tests/test.py --category planning

# Mock-booking execution (requires Luma)
python core/tests/test.py --category execution

# Full YAML smoke (requires Luma + flag)
RUN_REAL_LUMA_E2E=true python core/tests/test.py --category smoke

# Fast unit tests (no Luma)
python core/tests/test.py --category unit
python core/tests/test.py --category e2e

# Fine-grained
python core/tests/test.py --category orchestration
python core/tests/test.py --category rendering
python core/tests/test.py --list
```

## Tiers

| Tier | Folder | Purpose |
|------|--------|---------|
| Planning | `planning/` | status, action, missing_slots, multi-turn slots |
| Execution | `execution/` | handle_message + mock booking/availability |
| Smoke | `smoke/` | All YAML scenarios under `scenarios/smoke/` |
| E2E (API) | `e2e/` | POST /api/message via TestClient |
| Unit | orchestration, session, rendering, … | Fast mocked tests |
