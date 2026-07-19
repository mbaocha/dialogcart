"""Availability ↔ Discovery adapters (production orchestration path).

Public adapters:
- AvailabilityProvider
- AvailabilityNavigationPolicy
- AvailabilitySelectionPolicy

Bridge helpers compose Discovery's Search / Navigator / Selector APIs:
``search_via_discovery``, ``present_via_discovery``, ``browse_via_discovery``,
``resolve_via_discovery``, ``project_via_discovery``.
"""

from core.workflows.availability.discovery.bridge import (
    browse_via_discovery,
    present_via_discovery,
    project_via_discovery,
    resolve_via_discovery,
    search_via_discovery,
)
from core.workflows.availability.discovery.navigation import (
    AvailabilityNavigationPolicy,
)
from core.workflows.availability.discovery.provider import AvailabilityProvider
from core.workflows.availability.discovery.selection import (
    AvailabilitySelectionPolicy,
)

__all__ = [
    "AvailabilityNavigationPolicy",
    "AvailabilityProvider",
    "AvailabilitySelectionPolicy",
    "browse_via_discovery",
    "present_via_discovery",
    "project_via_discovery",
    "resolve_via_discovery",
    "search_via_discovery",
]
