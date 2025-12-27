"""
Temporal parsing rules configuration.

Controls how time parsing handles ambiguous cases like missing AM/PM.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TemporalRules:
    """
    Configuration for temporal parsing rules.

    Attributes:
        require_explicit_meridiem: If True, reject times without explicit AM/PM
        allow_partial_meridiem_propagation: If True, allow AM/PM from one time to apply to others in range
            Example: "between 2pm and 5" → "between 2pm and 5pm" (both become timetoken)
        allow_time_of_day_inference: If True, allow inferring AM/PM from time window context
            Example: "tomorrow morning between 2 and 5" → "2am and 5am" (both become timetoken)
            Uses time window words (morning→AM, afternoon/evening/night→PM) to infer missing meridiem
    """
    require_explicit_meridiem: bool = True
    allow_partial_meridiem_propagation: bool = False
    allow_time_of_day_inference: bool = False


# Default temporal rules configuration
TEMPORAL_RULES = TemporalRules(
    require_explicit_meridiem=True,
    # Enable: "between 2pm and 5" → "between 2pm and 5pm" (both become timetoken)
    allow_partial_meridiem_propagation=True,
    # Enable: "tomorrow morning between 2 and 5" → "2am and 5am" (both become timetoken)
    # Uses time window context (morning→AM, afternoon/evening/night→PM) to infer missing meridiem
    allow_time_of_day_inference=True,
)
