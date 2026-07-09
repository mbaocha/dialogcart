"""Resolve organization_id from environment.

Neutral utility extracted from orchestrator.py so that both orchestrator.py
and turn_planner.py can import it without creating a circular dependency.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _get_org_id_from_env() -> int:
    """Return organization_id from ORG_ID env var with safe default."""
    value = os.getenv("ORG_ID", "1")
    try:
        org_id = int(value)
        if org_id <= 0:
            raise ValueError("ORG_ID must be positive")
        return org_id
    except Exception:  # noqa: BLE001
        logger.warning("Invalid ORG_ID env value '%s', defaulting to 1", value)
        return 1
