"""Validated runtime settings for session retention and artifact freshness."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SESSION_TTL_ENV = "DIALOGCART_SESSION_TTL_SECONDS"
AVAILABILITY_TTL_ENV = "DIALOGCART_AVAILABILITY_TTL_SECONDS"
CONFIRMATION_TTL_ENV = "DIALOGCART_CONFIRMATION_TTL_SECONDS"

DEFAULT_SESSION_TTL_SECONDS = 20 * 60
# These preserve the previous effective upper bound while making freshness
# independent from any future increase to session retention.
DEFAULT_AVAILABILITY_TTL_SECONDS = 20 * 60
DEFAULT_CONFIRMATION_TTL_SECONDS = 20 * 60


def _positive_seconds(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        logger.warning("Ignoring invalid %s=%r; using %s seconds", name, raw, default)
        return default
    return value


@dataclass(frozen=True)
class SessionFreshnessSettings:
    session_ttl_seconds: int
    availability_ttl_seconds: int
    confirmation_ttl_seconds: int


def load_session_freshness_settings() -> SessionFreshnessSettings:
    """Load all related settings through one validated configuration boundary."""
    return SessionFreshnessSettings(
        session_ttl_seconds=_positive_seconds(
            SESSION_TTL_ENV, DEFAULT_SESSION_TTL_SECONDS
        ),
        availability_ttl_seconds=_positive_seconds(
            AVAILABILITY_TTL_ENV, DEFAULT_AVAILABILITY_TTL_SECONDS
        ),
        confirmation_ttl_seconds=_positive_seconds(
            CONFIRMATION_TTL_ENV, DEFAULT_CONFIRMATION_TTL_SECONDS
        ),
    )

