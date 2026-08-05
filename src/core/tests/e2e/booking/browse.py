"""Booking E2E scenarios — browse conversation state."""

# ============================================================
# Covered
#
# ✓ Valid
#
# TODO
#
# □ References
# □ Revision
# □ Digressions
# □ Invalid
# □ Recovery
# ============================================================

from __future__ import annotations

from typing import List

from core.planning.time_resolution import TIME_MATCH_EXACT, TIME_MATCH_MISMATCH
from core.tests.e2e.framework.conversation import (
    Expect,
    FLEXI_SERVICE,
    FROZEN_TIME,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _confirmation_state,
    _resolve_search_date,
    _response_text,
    attach_commit_customer_identity,
)
from core.adapters.errors import UpstreamError
from core.session.session_manager import get_session, save_session
from core.tests.e2e.booking import _helpers as _booking_helpers

globals().update(
    {
        name: getattr(_booking_helpers, name)
        for name in getattr(_booking_helpers, "__all__", dir(_booking_helpers))
        if not name.startswith("__")
    }
)

SCENARIOS: List[Scenario] = []
RELATED_SCENARIOS: List[Scenario] = []


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario


def _register_related(scenario: Scenario) -> Scenario:
    RELATED_SCENARIOS.append(scenario)
    return scenario


# ============================================================
# VALID RESPONSES
# ============================================================
# Browse exhaustion / pagination regressions (related suite).
from core.tests.e2e.booking._browse_exhaustion import SCENARIOS as _BROWSE_EXHAUSTION_SCENARIOS
RELATED_SCENARIOS.extend(_BROWSE_EXHAUSTION_SCENARIOS)

# ============================================================
# REFERENCE EXPRESSIONS
# ============================================================
# (no scenarios in this section yet)

# ============================================================
# REVISIONS
# ============================================================
# (no scenarios in this section yet)

# ============================================================
# DIGRESSIONS
# ============================================================
# (no scenarios in this section yet)

# ============================================================
# INVALID INPUT
# ============================================================
# (no scenarios in this section yet)

# ============================================================
# RECOVERY
# ============================================================
# (no scenarios in this section yet)
