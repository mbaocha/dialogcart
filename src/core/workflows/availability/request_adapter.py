"""Translate planning slots into availability request parameters.

Planner code uses canonical planning slot keys. Only this adapter maps those
keys onto backend availability API parameter names / extra query params.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from core.adapters.nlu.entity_schema_builder import (
    search_criteria_slot_keys_from_entity_schema,
)

# Planning keys that become top-level service-availability client arguments.
_TOP_LEVEL_SERVICE_KEYS = frozenset({"service_id", "date", "start_date"})

# Planning key → backend query parameter name for pass-through criteria.
_SERVICE_PARAM_NAMES: Dict[str, str] = {
    "staff_id": "staff_id",
    "location": "location",
    "resource": "resource",
    "resource_id": "resource_id",
}


def _slot_value(slots: Mapping[str, Any], key: str) -> Any:
    value = slots.get(key)
    if value is None or value == "":
        return None
    return value


def _extract_date(slots: Mapping[str, Any]) -> Optional[str]:
    date = _slot_value(slots, "date") or _slot_value(slots, "start_date")
    if date is not None:
        return str(date)
    date_range = slots.get("date_range")
    if isinstance(date_range, dict):
        start = date_range.get("start") or date_range.get("start_date")
        if start is not None and start != "":
            return str(start)
    return None


def build_service_availability_request(
    slots: Mapping[str, Any],
    *,
    organization_id: Any,
    api_service_id: Any,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the canonical service availability request from planning slots.

    Returns:
        {
          "organization_id": int,
          "service_id": Any,
          "date": Optional[str],
          "extra_params": Dict[str, Any],  # backend query params for criteria
          "identity": Dict[str, Any],      # flat criteria identity (fingerprint)
        }
    """
    criteria_keys = search_criteria_slot_keys_from_entity_schema(entity_schema)
    date = _extract_date(slots) if ("date" in criteria_keys or "start_date" in criteria_keys) else None

    extra_params: Dict[str, Any] = {}
    identity: Dict[str, Any] = {
        "organization_id": organization_id,
        "service_id": api_service_id,
    }
    if date is not None:
        identity["date"] = date

    for key in sorted(criteria_keys):
        if key in _TOP_LEVEL_SERVICE_KEYS or key in ("date_range",):
            continue
        value = _slot_value(slots, key)
        if value is None:
            continue
        param_name = _SERVICE_PARAM_NAMES.get(key, key)
        extra_params[param_name] = value
        identity[key] = value

    return {
        "organization_id": organization_id,
        "service_id": api_service_id,
        "date": date,
        "extra_params": extra_params,
        "identity": identity,
    }
