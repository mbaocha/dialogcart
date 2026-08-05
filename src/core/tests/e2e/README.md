# Core E2E tests

Multi-turn conversation tests against the Core HTTP `/api/message` path.

Booking conversation ownership and Covered/Missing inventory:
[`BOOKING_CONVERSATION_SPEC.md`](BOOKING_CONVERSATION_SPEC.md).

## Philosophy

E2E replays real production `/resolve` responses. Handwritten NLU payloads are not used.

The **only** intentional difference between production and E2E is:

| Environment | NLU boundary |
|-------------|--------------|
| Production | Live Luma (`/resolve`) |
| E2E | `RecordingLumaClient` (replayed `/resolve`) |

Everything after the NLU boundary (session merge, planner, execution, renderer) must execute identically to production.

## Single reference clock

Relative dates (`tomorrow`, weekdays, …) must resolve against one instant shared by Core E2E fixtures and NLU test bootstrap.

| Symbol | Source |
|--------|--------|
| `TEST_NOW` / `TEST_NOW_ISO` / `FROZEN_TIME` | `core.tests.harness.test_clock` |

Consumers:

- E2E fixtures (`FROZEN_TIME` via `framework/conversation.py`)
- `run.py` (sets `LUMA_TEST_NOW` for NLU test bootstrap only)
- `LumaClient.resolve` (sends body `test_now` when env or argument is set)
- `RecordingLumaClient` (forwards `test_now` on **live** miss/recache only; **cache keys omit `test_now`**)

Production NLU (`python -m nlu.api` without `LUMA_TEST_NOW`) uses wall clock. Production `LumaClient` omits `test_now` when the env is unset.

For deterministic live/recache relative dates, start NLU with:

```bash
python run.py
```

## RecordingLumaClient

Composition wrapper used by all E2E booking fixtures:

```text
RecordingLumaClient
      ↓
TestLumaClient   (injects test catalog aliases)
      ↓
LumaClient       (HTTP POST /resolve)
```

Wired in `framework/fixtures.py` via `_wire_booking_deps` for:

- `booking_conversation`
- `paginated_booking_conversation`
- `build_recorded_bundle` (scenario runners)

**Behaviour**

| Mode | What happens |
|------|----------------|
| Default | Lookup recording → **on hit return recorded payload and never call inner**; on miss call live `/resolve`, save raw JSON, return it |
| `--recache-luma` | Bypass cache for the **default** E2E recordings corpus only → live `/resolve` → overwrite → return. Custom dirs (e.g. unit-test `tmp_path`) still honor hits. |

Recordings live under:

```text
core/tests/e2e/recordings/luma/<sha256(key)[:16]>.json
```

Each file stores `{ "key": {...}, "response": <raw /resolve JSON> }`. Responses are never reshaped; planner-only fields are never injected. Recording keys do not include `test_now`.

**CLI**

```bash
python core/tests/test.py --category e2e
python core/tests/test.py --category e2e --recache-luma
# or
pytest core/tests/e2e --recache-luma
```

`--recache-luma` sets `DIALOGCART_RECACHE_LUMA=1`.

## Availability mocks

Fixture params in `E2E_FIXTURE_PARAMS` configure **mocked availability slot layouts only**. They do not fabricate NLU responses.

## Markers

- `@pytest.mark.live_luma` — skips when Live Luma is unreachable (`live_luma_available()`). Required for cache-miss / recache runs.
