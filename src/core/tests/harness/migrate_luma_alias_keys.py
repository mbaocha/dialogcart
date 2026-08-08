"""Offline migration: string catalog aliases to numeric recording keys.

Copies existing Luma replay recordings under the filename implied by today's
tenant_context.aliases projection. Never calls Luma. Never deletes originals.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core.tests.harness.recording_luma_client import (
    build_recording_key,
    recording_filename,
)

DEFAULT_RECORDINGS_DIR = (
    Path(__file__).resolve().parents[1] / "e2e" / "recordings" / "luma"
)

# Proven test-catalog phrase -> numeric item id. Do not infer beyond this map.
PHRASE_TO_CATALOG_ID: Dict[str, int] = {
    "premium haircut": 1001,
    "haircut": 1001,
    "flexi haircut + prunning": 1002,
}

LEGACY_CANONICAL_VALUES = frozenset({"haircut"})

# Unique miss hashes from the 2026-08-08 replay run (src/e2e.out).
PRIOR_REPLAY_MISS_HASHES = frozenset(
    {
        "02d48b1687e497a2",
        "09c943a275fc9672",
        "0e6db51a72257e27",
        "1f18b05586549831",
        "2b2bee4f409f9a7b",
        "2ec2e9500586523d",
        "32b5a26709d90ed0",
        "35506fb174a5a72e",
        "3d75fe0e54e06df6",
        "42f913e3288b8b0d",
        "5639cd363ab92ad1",
        "5ed182afb5360efb",
        "6121f908a7741179",
        "699a0c4a8e05de54",
        "7237301a1181e92a",
        "73063957f0210f52",
        "73a92f6d0d483c3d",
        "77da75bba887120f",
        "79e0699c68b53fe4",
        "8e3096ffe5b40fa3",
        "8fa6a04541f8a03a",
        "9210a95bc8b6efbd",
        "947ed1a658a8ec17",
        "95166c130bb8ac3f",
        "976c33888b56e103",
        "988e3271e1c50a85",
        "9b0ee6c41e4b1859",
        "a20e7ed8cc4d5908",
        "a4a0e182784ee780",
        "a9d4c7c42918fbe9",
        "ae153f45b8bfb5aa",
        "aef661511bd83f37",
        "b05d036f466556d6",
        "b2e2606ea7fc9121",
        "b7806f5aeb23e6cb",
        "bddb61f0c5e11885",
        "c8c767b41841475b",
        "d60feba841ae7052",
        "d8417f9b34220901",
        "d89f9cae46fef309",
        "dca4379e0e1c875e",
        "da71f75f59c265f4",
        "e29d36babf58f93e",
        "eb68d5fbf8652434",
        "f4d855e63b10ac99",
        "f44684546b028280",
        "f838a42c51bb0024",
    }
)

CONTEXT_DRIFT_MISS_HASHES = frozenset(
    {
        "77da75bba887120f",
        "79e0699c68b53fe4",
        "bddb61f0c5e11885",
        "eb68d5fbf8652434",
    }
)


@dataclass
class MigrationPlanItem:
    source: Path
    dest_name: str
    dest: Path
    new_key: Dict[str, Any]
    response: Dict[str, Any]
    action: str


@dataclass
class MigrationReport:
    eligible: List[MigrationPlanItem] = field(default_factory=list)
    writes: List[MigrationPlanItem] = field(default_factory=list)
    skip_identical: List[MigrationPlanItem] = field(default_factory=list)
    collisions: List[MigrationPlanItem] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    ambiguous: List[Tuple[str, str]] = field(default_factory=list)
    invalid: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def recovered_hashes(self) -> List[str]:
        names = {item.dest_name[:-5] for item in self.writes}
        names.update(item.dest_name[:-5] for item in self.skip_identical)
        names.update(item.dest_name[:-5] for item in self.collisions)
        return sorted(names)


def _legacy_value_ok(value: Any, target_id: int) -> bool:
    if value == target_id:
        return True
    if isinstance(value, str) and value.isdigit() and int(value) == target_id:
        return True
    if isinstance(value, str) and value.strip().lower() in LEGACY_CANONICAL_VALUES:
        return True
    return False


def migrate_aliases(
    aliases: Mapping[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return (new_aliases, error). error set means ambiguous / unmappable."""
    if not isinstance(aliases, Mapping) or not aliases:
        return None, "no aliases"

    migrated: Dict[str, Any] = {}
    changed = False
    for phrase, value in aliases.items():
        lookup = str(phrase).strip().lower()
        target = PHRASE_TO_CATALOG_ID.get(lookup)
        if target is None:
            return None, f"unknown alias phrase {phrase!r}"
        if not _legacy_value_ok(value, target):
            return None, (
                f"unproven alias value for {phrase!r}: {value!r} "
                f"(expected legacy 'haircut' or {target})"
            )
        migrated[phrase] = target
        if value != target:
            changed = True
    if not changed:
        return None, None
    return migrated, None


def migrate_recording_key(
    key: Mapping[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(key, Mapping):
        return None, "missing key"
    tenant = key.get("tenant_context")
    if not isinstance(tenant, dict):
        return None, "missing tenant_context"
    aliases = tenant.get("aliases")
    migrated, err = migrate_aliases(aliases if isinstance(aliases, Mapping) else {})
    if err:
        return None, err
    if migrated is None:
        return None, None
    new_tenant = {**tenant, "aliases": migrated}
    rebuilt = build_recording_key(
        text=str(key.get("text") or ""),
        domain=str(key.get("domain") or "service"),
        timezone=str(key.get("timezone") or "UTC"),
        tenant_context=new_tenant,
        conversation_context=key.get("conversation_context"),
        test_now=key.get("test_now") if key.get("test_now") else None,
    )
    return rebuilt, None


def _responses_identical(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, default=str) == json.dumps(
        right, sort_keys=True, default=str
    )


def plan_migration(recordings_dir: Path) -> MigrationReport:
    report = MigrationReport()
    if not recordings_dir.is_dir():
        report.invalid.append((str(recordings_dir), "recordings directory missing"))
        return report

    dest_claims: Dict[str, List[MigrationPlanItem]] = {}

    for path in sorted(recordings_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.invalid.append((path.name, f"unreadable: {exc}"))
            continue
        key = payload.get("key")
        response = payload.get("response")
        if not isinstance(key, dict) or not isinstance(response, dict):
            report.invalid.append((path.name, "missing key or response"))
            continue
        new_key, err = migrate_recording_key(key)
        if err:
            report.ambiguous.append((path.name, err))
            continue
        if new_key is None:
            report.unchanged.append(path.name)
            continue

        dest_name = recording_filename(new_key)
        recomputed = recording_filename(
            build_recording_key(
                text=new_key["text"],
                domain=new_key["domain"],
                timezone=new_key["timezone"],
                tenant_context=new_key["tenant_context"],
                conversation_context=new_key["conversation_context"],
            )
        )
        if dest_name != recomputed:
            report.invalid.append((path.name, "filename recompute mismatch"))
            continue
        if dest_name == path.name:
            report.unchanged.append(path.name)
            continue

        dest = recordings_dir / dest_name
        item = MigrationPlanItem(
            source=path,
            dest_name=dest_name,
            dest=dest,
            new_key=new_key,
            response=response,
            action="write",
        )
        dest_claims.setdefault(dest_name, []).append(item)
        report.eligible.append(item)

    for dest_name, items in dest_claims.items():
        dest = recordings_dir / dest_name
        dest_exists = dest.is_file()
        existing_response: Any = None
        if dest_exists:
            try:
                existing_payload = json.loads(dest.read_text(encoding="utf-8"))
                existing_response = existing_payload.get("response")
            except (OSError, json.JSONDecodeError):
                for item in items:
                    item.action = "collision"
                    report.collisions.append(item)
                continue

        source_same = all(
            _responses_identical(items[0].response, other.response)
            for other in items[1:]
        )
        if dest_exists:
            dest_matches = isinstance(existing_response, dict) and all(
                _responses_identical(existing_response, item.response)
                for item in items
            )
            if dest_matches and source_same:
                for item in items:
                    item.action = "skip_identical"
                    report.skip_identical.append(item)
                continue
            for item in items:
                item.action = "collision"
                report.collisions.append(item)
            continue

        if not source_same:
            for item in items:
                item.action = "collision"
                report.collisions.append(item)
            continue

        winner = items[0]
        winner.action = "write"
        report.writes.append(winner)
        for extra in items[1:]:
            extra.action = "skip_identical"
            report.skip_identical.append(extra)

    return report


def apply_migration(report: MigrationReport) -> List[Path]:
    """Write non-colliding copies. Existing dest collisions are left untouched."""
    written: List[Path] = []
    for item in report.writes:
        payload = {"key": item.new_key, "response": item.response}
        text = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
        item.dest.write_text(text, encoding="utf-8")
        stored = json.loads(item.dest.read_text(encoding="utf-8"))
        stored_name = recording_filename(stored["key"])
        if stored_name != item.dest.name:
            raise RuntimeError(
                f"Post-write key recompute failed: {item.dest.name} != {stored_name}"
            )
        written.append(item.dest)
    return written


def format_report(report: MigrationReport, *, applied: bool = False) -> str:
    recovered = report.recovered_hashes
    recovered_misses = sorted(h for h in recovered if h in PRIOR_REPLAY_MISS_HASHES)
    unrecovered = sorted(PRIOR_REPLAY_MISS_HASHES - set(recovered_misses))
    lines = [
        "Luma alias-key migration " + ("APPLIED" if applied else "DRY-RUN"),
        f"eligible sources: {len(report.eligible)}",
        f"writes (new copies): {len(report.writes)}",
        f"skip identical dest: {len(report.skip_identical)}",
        f"unchanged (already numeric or no-op): {len(report.unchanged)}",
        f"ambiguous/unmappable: {len(report.ambiguous)}",
        f"invalid: {len(report.invalid)}",
        f"collisions: {len(report.collisions)}",
        f"responses reused (writes + identical dest): "
        f"{len(report.writes) + len(report.skip_identical)}",
        f"expected recovered prior-miss hashes: {len(recovered_misses)}",
        f"prior misses still unrecovered: {len(unrecovered)}",
        f"context-drift misses (not targeted): {sorted(CONTEXT_DRIFT_MISS_HASHES)}",
    ]
    if recovered_misses:
        lines.append("recovered miss hashes:")
        lines.extend(f"  {h}" for h in recovered_misses)
    if unrecovered:
        lines.append("unrecovered prior miss hashes:")
        lines.extend(f"  {h}" for h in unrecovered)
    if report.collisions:
        lines.append("collisions:")
        for item in report.collisions:
            lines.append(f"  {item.source.name} -> {item.dest_name}")
    if report.ambiguous:
        lines.append("ambiguous/unmappable (first 30):")
        for name, reason in report.ambiguous[:30]:
            lines.append(f"  {name}: {reason}")
        extra = len(report.ambiguous) - 30
        if extra > 0:
            lines.append(f"  ... +{extra} more")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recordings-dir",
        type=Path,
        default=DEFAULT_RECORDINGS_DIR,
        help="E2E luma recordings directory",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write new recording copies (default is dry-run)",
    )
    args = parser.parse_args(argv)
    report = plan_migration(args.recordings_dir.resolve())
    print(format_report(report, applied=False))
    if not args.apply:
        return 0
    written = apply_migration(report)
    print(format_report(report, applied=True))
    print(f"wrote {len(written)} new recording file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
