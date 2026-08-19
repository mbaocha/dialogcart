"""Canonical Session V2 artifact timestamps and load-time invalidation.

Availability freshness owns cache, fingerprint, presented options, and
pagination freshness. Presentation has no independent freshness authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional

from core.clock import utc_now, utc_now_iso
from core.config.session_freshness import load_session_freshness_settings

AVAILABILITY_REFRESH_REASON_KEY = "_availability_refresh_reason"
AVAILABILITY_REFRESH_REASON_EXPIRED = "expired"


def _metadata(session: Dict[str, Any]) -> Dict[str, Any]:
    metadata = session.setdefault("metadata", {})
    artifacts = metadata.setdefault("artifacts", {})
    artifacts.setdefault("availability", None)
    artifacts.setdefault("confirmation", None)
    # Presentation and pagination are derived from availability. Discard the
    # redundant historical record rather than allowing it to become authoritative.
    artifacts.pop("presentation", None)
    return metadata


def _expiry(ttl_seconds: int) -> Dict[str, str]:
    created = utc_now()
    return {
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "expires_at": (created + timedelta(seconds=ttl_seconds))
        .isoformat()
        .replace("+00:00", "Z"),
    }


def stamp_last_activity(session: Dict[str, Any]) -> None:
    _metadata(session)["last_activity_at"] = utc_now_iso()


def stamp_availability_created(session: Dict[str, Any]) -> None:
    settings = load_session_freshness_settings()
    metadata = _metadata(session)
    evidence = _expiry(settings.availability_ttl_seconds)
    metadata["artifacts"]["availability"] = dict(evidence)


def sync_confirmation_freshness(session: Dict[str, Any]) -> None:
    metadata = _metadata(session)
    if session.get("confirmation_state") != "pending":
        metadata["artifacts"]["confirmation"] = None
        return
    if not isinstance(metadata["artifacts"].get("confirmation"), dict):
        settings = load_session_freshness_settings()
        metadata["artifacts"]["confirmation"] = _expiry(
            settings.confirmation_ttl_seconds
        )


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fresh(record: Any, now: datetime) -> bool:
    """Validate complete Core-owned provenance; corrupt evidence fails closed."""
    if not isinstance(record, Mapping):
        return False
    created_at = _parse(record.get("created_at"))
    expires_at = _parse(record.get("expires_at"))
    if created_at is None or expires_at is None:
        return False
    return created_at <= now < expires_at and expires_at > created_at


def _clear_canonical_confirmation(session: Dict[str, Any], *, reason: str) -> None:
    """Consume authorization while retaining Session V2's required null field."""
    from core.session.confirmation_gate import consume_confirmation_state

    consume_confirmation_state(session, reason=reason)
    session["confirmation_state"] = None


def apply_load_freshness(session: Dict[str, Any]) -> Dict[str, Any]:
    """Invalidate stale executable evidence on canonical V2 before hydration."""
    metadata = _metadata(session)
    artifacts = metadata["artifacts"]
    now = utc_now()
    availability = session.setdefault("availability", {})
    cache = availability.setdefault("cache", {})
    presentation = availability.setdefault("presentation", {})
    has_availability = bool(
        availability.get("fingerprint")
        or cache.get("search_result") is not None
        or presentation.get("presented") is not None
    )
    availability_stale = has_availability and not _fresh(
        artifacts.get("availability"), now
    )
    if availability_stale:
        from core.workflows.availability.presentation import clear_availability_artifacts

        clear_availability_artifacts(session)
        planning = session.setdefault("planning", {})
        planning["bound_datetime"] = None
        # Session V2 does not yet retain provenance distinguishing explicitly
        # requested date/time from offer-bound date/time. Fail closed rather than
        # preserve an unproven executable selection.
        slots = dict(planning.get("slots") or {})
        for key in ("date", "date_range", "time", "has_datetime", "datetime_range"):
            slots.pop(key, None)
        planning["slots"] = slots
        planning["proposals"] = {"date": None, "time": None}
        planning["temporal"] = None
        # These are derived from effective slots and must be recomputed by
        # Planning; retaining their pre-expiry values would mislead NLU context.
        planning["status"] = None
        planning["missing_slots"] = []
        planning["ask_next"] = None
        artifacts["availability"] = None
        # Same-turn, non-persistent provenance. Session V2 normalization drops
        # this compatibility marker before storage.
        session[AVAILABILITY_REFRESH_REASON_KEY] = (
            AVAILABILITY_REFRESH_REASON_EXPIRED
        )

        # A confirmation derived from expired offers cannot remain executable,
        # even when its own wall-clock TTL has not elapsed yet.
        if session.get("confirmation_state") == "pending":
            _clear_canonical_confirmation(
                session,
                reason="availability_freshness_expired",
            )
            artifacts["confirmation"] = None

    if session.get("confirmation_state") == "pending" and not _fresh(
        artifacts.get("confirmation"), now
    ):
        _clear_canonical_confirmation(
            session,
            reason="confirmation_freshness_expired",
        )
        artifacts["confirmation"] = None
        planning = session.setdefault("planning", {})
        planning.pop("action", None)
        session.pop("action", None)
        planning["bound_datetime"] = None

    return session
