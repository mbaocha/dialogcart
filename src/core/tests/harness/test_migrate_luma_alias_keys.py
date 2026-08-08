"""Offline alias-key migration: deterministic copies, no live Luma."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.tests.harness.migrate_luma_alias_keys import (
    PHRASE_TO_CATALOG_ID,
    apply_migration,
    migrate_aliases,
    migrate_recording_key,
    plan_migration,
)
from core.tests.harness.recording_luma_client import (
    build_recording_key,
    recording_filename,
)


def _payload(text: str, aliases: dict, response: dict | None = None) -> dict:
    key = build_recording_key(
        text=text,
        domain="service",
        timezone="UTC",
        tenant_context={"aliases": aliases, "booking_mode": "service"},
        conversation_context={},
    )
    return {"key": key, "response": response or {"intent": {"name": "CREATE_APPOINTMENT"}}}


def _write(directory: Path, payload: dict) -> Path:
    path = directory / recording_filename(payload["key"])
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_phrase_map_is_restricted_to_proven_catalog_ids():
    assert PHRASE_TO_CATALOG_ID == {
        "premium haircut": 1001,
        "haircut": 1001,
        "flexi haircut + prunning": 1002,
    }


def test_migrate_aliases_maps_legacy_haircut_canonical_only():
    migrated, err = migrate_aliases(
        {
            "premium haircut": "haircut",
            "flexi haircut + prunning": "haircut",
        }
    )
    assert err is None
    assert migrated == {
        "premium haircut": 1001,
        "flexi haircut + prunning": 1002,
    }


def test_migrate_aliases_rejects_unproven_canonical_and_unknown_phrase():
    _, err_value = migrate_aliases({"premium haircut": "beauty_and_wellness.haircut"})
    assert err_value is not None
    _, err_phrase = migrate_aliases({"executive oil change": 26})
    assert err_phrase is not None


def test_migrate_recording_key_preserves_non_alias_identity():
    key = {
        "text": "book haircut",
        "domain": "service",
        "timezone": "UTC",
        "tenant_context": {
            "aliases": {
                "premium haircut": "haircut",
                "flexi haircut + prunning": "haircut",
            },
            "booking_mode": "service",
        },
        "conversation_context": {"last_intent": "CREATE_APPOINTMENT"},
    }
    new_key, err = migrate_recording_key(key)
    assert err is None
    assert new_key is not None
    assert new_key["text"] == "book haircut"
    assert new_key["conversation_context"] == {"last_intent": "CREATE_APPOINTMENT"}
    assert new_key["tenant_context"]["aliases"]["premium haircut"] == 1001
    assert recording_filename(new_key) == recording_filename(
        build_recording_key(
            text=new_key["text"],
            domain=new_key["domain"],
            timezone=new_key["timezone"],
            tenant_context=new_key["tenant_context"],
            conversation_context=new_key["conversation_context"],
        )
    )


def test_plan_and_apply_copies_without_deleting_source(tmp_path: Path):
    source_payload = _payload(
        "book haircut",
        {"premium haircut": "haircut", "flexi haircut + prunning": "haircut"},
        {"intent": {"name": "CREATE_APPOINTMENT"}, "facts": {"service_id": None}},
    )
    source = _write(tmp_path, source_payload)
    dest_name = recording_filename(
        migrate_recording_key(source_payload["key"])[0]
    )

    dry = plan_migration(tmp_path)
    assert dry.collisions == []
    assert len(dry.writes) == 1
    assert dry.writes[0].dest_name == dest_name
    assert not (tmp_path / dest_name).exists()

    written = apply_migration(dry)
    assert len(written) == 1
    assert source.is_file()
    dest = tmp_path / dest_name
    assert dest.is_file()
    stored = json.loads(dest.read_text(encoding="utf-8"))
    assert stored["response"] == source_payload["response"]
    assert recording_filename(stored["key"]) == dest_name
    assert stored["key"]["tenant_context"]["aliases"]["premium haircut"] == 1001


def test_identical_dest_is_not_overwritten(tmp_path: Path):
    response = {"intent": {"name": "CREATE_APPOINTMENT"}, "marker": 1}
    source_payload = _payload(
        "book me a premium haircut",
        {"premium haircut": "haircut", "flexi haircut + prunning": "haircut"},
        response,
    )
    _write(tmp_path, source_payload)
    new_key, _ = migrate_recording_key(source_payload["key"])
    assert new_key is not None
    dest_payload = {"key": new_key, "response": response}
    dest = _write(tmp_path, dest_payload)

    before = dest.read_text(encoding="utf-8")
    report = plan_migration(tmp_path)
    assert report.writes == []
    assert report.collisions == []
    assert report.skip_identical
    apply_migration(report)
    assert dest.read_text(encoding="utf-8") == before


def test_collision_when_dest_response_differs(tmp_path: Path):
    source_payload = _payload(
        "premium",
        {"premium haircut": "haircut", "flexi haircut + prunning": "haircut"},
        {"intent": {"name": "CREATE_APPOINTMENT"}, "marker": "old"},
    )
    _write(tmp_path, source_payload)
    new_key, _ = migrate_recording_key(source_payload["key"])
    assert new_key is not None
    _write(
        tmp_path,
        {"key": new_key, "response": {"intent": {"name": "CREATE_APPOINTMENT"}, "marker": "new"}},
    )
    report = plan_migration(tmp_path)
    assert report.collisions
    apply_migration(report)
    kept = json.loads((tmp_path / recording_filename(new_key)).read_text(encoding="utf-8"))
    assert kept["response"]["marker"] == "new"
