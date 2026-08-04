"""Assert RecordingLumaClient replays production /resolve fields without LLM."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from core.tests.harness.recording_luma_client import RecordingLumaClient

_RECORDINGS_DIR = (
    Path(__file__).resolve().parent / "recordings" / "luma"
)


def _production_resolve_fields(response: Dict[str, Any]) -> Dict[str, bool]:
    turn = response.get("turn") if isinstance(response.get("turn"), dict) else {}
    return {
        "intent": isinstance(response.get("intent"), dict)
        and bool((response.get("intent") or {}).get("name")),
        "facts": isinstance(response.get("facts"), dict),
        "temporal": isinstance(response.get("temporal"), dict),
        "turn.understanding": isinstance(turn.get("understanding"), str)
        and bool(turn.get("understanding")),
    }


def test_cached_resolve_recordings_include_production_fields():
    """At least one on-disk recording must carry the full production envelope."""
    assert _RECORDINGS_DIR.is_dir(), f"missing recordings dir: {_RECORDINGS_DIR}"
    complete = []
    for path in sorted(_RECORDINGS_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        response = payload.get("response")
        if not isinstance(response, dict):
            continue
        flags = _production_resolve_fields(response)
        if all(flags.values()):
            complete.append((path.name, payload.get("key", {}).get("text"), flags))
    assert complete, (
        "No cached /resolve recording includes intent+facts+temporal+"
        "turn.understanding — recache with live NLU "
        "(pytest --recache-luma) after upgrading recordings."
    )


def test_recording_luma_client_replays_without_inner_resolve(tmp_path):
    """Cache hit must return the stored body and never call the inner client."""

    class _BoomInner:
        def resolve(self, *args, **kwargs):
            raise AssertionError("inner resolve must not run on cache hit")

        def notify_execution(self, *args, **kwargs):
            raise AssertionError("notify_execution unused")

    key = {
        "text": "premium",
        "domain": "service",
        "timezone": "UTC",
        "tenant_context": {},
        "conversation_context": {},
    }
    response = {
        "intent": {"name": "CREATE_APPOINTMENT", "confidence": 0.9},
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": "premium haircut",
            "booking_id": None,
        },
        "temporal": {"mode": "none", "confidence": 0.0},
        "turn": {"understanding": "UNDERSTOOD"},
    }
    # Use the same filename scheme as RecordingLumaClient.
    from core.tests.harness.recording_luma_client import (
        build_recording_key,
        recording_filename,
    )

    path = tmp_path / recording_filename(build_recording_key(**key))
    path.write_text(
        json.dumps({"key": key, "response": response}, indent=2) + "\n",
        encoding="utf-8",
    )

    client = RecordingLumaClient(_BoomInner(), recordings_dir=tmp_path)
    out = client.resolve(
        user_id="e2e",
        text="premium",
        domain="service",
        timezone="UTC",
        tenant_context={},
        conversation_context={},
    )
    assert client.last_cache_hit is True
    assert out == response
    flags = _production_resolve_fields(out)
    assert all(flags.values()), flags
