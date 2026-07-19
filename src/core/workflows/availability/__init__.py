"""Availability workflow package.

Production orchestration goes through Discovery:

    Planner → AvailabilityWorkflow → Discovery (Search / Navigator / Selector)
      → AvailabilityProvider / AvailabilityNavigationPolicy / AvailabilitySelectionPolicy
      → availability algorithms (presentation, selection, fingerprint)

Public surface: contracts, workflow facade, Discovery bridge and adapters,
and session adapters used outside this package.
"""

from core.workflows.availability.contracts import (
    AvailabilityCache,
    BrowseIntent,
    BrowseProjection,
    PresentedAvailability,
    SelectionResolution,
)
from core.workflows.availability.discovery import (
    AvailabilityNavigationPolicy,
    AvailabilityProvider,
    AvailabilitySelectionPolicy,
    browse_via_discovery,
    present_via_discovery,
    project_via_discovery,
    resolve_via_discovery,
    search_via_discovery,
)
from core.workflows.availability.presentation import (
    availability_cache_from_session,
    ensure_presented_availability,
    presented_availability_from_session,
)
from core.workflows.availability.workflow import AvailabilityWorkflow

__all__ = [
    "AvailabilityCache",
    "AvailabilityNavigationPolicy",
    "AvailabilityProvider",
    "AvailabilitySelectionPolicy",
    "AvailabilityWorkflow",
    "BrowseIntent",
    "BrowseProjection",
    "PresentedAvailability",
    "SelectionResolution",
    "availability_cache_from_session",
    "browse_via_discovery",
    "ensure_presented_availability",
    "present_via_discovery",
    "presented_availability_from_session",
    "project_via_discovery",
    "resolve_via_discovery",
    "search_via_discovery",
]
