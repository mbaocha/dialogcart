"""
Shared catalog → phrase map projection for NLU request context.

Single projection used by:
- entity_schema.fields[].catalog (original display-name keys)
- tenant_context.aliases (lowercased keys, compatibility)
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence


def project_collection(items: Any) -> Dict[str, Any]:
    """Project active catalog items to display-name → id."""
    if not isinstance(items, list):
        return {}

    phrase_map: Dict[str, Any] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("is_active") is False:
            continue
        name = item.get("name")
        if not name or not isinstance(name, str):
            continue
        value = catalog_item_value(item, name)
        if value is None:
            continue
        phrase_map[name] = value
    return phrase_map


def project_catalog_collections(
    catalog_data: Optional[Mapping[str, Any]],
    collection_keys: Optional[Sequence[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Project catalog collections to phrase maps.

    When ``collection_keys`` is provided (from the active business schema),
    only those collections are projected — no hardcoded collection names.
    Otherwise every list-valued key in ``catalog_data`` is projected.
    """
    if not isinstance(catalog_data, Mapping):
        return {}

    if collection_keys is None:
        keys = [k for k, v in catalog_data.items() if isinstance(v, list)]
    else:
        keys = [k for k in collection_keys if isinstance(k, str) and k]

    projected: Dict[str, Dict[str, Any]] = {}
    for key in keys:
        phrase_map = project_collection(catalog_data.get(key))
        if phrase_map:
            projected[key] = phrase_map
    return projected


def aliases_from_projection(
    projected_collections: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Compatibility aliases: lowercased phrase → id from all projected collections."""
    alias_map: Dict[str, Any] = {}
    for phrase_map in projected_collections.values():
        if not isinstance(phrase_map, Mapping):
            continue
        for phrase, entity_id in phrase_map.items():
            alias_map[str(phrase).lower()] = entity_id
    return alias_map


def catalog_item_value(item: Mapping[str, Any], name: str) -> Any:
    """Resolve a stable catalog id using generic field precedence."""
    item_id = item.get("id")
    if item_id is not None:
        try:
            return int(item_id)
        except (TypeError, ValueError):
            return item_id

    for key in ("canonical_key", "service_family_id", "canonical", "slug"):
        val = item.get(key)
        if val is not None and val != "":
            return val

    return name.lower().replace(" ", "_")
