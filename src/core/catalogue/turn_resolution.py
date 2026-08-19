"""Apply trusted catalogue facts to one NLU turn before planning."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .service_discovery import (
    ServiceCatalogue,
    build_presentation,
    resolve_presented_selection,
)

_SERVICE_DISCOVERY_INTENTS = frozenset({"CREATE_APPOINTMENT", "AVAILABILITY"})


def apply_catalogue_turn(
    response: dict[str, Any],
    *,
    catalogue: ServiceCatalogue,
    session: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Ground service/category evidence and attach transient presentation state."""
    updated = dict(response)
    facts = dict(updated.get("facts") or {})
    previous = session.get("catalogue_presentation") if isinstance(session, Mapping) else None

    selected_service = facts.get("service_id")
    if selected_service is not None:
        service = catalogue.service(selected_service)
        duplicate_name = bool(
            service
            and sum(
                item.name.strip().casefold() == service.name.strip().casefold()
                for item in catalogue.services
            ) > 1
        )
        if service is None or duplicate_name:
            facts["service_id"] = None
            updated["service_candidates"] = [
                item.name for item in catalogue.services
                if service is not None
                and item.name.strip().casefold() == service.name.strip().casefold()
            ]
            updated["_catalogue_presentation"] = None
            updated["facts"] = facts
            return updated
        else:
            # Catalogue lookup normalizes IDs to strings internally, but an
            # already-authoritative schema value must retain its original type
            # so compatibility facts remain identical to entity_resolutions.
            facts["service_id"] = selected_service
            updated["service_candidates"] = []
            updated["_catalogue_presentation"] = None
        updated["facts"] = facts
        if facts.get("service_id") is not None:
            return updated

    selection = resolve_presented_selection(
        updated.get("catalog_selection"),
        presentation=previous,
        catalogue=catalogue,
    )
    category = None
    if selection and selection["kind"] == "service":
        service = catalogue.service(selection["id"])
        if service is not None:
            facts["service_id"] = service.id
            updated["facts"] = facts
            updated["service_candidates"] = []
            updated["_catalogue_presentation"] = None
            return updated
    if selection and selection["kind"] == "category":
        category = catalogue.category(selection["id"])

    category_evidence = updated.get("service_category")
    if category is None and isinstance(category_evidence, Mapping):
        if category_evidence.get("resolution") == "RESOLVED":
            category = catalogue.category(category_evidence.get("name"))

    if category is not None:
        presentation = build_presentation(
            catalogue, kind="service", services=category.services
        )
        updated["service_candidates"] = [service.name for service in category.services]
        updated["_catalogue_presentation"] = presentation
        updated["facts"] = facts
        return updated

    intent = updated.get("intent")
    intent_name = intent.get("name") if isinstance(intent, Mapping) else None
    if catalogue.category_first and (
        intent_name in _SERVICE_DISCOVERY_INTENTS
        or updated.get("operation") == "list_service_categories"
    ):
        presentation = build_presentation(catalogue, kind="category")
        updated["service_candidates"] = [group.label for group in catalogue.categories]
        updated["_catalogue_presentation"] = presentation
    elif updated.get("catalog_selection") is not None:
        # Invalid/stale/wrong-kind ordinal evidence always fails closed.
        updated["service_candidates"] = []
        updated["_catalogue_presentation"] = None
    updated["facts"] = facts
    return updated
