"""Promptable optional business entities (offer-once elicitation).

Keeps ``missing_slots`` required-only. Derives a separate promptable queue and
feeds the shared ``ask_next`` pointer (required first, then promptable).

Decline comes from Stage 2 top-level ``declined_entities`` (schema field names);
Core maps those names to planning slot keys. No utterance/language interpretation.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence, Set

from core.adapters.nlu.entity_schema_builder import (
    bookable_item_slot_key,
    field_for_planning_slot,
    planning_slot_key_for_field,
    search_criteria_slot_keys_from_entity_schema,
)


def catalog_unique_id_count(field: Mapping[str, Any]) -> int:
    """Count unique catalog ids for a schema field (0 when not a catalog)."""
    if field.get("type") != "catalog":
        return 0
    catalog = field.get("catalog")
    if not isinstance(catalog, Mapping) or not catalog:
        return 0
    return len({str(v) for v in catalog.values() if v is not None and str(v).strip()})


def has_meaningful_catalog_choice(field: Mapping[str, Any]) -> bool:
    """Platform rule: prompt only when catalog has more than one unique item."""
    return catalog_unique_id_count(field) > 1


def catalog_labels_for_planning_slot(
    entity_schema: Optional[Mapping[str, Any]],
    slot_key: str,
) -> List[str]:
    """Human labels from entity_schema catalog for a planning slot (id-unique)."""
    field = field_for_planning_slot(entity_schema, slot_key)
    if not field or field.get("type") != "catalog":
        return []
    catalog = field.get("catalog")
    if not isinstance(catalog, Mapping):
        return []
    labels: List[str] = []
    seen_ids: Set[str] = set()
    for phrase, raw_id in catalog.items():
        if not isinstance(phrase, str) or not phrase.strip():
            continue
        id_key = str(raw_id).strip() if raw_id is not None else ""
        if not id_key or id_key in seen_ids:
            continue
        seen_ids.add(id_key)
        labels.append(phrase.strip())
    return labels


def choice_labels_for_planning_slot(
    entity_schema: Optional[Mapping[str, Any]],
    slot_key: str,
) -> List[str]:
    """Configured choices for a catalog or enum planning slot."""
    field = field_for_planning_slot(entity_schema, slot_key)
    if not field:
        return []
    if field.get("type") == "catalog":
        return catalog_labels_for_planning_slot(entity_schema, slot_key)
    if field.get("type") != "enum":
        return []

    values = field.get("values")
    if not isinstance(values, list):
        return []
    labels: List[str] = []
    seen: Set[str] = set()
    for value in values:
        label = str(value).strip() if value is not None else ""
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels


def _slot_filled(slots: Mapping[str, Any], key: str) -> bool:
    value = slots.get(key)
    return value is not None and value != ""


def _bookable_item_collected(
    slots: Mapping[str, Any],
    entity_schema: Optional[Mapping[str, Any]],
) -> bool:
    """True when the schema bookable_item planning slot has a value."""
    return _slot_filled(slots, bookable_item_slot_key(entity_schema))


def normalize_declined_slots(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        key = item.strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def planning_keys_from_declined_entities(
    entity_schema: Optional[Mapping[str, Any]],
    declined_entities: Optional[Sequence[str]] = None,
) -> List[str]:
    """Map Stage 2 entity field names to durable planning slot keys."""
    names = normalize_declined_slots(list(declined_entities or []))
    if not names or not isinstance(entity_schema, Mapping):
        return []
    fields = entity_schema.get("fields")
    if not isinstance(fields, list):
        return []
    by_name = {
        raw.get("name"): raw
        for raw in fields
        if isinstance(raw, Mapping) and isinstance(raw.get("name"), str)
    }
    keys: List[str] = []
    seen: Set[str] = set()
    for name in names:
        field = by_name.get(name)
        if not isinstance(field, Mapping):
            continue
        key = planning_slot_key_for_field(field)
        if key is None or key in seen:
            continue
        keys.append(key)
        seen.add(key)
    return keys


def promptable_slot_keys_from_entity_schema(
    entity_schema: Optional[Mapping[str, Any]],
) -> List[str]:
    """Ordered planning keys for optional fields with ``prompt_if_missing: true``.

    Ignored when ``required: true`` (prompt_if_missing must not apply).
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
        if raw.get("required") is True:
            continue
        if raw.get("prompt_if_missing") is not True:
            continue
        key = planning_slot_key_for_field(raw)
        if key is None or key in seen:
            continue
        keys.append(key)
        seen.add(key)
    return keys


def derive_promptable_slots(
    entity_schema: Optional[Mapping[str, Any]],
    slots: Optional[Mapping[str, Any]] = None,
    declined_slots: Optional[Sequence[str]] = None,
) -> List[str]:
    """Promptable optional slots still needing an offer (not selected/declined).

    Search-criteria promptables require the bookable_item planning slot first.
    Catalog cardinality ≤ 1 skips the prompt (platform rule).
    """
    collected = slots if isinstance(slots, Mapping) else {}
    declined = set(normalize_declined_slots(list(declined_slots or [])))
    result: List[str] = []
    if not isinstance(entity_schema, Mapping):
        return result
    fields = entity_schema.get("fields")
    if not isinstance(fields, list):
        return result
    search_keys = search_criteria_slot_keys_from_entity_schema(entity_schema)
    bookable_ready = _bookable_item_collected(collected, entity_schema)

    for raw in fields:
        if not isinstance(raw, Mapping):
            continue
        if raw.get("required") is True:
            continue
        if raw.get("prompt_if_missing") is not True:
            continue
        key = planning_slot_key_for_field(raw)
        if key is None:
            continue
        if key in declined or _slot_filled(collected, key):
            continue
        if not has_meaningful_catalog_choice(raw):
            continue
        if key in search_keys and not bookable_ready:
            continue
        if key not in result:
            result.append(key)
    return result


def unresolved_search_promptables(
    promptable_slots: Sequence[str],
    entity_schema: Optional[Mapping[str, Any]],
) -> List[str]:
    """Subset of promptable slots that participate in availability criteria."""
    search_keys = search_criteria_slot_keys_from_entity_schema(entity_schema)
    return [key for key in promptable_slots if key in search_keys]


def apply_preference_decline(
    *,
    declined_slots: Sequence[str],
    turn_declined_slots: Sequence[str],
    slots: Mapping[str, Any],
) -> List[str]:
    """Merge durable declined preferences for this turn.

    ``turn_declined_slots`` are planning keys mapped from Stage 2
    ``declined_entities``. Selecting a value clears a prior decline for that key.
    """
    declined = normalize_declined_slots(list(declined_slots))
    declined_set = set(declined)
    for key in normalize_declined_slots(list(turn_declined_slots)):
        if key not in declined_set:
            declined.append(key)
            declined_set.add(key)

    for key in list(declined):
        if _slot_filled(slots, key):
            declined_set.discard(key)
    return [k for k in declined if k in declined_set]
