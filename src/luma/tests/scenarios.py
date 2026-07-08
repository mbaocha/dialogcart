"""
Central scenarios module that imports and re-exports all scenario types.

This module maintains backward compatibility by re-exporting scenarios
from their dedicated files.
"""

from .availability_browse_scenarios import availability_browse_scenarios
from .booking_scenarios import booking_scenarios
from .other_scenarios import other_scenarios

# Default: use booking scenarios for backward compatibility
scenarios = booking_scenarios

__all__ = [
    "availability_browse_scenarios",
    "booking_scenarios",
    "other_scenarios",
    "scenarios",
]
