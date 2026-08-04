"""
Build NLU entity_schema from business category configuration + catalog projection.

entity_schema is a derived request artifact — never persisted.
Business category owns field envelopes (name, type, description, catalog key, role, values).
Catalog owns phrase → id values (via shared catalog_projection).
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Set

from core.adapters.nlu.catalog_projection import project_collection

_BOOKABLE_NAMES = frozenset({"service", "room_type"})
_STAFF_NAMES = frozenset({"staff", "technician"})
_SUPPORTED_TYPES = frozenset({"catalog", "enum", "text"})
_SUPPORTED_ROLES = frozenset({"bookable_item", "staff"})


def build_entity_schema(
    business_category: str,
    catalog_data: Optional[Mapping[str, Any]] = None,
    *,
    projected_collections: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Compose entity_schema for ``business_category`` from config + catalog projection.

    Prefer ``projected_collections`` when the caller already projected the
    catalog once (shared with aliases). Falls back to projecting from
    ``catalog_data`` per entity catalog key.

    Returns None when no fields can be built so callers may omit /resolve field.
    """
    from core.config.business_category_loader import get_category_entities

    fields: List[Dict[str, Any]] = []
    for entity in get_category_entities(business_category):
        field = _build_entity_field(
            entity,
            catalog_data=catalog_data,
            projected_collections=projected_collections,
        )
        if field is not None:
            fields.append(field)

    if not fields:
        return None
    return {"version": 1, "fields": fields}


def resolved_id_key_for_field(field: Mapping[str, Any]) -> Optional[str]:
    """Return the resolved-id fact/slot key for a catalog entity field, else None."""
    if field.get("type") != "catalog":
        return None
    name = field.get("name")
    if not isinstance(name, str) or not name:
        return None
    role = field.get("role")
    if role is None:
        if name in _BOOKABLE_NAMES:
            role = "bookable_item"
        elif name in _STAFF_NAMES:
            role = "staff"
    if role == "bookable_item":
        return "service_id"
    if role == "staff":
        return "staff_id"
    return f"{name}_id"


def promotable_slot_keys_from_entity_schema(
    entity_schema: Optional[Mapping[str, Any]],
) -> FrozenSet[str]:
    """Allowlisted slot keys Core may promote from Luma facts for this schema.

    Catalog fields with a platform role (``bookable_item``, ``staff``) promote
    only their canonical Core slot (``service_id`` / ``staff_id``) — not the
    business entity name. Enum/text fields promote the field name. Other
    catalogs promote the resolved id key (and name when no platform role).

    Does not include platform-only keys (``booking_id``); callers add those.
    """
    if not isinstance(entity_schema, Mapping):
        return frozenset()
    fields = entity_schema.get("fields")
    if not isinstance(fields, list):
        return frozenset()

    keys: Set[str] = set()
    for raw in fields:
        if not isinstance(raw, Mapping):
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            continue
        field_type = raw.get("type")
        if field_type not in _SUPPORTED_TYPES:
            continue
        if field_type == "catalog":
            resolved = resolved_id_key_for_field(raw)
            if _effective_role(raw) in _SUPPORTED_ROLES:
                # One-way normalization: role → canonical platform slot only.
                if resolved:
                    keys.add(resolved)
                continue
            if resolved:
                keys.add(resolved)
            keys.add(name)
            continue
        # enum / text: field name is the canonical planning slot.
        keys.add(name)
    return frozenset(keys)


def planning_slot_key_for_field(field: Mapping[str, Any]) -> Optional[str]:
    """Durable planning slot key for a schema field (resolved id for catalogs)."""
    name = field.get("name")
    if not isinstance(name, str) or not name:
        return None
    field_type = field.get("type")
    if field_type not in _SUPPORTED_TYPES:
        return None
    resolved = resolved_id_key_for_field(field)
    return resolved or name


def required_slot_keys_from_entity_schema(
    entity_schema: Optional[Mapping[str, Any]],
) -> List[str]:
    """Ordered planning slot keys for entities marked ``required: true``.

    Declaration order is preserved. Catalog entities contribute their resolved
    id key (e.g. ``staff_id``); enum/text contribute the field name.
    """
    if not isinstance(entity_schema, Mapping):
        return []
    fields = entity_schema.get("fields")
    if not isinstance(fields, list):
        return []

    keys: List[str] = []
    seen: Set[str] = set()
    for raw in fields:
        if not isinstance(raw, Mapping):
            continue
        if raw.get("required") is not True:
            continue
        key = planning_slot_key_for_field(raw)
        if key is None or key in seen:
            continue
        keys.append(key)
        seen.add(key)
    return keys


# Role defaults when ``availability_criteria`` is absent on a field.
_AVAILABILITY_CRITERIA_DEFAULT_ROLES = frozenset({"bookable_item", "staff"})

# Platform temporal / identity keys always eligible as availability criteria.
PLATFORM_SEARCH_CRITERIA_KEYS: FrozenSet[str] = frozenset(
    {
        "service_id",
        "date",
        "start_date",
        "date_range",
        "location",
        "staff_id",
        "resource",
        "resource_id",
    }
)

# Legacy fingerprint/fact aliases → canonical planning keys.
SEARCH_CRITERIA_KEY_ALIASES: Dict[str, str] = {
    "staff": "staff_id",
}


def _effective_role(field: Mapping[str, Any]) -> Optional[str]:
    role = field.get("role")
    if role in _SUPPORTED_ROLES:
        return str(role)
    name = field.get("name")
    if not isinstance(name, str):
        return None
    if name in _BOOKABLE_NAMES:
        return "bookable_item"
    if name in _STAFF_NAMES:
        return "staff"
    return None


def field_availability_criteria(field: Mapping[str, Any]) -> bool:
    """Effective ``availability_criteria`` for a schema field.

    Explicit boolean wins. When absent: ``bookable_item`` / ``staff`` → True,
    everything else → False (zero YAML churn for salon/hotel).
    """
    flag = field.get("availability_criteria")
    if flag is True:
        return True
    if flag is False:
        return False
    return _effective_role(field) in _AVAILABILITY_CRITERIA_DEFAULT_ROLES


def search_criteria_slot_keys_from_entity_schema(
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> FrozenSet[str]:
    """Planning slot keys that participate in availability identity.

    Single source of truth for: SEARCH gating inputs, promptable-before-search,
    fingerprint, revision, invalidation, and availability request construction.

    Includes platform availability keys plus resolved keys for schema fields
    whose effective ``availability_criteria`` is true.
    """
    keys: Set[str] = set(PLATFORM_SEARCH_CRITERIA_KEYS)
    if not isinstance(entity_schema, Mapping):
        return frozenset(keys)
    fields = entity_schema.get("fields")
    if not isinstance(fields, list):
        return frozenset(keys)
    for raw in fields:
        if not isinstance(raw, Mapping):
            continue
        if not field_availability_criteria(raw):
            continue
        key = planning_slot_key_for_field(raw)
        if key:
            keys.add(key)
    return frozenset(keys)


def canonicalize_search_criteria_key(key: str) -> str:
    """Map legacy search-criteria aliases to canonical planning keys."""
    return SEARCH_CRITERIA_KEY_ALIASES.get(key, key)


def planning_slot_key_for_role(
    entity_schema: Optional[Mapping[str, Any]],
    role: str,
) -> Optional[str]:
    """Planning slot key for the first schema field with the given effective role."""
    if not isinstance(entity_schema, Mapping) or not role:
        return None
    fields = entity_schema.get("fields")
    if not isinstance(fields, list):
        return None
    for raw in fields:
        if not isinstance(raw, Mapping):
            continue
        if _effective_role(raw) != role:
            continue
        key = planning_slot_key_for_field(raw)
        if key:
            return key
    return None


def bookable_item_slot_key(
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> str:
    """Planning slot for ``role: bookable_item``.

    Falls back to ``service_id`` when schema is absent or omits the role
    (platform compatibility with existing salon/service booking slots).
    """
    key = planning_slot_key_for_role(entity_schema, "bookable_item")
    return key if key else "service_id"


def field_for_planning_slot(
    entity_schema: Optional[Mapping[str, Any]],
    slot_key: str,
) -> Optional[Dict[str, Any]]:
    """Return the schema field whose planning slot key matches ``slot_key``."""
    if not isinstance(entity_schema, Mapping) or not slot_key:
        return None
    fields = entity_schema.get("fields")
    if not isinstance(fields, list):
        return None
    for raw in fields:
        if not isinstance(raw, Mapping):
            continue
        if planning_slot_key_for_field(raw) == slot_key:
            return dict(raw)
        name = raw.get("name")
        if name == slot_key:
            return dict(raw)
    return None


def description_for_planning_slot(
    entity_schema: Optional[Mapping[str, Any]],
    slot_key: str,
) -> Optional[str]:
    """Human description for a planning slot from the active entity schema."""
    field = field_for_planning_slot(entity_schema, slot_key)
    if not field:
        return None
    description = field.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    name = field.get("name")
    if isinstance(name, str) and name.strip():
        return f"The {name.strip()} mentioned by the user."
    return None


def catalog_candidates_for_slot(
    sources: Mapping[str, Any],
    slot_key: str,
    *,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> List[Any]:
    """Candidate suggestions for the active missing catalog planning slot.

    Bookable ``service_id`` continues to use ``service_candidates``.
    Other catalog entities use ``{field_name}_candidates`` when present.
    """
    if not isinstance(sources, Mapping) or not slot_key:
        return []

    def _from(container: Any, key: str) -> List[Any]:
        if not isinstance(container, Mapping):
            return []
        raw = container.get(key)
        return list(raw) if isinstance(raw, list) else []

    facts = sources.get("facts") if isinstance(sources.get("facts"), Mapping) else {}

    if slot_key == "service_id":
        for key in ("service_candidates",):
            found = _from(sources, key) or _from(facts, key)
            if found:
                return found
        return []

    field = field_for_planning_slot(entity_schema, slot_key)
    names: List[str] = []
    if field and isinstance(field.get("name"), str):
        names.append(f"{field['name']}_candidates")
    # Common resolved-id → candidates key (staff_id → staff_candidates)
    if slot_key.endswith("_id") and len(slot_key) > 3:
        names.append(f"{slot_key[:-3]}_candidates")
    names.append(f"{slot_key}_candidates")

    for key in names:
        found = _from(sources, key) or _from(facts, key)
        if found:
            return found
    return []


def _build_entity_field(
    entity: Mapping[str, Any],
    *,
    catalog_data: Optional[Mapping[str, Any]],
    projected_collections: Optional[Mapping[str, Mapping[str, Any]]],
) -> Optional[Dict[str, Any]]:
    name = entity.get("name")
    field_type = entity.get("type")
    if not name or not isinstance(name, str):
        return None
    if field_type not in _SUPPORTED_TYPES:
        return None

    description = entity.get("description")
    if not isinstance(description, str) or not description:
        description = f"The {name} mentioned by the user."

    role = entity.get("role")
    if role is not None and role not in _SUPPORTED_ROLES:
        role = None

    required = entity.get("required") is True
    # prompt_if_missing only applies to optional entities (default false).
    prompt_if_missing = (not required) and entity.get("prompt_if_missing") is True
    # Pass through explicit availability_criteria only (defaults applied at read time).
    availability_criteria = entity.get("availability_criteria")
    has_availability_criteria = isinstance(availability_criteria, bool)

    if field_type == "catalog":
        catalog_key = entity.get("catalog")
        if not catalog_key or not isinstance(catalog_key, str):
            return None

        phrase_map: Dict[str, Any] = {}
        if projected_collections is not None:
            projected = projected_collections.get(catalog_key)
            if isinstance(projected, Mapping):
                phrase_map = dict(projected)
        elif isinstance(catalog_data, Mapping):
            phrase_map = project_collection(catalog_data.get(catalog_key))

        if not phrase_map:
            return None

        field: Dict[str, Any] = {
            "name": name,
            "type": "catalog",
            "description": description,
            "catalog": phrase_map,
        }
        if isinstance(role, str):
            field["role"] = role
        if required:
            field["required"] = True
        if prompt_if_missing:
            field["prompt_if_missing"] = True
        if has_availability_criteria:
            field["availability_criteria"] = availability_criteria
        return field

    if field_type == "enum":
        values = entity.get("values")
        if not isinstance(values, list) or not values:
            return None
        normalized = [str(v).strip() for v in values if str(v).strip()]
        if not normalized:
            return None
        field = {
            "name": name,
            "type": "enum",
            "description": description,
            "values": normalized,
        }
        if required:
            field["required"] = True
        if prompt_if_missing:
            field["prompt_if_missing"] = True
        if has_availability_criteria:
            field["availability_criteria"] = availability_criteria
        return field

    # text
    field = {
        "name": name,
        "type": "text",
        "description": description,
    }
    if required:
        field["required"] = True
    if prompt_if_missing:
        field["prompt_if_missing"] = True
    if has_availability_criteria:
        field["availability_criteria"] = availability_criteria
    return field
