"""Service catalogue contracts owned by Core."""

from .service_discovery import (
    CategoryGroup,
    ServiceCatalogue,
    ServiceRecord,
    build_presentation,
    derive_service_catalogue,
    is_valid_presentation,
    nlu_catalog_context,
    normalize_category,
    resolve_presented_selection,
)
from .turn_resolution import apply_catalogue_turn

__all__ = [
    "CategoryGroup",
    "ServiceCatalogue",
    "ServiceRecord",
    "build_presentation",
    "derive_service_catalogue",
    "is_valid_presentation",
    "nlu_catalog_context",
    "normalize_category",
    "resolve_presented_selection",
    "apply_catalogue_turn",
]
