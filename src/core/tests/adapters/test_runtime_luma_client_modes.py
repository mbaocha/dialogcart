"""Focused tests for runtime Luma live/record/replay selection."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from core.adapters.nlu.luma_client import LumaClient
from core.adapters.nlu.recording_luma_client import (
    LumaReplayMissError,
    RecordingLumaClient,
    build_recording_key,
    recording_filename,
)
from core.adapters.nlu.runtime_client import (
    DEFAULT_LUMA_MODE,
    DEFAULT_RUNTIME_RECORDINGS_DIR,
    build_runtime_luma_client,
    resolve_luma_mode,
    resolve_runtime_recordings_dir,
)
from core.tests.harness.recording_luma_client import (
    RECACHE_ENV,
    RecordingLumaClient as E2ERecordingLumaClient,
)


REQUEST = {
    "user_id": "user-is-not-part-of-key",
    "text": "book premium",
    "domain": "service",
    "timezone": "Europe/London",
    "tenant_context": {"aliases": {"service": ["premium"]}},
    "conversation_context": {
        "last_intent": "CREATE_APPOINTMENT",
        "slots": {"date": "2026-08-10"},
    },
}
RESPONSE = {"intent": {"name": "CREATE_APPOINTMENT"}, "facts": {}}


class CaptureLive:
    def __init__(self) -> None:
        self.resolve_calls: list[Dict[str, Any]] = []
        self.notify_calls: list[Dict[str, Any]] = []

    def resolve(self, **kwargs: Any) -> Dict[str, Any]:
        self.resolve_calls.append(kwargs)
        return RESPONSE

    def notify_execution(self, **kwargs: Any) -> Dict[str, Any]:
        self.notify_calls.append(kwargs)
        return {"success": True}


def _recording_path(directory: Path) -> Path:
    key_args = {
        key: REQUEST[key]
        for key in (
            "text",
            "domain",
            "timezone",
            "tenant_context",
            "conversation_context",
        )
    }
    return directory / recording_filename(build_recording_key(**key_args))


def _seed(directory: Path) -> Path:
    path = _recording_path(directory)
    path.write_text(
        json.dumps(
            {
                "key": build_recording_key(
                    **{
                        key: REQUEST[key]
                        for key in (
                            "text",
                            "domain",
                            "timezone",
                            "tenant_context",
                            "conversation_context",
                        )
                    }
                ),
                "response": RESPONSE,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_e2e_import_is_compatible_and_key_format_has_exact_parity() -> None:
    assert issubclass(E2ERecordingLumaClient, RecordingLumaClient)
    key = build_recording_key(
        **{
            name: REQUEST[name]
            for name in (
                "text",
                "domain",
                "timezone",
                "tenant_context",
                "conversation_context",
            )
        }
    )
    assert key == {
        "text": "book premium",
        "domain": "service",
        "timezone": "Europe/London",
        "tenant_context": {"aliases": {"service": ["premium"]}},
        "conversation_context": {
            "last_intent": "CREATE_APPOINTMENT",
            "slots": {"date": "2026-08-10"},
        },
    }
    assert recording_filename(key) == "9619ea1d34c6f124.json"
    assert "user_id" not in key
    assert "test_now" not in key


@pytest.mark.parametrize("mode", ["record", "replay"])
def test_record_and_replay_hits_do_not_call_live_or_write(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(Path, "write_text", lambda *args, **kwargs: pytest.fail("write"))
    live = CaptureLive()
    client = RecordingLumaClient(live, recordings_dir=tmp_path, mode=mode)
    assert client.resolve(**REQUEST) == RESPONSE
    assert live.resolve_calls == []
    assert client.last_cache_hit is True


def test_e2e_custom_directory_still_replays_under_recache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setenv(RECACHE_ENV, "1")
    live = CaptureLive()
    client = E2ERecordingLumaClient(live, recordings_dir=tmp_path)
    assert client.resolve(**REQUEST) == RESPONSE
    assert live.resolve_calls == []


def test_record_miss_calls_live_once_and_writes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = CaptureLive()
    writes = []
    original_write_text = Path.write_text

    def write_once(path: Path, *args: Any, **kwargs: Any):
        writes.append(path)
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_once)
    client = RecordingLumaClient(live, recordings_dir=tmp_path, mode="record")
    assert client.resolve(**REQUEST) == RESPONSE
    assert len(live.resolve_calls) == 1
    assert writes == [_recording_path(tmp_path)]
    assert json.loads(writes[0].read_text(encoding="utf-8"))["response"] == RESPONSE


def test_replay_miss_is_sanitized_and_has_no_live_call_or_mutation(tmp_path: Path) -> None:
    live = CaptureLive()
    client = RecordingLumaClient(live, recordings_dir=tmp_path, mode="replay")
    with pytest.raises(LumaReplayMissError) as caught:
        client.resolve(**REQUEST)
    diagnostic = str(caught.value)
    assert (
        recording_filename(
            build_recording_key(
                **{
                    key: REQUEST[key]
                    for key in (
                        "text",
                        "domain",
                        "timezone",
                        "tenant_context",
                        "conversation_context",
                    )
                }
            )
        )
        in diagnostic
    )
    assert REQUEST["text"] not in diagnostic
    assert "CREATE_APPOINTMENT" not in diagnostic
    assert live.resolve_calls == []
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_live_mode_never_touches_recordings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    live = CaptureLive()
    monkeypatch.setattr(Path, "is_file", lambda *args: pytest.fail("recording read"))
    monkeypatch.setattr(Path, "write_text", lambda *args, **kwargs: pytest.fail("recording write"))
    selected = build_runtime_luma_client(
        mode="live", recordings_dir=tmp_path, live_client=live  # type: ignore[arg-type]
    )
    assert selected is live
    assert selected.resolve(**REQUEST) == RESPONSE
    assert len(live.resolve_calls) == 1


def test_runtime_selection_defaults_and_constructs_correct_clients(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DIALOGCART_LUMA_MODE", raising=False)
    monkeypatch.delenv("DIALOGCART_LUMA_RECORDING_DIR", raising=False)
    assert resolve_luma_mode() == DEFAULT_LUMA_MODE == "live"
    assert resolve_runtime_recordings_dir() == DEFAULT_RUNTIME_RECORDINGS_DIR

    live = CaptureLive()
    record = build_runtime_luma_client(
        mode="record", recordings_dir=tmp_path, live_client=live  # type: ignore[arg-type]
    )
    replay = build_runtime_luma_client(
        mode="replay", recordings_dir=tmp_path, live_client=live  # type: ignore[arg-type]
    )
    assert isinstance(record, RecordingLumaClient) and record.mode == "record"
    assert isinstance(replay, RecordingLumaClient) and replay.mode == "replay"


def test_invalid_mode_fails_clearly() -> None:
    with pytest.raises(ValueError, match="DIALOGCART_LUMA_MODE.*record\\|replay\\|live"):
        resolve_luma_mode("surprise")


def test_runtime_module_has_no_test_clock_dependency() -> None:
    import core.adapters.nlu.recording_luma_client as recording_module
    import core.adapters.nlu.runtime_client as runtime_module

    source = inspect.getsource(recording_module) + inspect.getsource(runtime_module)
    assert "core.tests" not in source
    assert "test_clock" not in source
    assert "TEST_NOW_ISO" not in source
