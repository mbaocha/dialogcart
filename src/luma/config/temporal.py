# luma/config/temporal.py
from enum import Enum

# ----------------------------
# Fuzzy time → concrete window
# ----------------------------
FUZZY_TIME_WINDOWS = {
    "morning": ("00:00", "11:59"),
    "afternoon": ("12:00", "16:59"),
    "evening": ("17:00", "21:59"),
    "night": ("21:00", "23:59"),
}

# ----------------------------
# Temporal shapes
# ----------------------------
APPOINTMENT_TEMPORAL_TYPE = "datetime_range"
RESERVATION_TEMPORAL_TYPE = "date_range"

# ----------------------------
# Range invariants
# ----------------------------
APPOINTMENT_ALLOW_EQUAL_START_END = True
RESERVATION_ALLOW_EQUAL_START_END = False

# ----------------------------
# Date modes
# ----------------------------


class DateMode(str, Enum):
    SINGLE = "single_day"
    RANGE = "range"
    FLEXIBLE = "flexible"


# ----------------------------
# Time modes
# ----------------------------
class TimeMode(str, Enum):
    EXACT = "exact"
    WINDOW = "window"
    RANGE = "range"
    FUZZY = "fuzzy"
    NONE = "none"


# ----------------------------
# Date roles
# ----------------------------
# Date roles are used to disambiguate start/end dates in multi-turn
# reservation flows and prevent later turns from overwriting
# previously resolved temporal anchors.
class DateRole(str, Enum):
    START = "START_DATE"
    END = "END_DATE"


# Date role keyword mappings
# Canonical keyword sets used to infer date roles from user language.
# This is not hard-coding logic, only declaring intent.
# NLP / extraction layers may extend this later.
# This mapping is advisory, not mandatory.
DATE_ROLE_KEYWORDS = {
    DateRole.START: {
        "from",
        "starting",
        "start",
        "beginning",
        "since",
        "on",
    },
    DateRole.END: {
        "to",
        "until",
        "till",
        "through",
        "ending",
        "end",
    },
}

# ----------------------------
# Binding policy
# ----------------------------
# Bare weekday (e.g. "monday") without modifier ("this"/"next")
# must never be resolved silently.
ALLOW_BARE_WEEKDAY_BINDING = False
