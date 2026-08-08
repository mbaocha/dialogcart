"""Test-only recording wrapper around a live Luma client.

Records and replays raw ``/resolve`` JSON for deterministic E2E runs.
Does not modify production ``LumaClient`` or reshape NLU responses.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, Literal, Optional, Protocol

from core.tests.harness.test_clock import LUMA_TEST_NOW_ENV, TEST_NOW_ISO

RECACHE_ENV = "DIALOGCART_RECACHE_LUMA"
RECORD_ENV = "DIALOGCART_RECORD_LUMA"
LumaRecordingMode = Literal["replay", "record", "recache"]

_DEFAULT_RECORDINGS_DIR = (
    Path(__file__).resolve().parents[1] / "e2e" / "recordings" / "luma"
)


class _LumaResolveClient(Protocol):
    def resolve(
        self,
        user_id: str,
        text: str,
        domain: str = "service",
        timezone: str = "UTC",
        tenant_context: Optional[Dict[str, Any]] = None,
        conversation_context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]: ...

    def notify_execution(
        self, user_id: str, booking_id: str, domain: str = "service"
    ) -> Dict[str, Any]: ...


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def recache_luma_enabled() -> bool:
    return _truthy_env(RECACHE_ENV)


def recording_luma_mode() -> LumaRecordingMode:
    """Return the single E2E recording mode selected by explicit opt-ins."""
    record = _truthy_env(RECORD_ENV)
    recache = recache_luma_enabled()
    if record and recache:
        raise ValueError(
            f"Conflicting Luma recording modes: {RECORD_ENV} and {RECACHE_ENV} "
            "cannot both be enabled"
        )
    if recache:
        return "recache"
    if record:
        return "record"
    return "replay"


def live_luma_calls_enabled() -> bool:
    return recording_luma_mode() in {"record", "recache"}


class LumaRecordingMissError(RuntimeError):
    """Raised when replay-only E2E execution has no matching recording."""


def build_recording_key(
    *,
    text: str,
    domain: str,
    timezone: str,
    tenant_context: Optional[Dict[str, Any]],
    conversation_context: Optional[Dict[str, Any]],
    test_now: Optional[str] = None,
) -> Dict[str, Any]:
    """Canonical request fields used for cache identity (excludes user_id).

    ``test_now`` is accepted for callers/tests but is **not** included in the
    key by default policy of :class:`RecordingLumaClient` (stable absolute-date
    recording filenames). Kept on the signature for explicit opt-in tools.
    """
    key: Dict[str, Any] = {
        "text": text,
        "domain": domain,
        "timezone": timezone,
        "tenant_context": tenant_context if tenant_context is not None else {},
        "conversation_context": (
            conversation_context if conversation_context is not None else {}
        ),
    }
    if test_now:
        key["test_now"] = test_now
    return key


def recording_filename(key: Dict[str, Any]) -> str:
    payload = json.dumps(key, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{digest[:16]}.json"


def _resolve_test_now(
    explicit: Optional[str] = None,
) -> str:
    """Resolve the frozen clock for live ``/resolve`` fallback.

    Precedence: explicit kwarg → ``LUMA_TEST_NOW`` env → canonical
    ``TEST_NOW_ISO``. This client is test-only; live fallback must never use
    wall clock (named-month year rollover would drift after ``TEST_NOW``).
    """
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    env_now = os.getenv(LUMA_TEST_NOW_ENV, "").strip()
    if env_now:
        return env_now
    return TEST_NOW_ISO


class RecordingLumaClient:  # noqa: N801
    __test__ = False

    """Composition wrapper: replay by default; live only in explicit write modes.

    Cache hit invariant: when a recording file exists and is eligible for
    replay, return it and **never** call the inner client.

    A replay miss raises :class:`LumaRecordingMissError` without constructing a
    live client or touching the recordings directory. ``--record-luma`` enables
    live calls on misses. ``--recache-luma`` / ``DIALOGCART_RECACHE_LUMA``
    bypasses cache only for the
    default E2E recordings corpus (force live + overwrite). Custom
    ``recordings_dir`` (e.g. pytest ``tmp_path``) always honors hits so the
    replay invariant remains testable under a suite-level recache flag.

    Cache keys deliberately omit ``test_now`` so existing absolute-date
    recordings keep matching. On live miss / corpus recache, ``test_now`` is
    always forwarded to the inner client (explicit kwarg, ``LUMA_TEST_NOW``,
    or canonical ``TEST_NOW_ISO``).
    """

    def __init__(
        self,
        inner: Optional[_LumaResolveClient] = None,
        *,
        recordings_dir: Optional[Path] = None,
        live_client_factory: Optional[Callable[[], _LumaResolveClient]] = None,
        mode: Optional[LumaRecordingMode] = None,
    ):
        self._inner = inner
        self._live_client_factory = live_client_factory
        self.mode = mode or recording_luma_mode()
        if self.mode not in {"replay", "record", "recache"}:
            raise ValueError(f"Unsupported Luma recording mode: {self.mode!r}")
        self.recordings_dir = Path(recordings_dir or _DEFAULT_RECORDINGS_DIR)
        self.last_response: Optional[Dict[str, Any]] = None
        self.last_recording_path: Optional[Path] = None
        self.last_cache_hit: Optional[bool] = None

    def _bypass_cache_for_recache(self) -> bool:
        """Force live only for the shared E2E corpus under ``--recache-luma``."""
        if self.mode != "recache":
            return False
        try:
            return self.recordings_dir.resolve() == _DEFAULT_RECORDINGS_DIR.resolve()
        except OSError:
            return False

    def resolve(
        self,
        user_id: str,
        text: str,
        domain: str = "service",
        timezone: str = "UTC",
        tenant_context: Optional[Dict[str, Any]] = None,
        conversation_context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        test_now = _resolve_test_now(kwargs.get("test_now"))

        # Stable cache identity — do not include test_now in the key.
        key = build_recording_key(
            text=text,
            domain=domain,
            timezone=timezone,
            tenant_context=tenant_context,
            conversation_context=conversation_context,
            test_now=None,
        )
        path = self.recordings_dir / recording_filename(key)
        self.last_recording_path = path

        # Cache hit → replay only. Never invoke inner on this path.
        if path.is_file() and not self._bypass_cache_for_recache():
            recorded = json.loads(path.read_text(encoding="utf-8"))
            response = recorded.get("response")
            if not isinstance(response, dict):
                raise ValueError(f"Invalid Luma recording (missing response): {path}")
            self.last_cache_hit = True
            self.last_response = response
            return response

        if self.mode == "replay":
            self.last_cache_hit = False
            raise LumaRecordingMissError(
                f"Luma replay cache miss: {path}. Re-run with --record-luma "
                "to record missing responses, or --recache-luma to replace "
                "the shared E2E recordings."
            )

        # Explicit record miss (or default-corpus recache bypass) → live, then save.
        # Always pin the E2E reference clock so named-month dates stay stable.
        response = self._get_live_client().resolve(
            user_id=user_id,
            text=text,
            domain=domain,
            timezone=timezone,
            tenant_context=tenant_context,
            conversation_context=conversation_context,
            entity_schema=kwargs.get("entity_schema"),
            test_now=test_now,
        )
        self._save_recording(path, key, response)
        self.last_cache_hit = False
        self.last_response = response
        return response

    def notify_execution(
        self, user_id: str, booking_id: str, domain: str = "service"
    ) -> Dict[str, Any]:
        if self.mode == "replay":
            return {
                "success": False,
                "message": "Live Luma notification skipped in replay mode",
            }
        return self._get_live_client().notify_execution(
            user_id=user_id, booking_id=booking_id, domain=domain
        )

    def _get_live_client(self) -> _LumaResolveClient:
        if self._inner is None and self._live_client_factory is not None:
            self._inner = self._live_client_factory()
        if self._inner is None:
            raise RuntimeError(
                f"Luma mode {self.mode!r} requires an explicitly configured live client"
            )
        return self._inner

    def _save_recording(
        self, path: Path, key: Dict[str, Any], response: Dict[str, Any]
    ) -> None:
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        payload = {"key": key, "response": response}
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
