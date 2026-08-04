"""Test-only recording wrapper around a live Luma client.

Records and replays raw ``/resolve`` JSON for deterministic E2E runs.
Does not modify production ``LumaClient`` or reshape NLU responses.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

RECACHE_ENV = "DIALOGCART_RECACHE_LUMA"

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
) -> Optional[str]:
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    env_now = os.getenv("LUMA_TEST_NOW", "").strip()
    return env_now or None


class RecordingLumaClient:  # noqa: N801
    __test__ = False

    """Composition wrapper: lookup/replay recordings; miss or recache → live.

    Cache hit invariant: when a recording file exists and is eligible for
    replay, return it and **never** call the inner client.

    ``--recache-luma`` / ``DIALOGCART_RECACHE_LUMA`` bypasses cache only for the
    default E2E recordings corpus (force live + overwrite). Custom
    ``recordings_dir`` (e.g. pytest ``tmp_path``) always honors hits so the
    replay invariant remains testable under a suite-level recache flag.

    Cache keys deliberately omit ``test_now`` so existing absolute-date
    recordings keep matching. On live miss / corpus recache, ``test_now`` is
    forwarded to the inner client when set via kwargs or ``LUMA_TEST_NOW``.
    """

    def __init__(
        self,
        inner: _LumaResolveClient,
        *,
        recordings_dir: Optional[Path] = None,
    ):
        self._inner = inner
        self.recordings_dir = Path(recordings_dir or _DEFAULT_RECORDINGS_DIR)
        self.last_response: Optional[Dict[str, Any]] = None
        self.last_recording_path: Optional[Path] = None
        self.last_cache_hit: Optional[bool] = None

    def _bypass_cache_for_recache(self) -> bool:
        """Force live only for the shared E2E corpus under ``--recache-luma``."""
        if not recache_luma_enabled():
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

        # Miss (or default-corpus recache bypass) → live, then save.
        live_kwargs: Dict[str, Any] = {}
        if test_now:
            live_kwargs["test_now"] = test_now

        response = self._inner.resolve(
            user_id=user_id,
            text=text,
            domain=domain,
            timezone=timezone,
            tenant_context=tenant_context,
            conversation_context=conversation_context,
            entity_schema=kwargs.get("entity_schema"),
            **live_kwargs,
        )
        self._save_recording(path, key, response)
        self.last_cache_hit = False
        self.last_response = response
        return response

    def notify_execution(
        self, user_id: str, booking_id: str, domain: str = "service"
    ) -> Dict[str, Any]:
        return self._inner.notify_execution(
            user_id=user_id, booking_id=booking_id, domain=domain
        )

    def _save_recording(
        self, path: Path, key: Dict[str, Any], response: Dict[str, Any]
    ) -> None:
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        payload = {"key": key, "response": response}
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
