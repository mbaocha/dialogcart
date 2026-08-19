"""Trusted, service-only catalogue discovery primitives.

Categories are derived labels used to narrow service discovery.  They are not
bookable resources and this module deliberately exposes no category id.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence


def normalize_category(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    label = value.strip()
    return label.casefold() if label else None


@dataclass(frozen=True)
class ServiceRecord:
    id: str
    name: str
    description: Optional[str] = None
    category: Optional[str] = None


@dataclass(frozen=True)
class CategoryGroup:
    key: str
    label: str
    services: tuple[ServiceRecord, ...]


@dataclass(frozen=True)
class ServiceCatalogue:
    services: tuple[ServiceRecord, ...]
    categories: tuple[CategoryGroup, ...]
    category_first: bool
    fingerprint: str

    def service(self, service_id: Any) -> Optional[ServiceRecord]:
        requested = str(service_id)
        matches = [service for service in self.services if service.id == requested]
        return matches[0] if len(matches) == 1 else None

    def category(self, value: Any) -> Optional[CategoryGroup]:
        key = normalize_category(value)
        if key is None:
            return None
        return next((group for group in self.categories if group.key == key), None)


def derive_service_catalogue(raw_services: Any) -> ServiceCatalogue:
    """Build the active trusted service view and optional category grouping."""
    services: list[ServiceRecord] = []
    if isinstance(raw_services, list):
        for raw in raw_services:
            if not isinstance(raw, Mapping) or raw.get("is_active") is False:
                continue
            raw_id, raw_name = raw.get("id"), raw.get("name")
            if raw_id is None or not isinstance(raw_name, str) or not raw_name.strip():
                continue
            description = raw.get("description")
            category = raw.get("category")
            services.append(
                ServiceRecord(
                    id=str(raw_id),
                    name=raw_name.strip(),
                    description=(
                        description.strip()
                        if isinstance(description, str) and description.strip()
                        else None
                    ),
                    category=(
                        category.strip()
                        if isinstance(category, str) and category.strip()
                        else None
                    ),
                )
            )

    category_first = bool(services) and all(s.category is not None for s in services)
    groups: list[CategoryGroup] = []
    if category_first:
        grouped: dict[str, list[ServiceRecord]] = {}
        labels: dict[str, str] = {}
        for service in services:
            key = normalize_category(service.category)
            assert key is not None
            labels.setdefault(key, str(service.category))
            grouped.setdefault(key, []).append(service)
        groups = [
            CategoryGroup(key=key, label=labels[key], services=tuple(items))
            for key, items in grouped.items()
        ]

    identity = [
        {"id": s.id, "name": s.name, "description": s.description, "category": s.category}
        for s in services
    ]
    fingerprint = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return ServiceCatalogue(tuple(services), tuple(groups), category_first, fingerprint)


def nlu_catalog_context(catalogue: ServiceCatalogue) -> dict[str, Any]:
    """Structured, request-scoped semantic evidence for NLU."""
    return {
        "services": [
            {
                "id": service.id,
                "name": service.name,
                **({"description": service.description} if service.description else {}),
                **({"category": service.category} if service.category else {}),
            }
            for service in catalogue.services
        ]
    }


def build_presentation(
    catalogue: ServiceCatalogue,
    *,
    kind: str,
    services: Optional[Sequence[ServiceRecord]] = None,
) -> dict[str, Any]:
    if kind == "category":
        identities = [(group.key, group.label) for group in catalogue.categories]
    elif kind == "service":
        identities = [(service.id, service.name) for service in (services or catalogue.services)]
    else:
        raise ValueError("catalogue presentation kind must be category or service")
    reference_seed = json.dumps(
        [catalogue.fingerprint, kind, identities], separators=(",", ":"), ensure_ascii=False
    )
    reference = "cp_" + hashlib.sha256(reference_seed.encode("utf-8")).hexdigest()[:16]
    return {
        "reference": reference,
        "kind": kind,
        "catalogue_fingerprint": catalogue.fingerprint,
        "options": [
            {"index": index, "id": identity, "label": label}
            for index, (identity, label) in enumerate(identities, start=1)
        ],
    }


def resolve_presented_selection(
    selection: Any,
    *,
    presentation: Any,
    catalogue: ServiceCatalogue,
) -> Optional[dict[str, str]]:
    """Validate an NLU ordinal reference entirely against trusted current state."""
    if not isinstance(selection, Mapping) or not isinstance(presentation, Mapping):
        return None
    if selection.get("presentation_ref") != presentation.get("reference"):
        return None
    if selection.get("kind") != presentation.get("kind"):
        return None
    if not is_valid_presentation(presentation, catalogue=catalogue):
        return None
    options = presentation["options"]
    option = selection.get("option")
    if isinstance(option, bool) or not isinstance(option, int) or option < 1:
        return None
    if option > len(options):
        return None
    chosen = options[option - 1]
    return {"kind": str(presentation.get("kind")), "id": str(chosen.get("id"))}


def is_valid_presentation(
    presentation: Any, *, catalogue: ServiceCatalogue
) -> bool:
    """Validate stored catalogue state before exposing or resolving it."""
    if not isinstance(presentation, Mapping):
        return False
    if presentation.get("catalogue_fingerprint") != catalogue.fingerprint:
        return False
    options = presentation.get("options")
    if not isinstance(options, list) or not options:
        return False
    identities: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(options, start=1):
        if not isinstance(raw, Mapping) or raw.get("index") != index:
            return False
        identity = str(raw.get("id"))
        label = raw.get("label")
        if identity in seen or not isinstance(label, str):
            return False
        seen.add(identity)
        if presentation.get("kind") == "service":
            current = catalogue.service(identity)
            if current is None or current.name != label:
                return False
        elif presentation.get("kind") == "category":
            current = catalogue.category(identity)
            if current is None or current.label != label:
                return False
        else:
            return False
        identities.append((identity, label))
    reference_seed = json.dumps(
        [catalogue.fingerprint, presentation.get("kind"), identities],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    expected_reference = (
        "cp_" + hashlib.sha256(reference_seed.encode("utf-8")).hexdigest()[:16]
    )
    if presentation.get("reference") != expected_reference:
        return False
    return True
