# Core E2E tests

Multi-turn conversation tests against the Core HTTP `/api/message` path.

## NLU clients

### RecordingLumaClient (preferred for live `/resolve`)

Composition wrapper used by live booking fixtures:

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

**Behaviour**

| Mode | What happens |
|------|----------------|
| Default | Lookup recording → replay on hit; on miss call live `/resolve`, save raw JSON, return it |
| `--recache-luma` | Ignore cache → live `/resolve` → overwrite recording → return |

Recordings live under:

```text
core/tests/e2e/recordings/luma/<sha256(key)[:16]>.json
```

Each file stores `{ "key": {...}, "response": <raw /resolve JSON> }`. Responses are never reshaped; planner-only fields are never injected.

**CLI**

```bash
python core/tests/test.py --category e2e
python core/tests/test.py --category e2e --recache-luma
# or
pytest core/tests/e2e --recache-luma
```

`--recache-luma` sets `DIALOGCART_RECACHE_LUMA=1`.

**When to use**

- Any E2E that should exercise production `/resolve` shapes
- Replacing handwritten NLU fixtures over time

**Opt in** (other fixtures):

```python
RecordingLumaClient(TestLumaClient(test_aliases=...))
```

**Limitation:** Core `LumaClient` does not send `test_now` on the wire. Relative dates (`tomorrow`) can drift with the NLU clock unless the NLU process is started with a fixed `LUMA_TEST_NOW`.

### ScriptedLumaClient (still appropriate when)

Use handwritten resolve bodies when you need:

- Deterministic Core/planner branches without live NLU
- Forced AVAILABILITY / correction shapes that are hard to elicit live
- Explicit NLU post-process harnesses (`NluServiceResolutionScriptedLumaClient`)

Scripted clients short-circuit before HTTP and **do not** use the recording cache. Prefer production-shaped payloads (`intent`, `facts`, `time_constraint`, …) — avoid inventing Core-only fields such as `date_proposal` in fake `/resolve` bodies.

### TestLumaClient alone

Thin live client that only injects catalog aliases. Prefer wrapping with `RecordingLumaClient` for E2E so runs become deterministic after the first live miss.

## Markers

- `@pytest.mark.live_luma` — skips when Live Luma is unreachable (`live_luma_available()`).
