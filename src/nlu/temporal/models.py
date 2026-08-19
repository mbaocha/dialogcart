"""
Canonical Temporal model — permanent NLU contract (Core will consume this).

Stage2 owns semantic meaning; TemporalResolver finalises closed-vocab ISO;
CalendarBinder consumes validated Temporal only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

# Permanent mode values (replaces separate date_constraint for new readers).
TEMPORAL_MODES = frozenset({"none", "single_day", "range", "flexible"})


@dataclass
class Temporal:
    """Nullable interval covering appointments, reservations, and flexible periods."""

    expression: Optional[str] = None
    start_date_expression: Optional[str] = None
    start_time_expression: Optional[str] = None
    end_date_expression: Optional[str] = None
    end_time_expression: Optional[str] = None
    start_date: Optional[str] = None
    start_time: Optional[str] = None
    end_date: Optional[str] = None
    end_time: Optional[str] = None
    mode: Optional[str] = None
    confidence: Optional[float] = None
    resolution: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable dict (nulls preserved)."""
        return asdict(self)
