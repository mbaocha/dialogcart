"""Runtime-safe recording and replay support for Luma ``/resolve`` calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol

from core.adapters.errors import UpstreamError


class LumaResolveClient(Protocol):
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


class LumaReplayMissError(UpstreamError):
    """A replay-only lookup did not find a matching recording."""


def build_recording_key(
    *,
    text: str,
    domain: str,
    timezone: str,
    tenant_context: Optional[Dict[str, Any]],
    conversation_context: Optional[Dict[str, Any]],
    test_now: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the canonical request identity used by the E2E corpus.

    ``test_now`` remains an explicit opt-in field. Normal recording clients do
    not include it, preserving the established stable-filename policy.
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


class RecordingLumaClient:  # noqa: N801
    """Replay Luma recordings, optionally recording live misses."""

    def __init__(
        self,
        inner: LumaResolveClient,
        *,
        recordings_dir: Path,
        mode: str = "record",
        test_now_provider: Optional[Callable[[Optional[str]], Optional[str]]] = None,
        bypass_cache: Optional[Callable[[], bool]] = None,
    ) -> None:
        if mode not in {"record", "replay"}:
            raise ValueError("RecordingLumaClient mode must be 'record' or 'replay'")
        self._inner = inner
        self.recordings_dir = Path(recordings_dir)
        self.mode = mode
        self._test_now_provider = test_now_provider
        self._bypass_cache = bypass_cache or (lambda: False)
        self.last_response: Optional[Dict[str, Any]] = None
        self.last_recording_path: Optional[Path] = None
        self.last_cache_hit: Optional[bool] = None

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

        bypass = self.mode == "record" and self._bypass_cache()
        if path.is_file() and not bypass:
            recorded = json.loads(path.read_text(encoding="utf-8"))
            response = recorded.get("response")
            if not isinstance(response, dict):
                raise ValueError(f"Invalid Luma recording (missing response): {path}")
            self.last_cache_hit = True
            self.last_response = response
            return response

        if self.mode == "replay":
            self.last_cache_hit = False
            raise LumaReplayMissError(
                f"Luma replay miss for recording {path.name}"
            )

        live_kwargs: Dict[str, Any] = {
            "user_id": user_id,
            "text": text,
            "domain": domain,
            "timezone": timezone,
            "tenant_context": tenant_context,
            "conversation_context": conversation_context,
            "entity_schema": kwargs.get("entity_schema"),
        }
        explicit_test_now = kwargs.get("test_now")
        if self._test_now_provider is not None:
            resolved_test_now = self._test_now_provider(explicit_test_now)
        else:
            resolved_test_now = explicit_test_now
        if resolved_test_now:
            live_kwargs["test_now"] = resolved_test_now

        response = self._inner.resolve(**live_kwargs)
        self._save_recording(path, key, response)
        self.last_cache_hit = False
        self.last_response = response
        return response

    def notify_execution(
        self, user_id: str, booking_id: str, domain: str = "service"
    ) -> Dict[str, Any]:
        if self.mode == "replay":
            return {"success": False, "message": "Luma replay mode: notification skipped"}
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
