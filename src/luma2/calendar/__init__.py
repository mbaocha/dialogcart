"""
Calendar Binding Layer

Converts semantic meaning into actual calendar dates and times.
Produces machine-usable values (ISO dates / timestamps).

This layer answers: "What real dates/times does this correspond to?"
NOT: "What does the user mean?" (that's Semantic Resolution)
"""

from luma.calendar.calendar_binder import (
    bind_calendar,
    CalendarBindingResult,
)

__all__ = [
    "bind_calendar",
    "CalendarBindingResult",
]

