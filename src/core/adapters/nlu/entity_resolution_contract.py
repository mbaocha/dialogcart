"""Typed Core mirror of NLU's public ``entity_resolutions`` contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple

from core.adapters.errors import ContractViolation
from core.adapters.nlu.entity_schema_builder import planning_slot_key_for_field


class EntityResolutionState(str, Enum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class EntityResolutionEvidence:
    entity_name: str
    slot_key: str
    resolution: EntityResolutionState
    value: Any = None
    candidate_values: Tuple[Any, ...] = ()

    def to_public_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"resolution": self.resolution.value}
        if self.resolution == EntityResolutionState.RESOLVED:
            result["value"] = self.value
        elif self.resolution == EntityResolutionState.AMBIGUOUS:
            result["candidate_values"] = list(self.candidate_values)
        return result


def _same_typed_value(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _schema_fields(entity_schema: Optional[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    if not isinstance(entity_schema, Mapping):
        return {}
    fields = entity_schema.get("fields")
    if not isinstance(fields, list):
        raise ContractViolation("entity_schema.fields must be a list")
    result: Dict[str, Mapping[str, Any]] = {}
    for field in fields:
        if not isinstance(field, Mapping) or not isinstance(field.get("name"), str):
            raise ContractViolation("entity_schema fields must have string names")
        result[str(field["name"])] = field
    return result


def _validate_canonical(value: Any, field: Mapping[str, Any], entity_name: str) -> None:
    field_type = field.get("type")
    if field_type == "catalog":
        catalog = field.get("catalog")
        allowed = list(catalog.values()) if isinstance(catalog, Mapping) else []
        if not any(_same_typed_value(value, item) for item in allowed):
            raise ContractViolation(
                f"entity_resolutions.{entity_name} contains a value outside its catalog"
            )
    elif field_type == "enum":
        values = field.get("values")
        if not isinstance(value, str) or not isinstance(values, list) or value not in values:
            raise ContractViolation(
                f"entity_resolutions.{entity_name} contains an invalid enum value"
            )
    elif field_type == "text":
        if not isinstance(value, str):
            raise ContractViolation(
                f"entity_resolutions.{entity_name} requires a string value"
            )


def parse_entity_resolutions(
    response: Mapping[str, Any],
    *,
    entity_schema: Optional[Mapping[str, Any]],
) -> Mapping[str, EntityResolutionEvidence]:
    """Validate and freeze authoritative current-turn entity evidence."""
    raw = response.get("entity_resolutions")
    if not isinstance(raw, Mapping):
        raise ContractViolation("entity_resolutions must be an object")
    fields = _schema_fields(entity_schema)
    legacy_facts = response.get("facts")
    if not isinstance(legacy_facts, Mapping):
        legacy_facts = {}
    output: Dict[str, EntityResolutionEvidence] = {}
    for entity_name, payload in raw.items():
        if not isinstance(entity_name, str) or entity_name not in fields:
            raise ContractViolation(f"unknown entity resolution name: {entity_name!r}")
        if not isinstance(payload, Mapping):
            raise ContractViolation(f"entity_resolutions.{entity_name} must be an object")
        try:
            state = EntityResolutionState(payload.get("resolution"))
        except (TypeError, ValueError) as exc:
            raise ContractViolation(
                f"entity_resolutions.{entity_name} has an invalid resolution"
            ) from exc
        expected = {"resolution"}
        value = None
        candidates: Tuple[Any, ...] = ()
        if state == EntityResolutionState.RESOLVED:
            expected.add("value")
            value = payload.get("value")
            if value is None:
                raise ContractViolation(f"entity_resolutions.{entity_name} RESOLVED requires value")
            _validate_canonical(value, fields[entity_name], entity_name)
        elif state == EntityResolutionState.AMBIGUOUS:
            expected.add("candidate_values")
            raw_candidates = payload.get("candidate_values")
            if not isinstance(raw_candidates, list) or len(raw_candidates) < 2:
                raise ContractViolation(
                    f"entity_resolutions.{entity_name} AMBIGUOUS requires at least two candidates"
                )
            for index, candidate in enumerate(raw_candidates):
                if candidate is None or any(
                    _same_typed_value(candidate, prior) for prior in raw_candidates[:index]
                ):
                    raise ContractViolation(
                        f"entity_resolutions.{entity_name} candidates must be non-null and distinct"
                    )
                _validate_canonical(candidate, fields[entity_name], entity_name)
            candidates = tuple(raw_candidates)
        if set(payload.keys()) != expected:
            raise ContractViolation(
                f"entity_resolutions.{entity_name} has fields inconsistent with {state.value}"
            )
        slot_key = planning_slot_key_for_field(fields[entity_name])
        if not slot_key:
            raise ContractViolation(f"entity {entity_name!r} has no planning slot mapping")
        output[entity_name] = EntityResolutionEvidence(
            entity_name=entity_name,
            slot_key=slot_key,
            resolution=state,
            value=value,
            candidate_values=candidates,
        )
        legacy_value = legacy_facts.get(slot_key)
        if state == EntityResolutionState.RESOLVED and legacy_value is not None:
            consistent = _same_typed_value(legacy_value, value)
            catalog = fields[entity_name].get("catalog")
            if not consistent and isinstance(legacy_value, str) and isinstance(catalog, Mapping):
                mapped = next(
                    (
                        candidate
                        for phrase, candidate in catalog.items()
                        if str(phrase).casefold() == legacy_value.casefold()
                    ),
                    None,
                )
                consistent = _same_typed_value(mapped, value)
            if not consistent:
                raise ContractViolation(
                    f"legacy facts.{slot_key} disagrees with entity_resolutions.{entity_name}"
                )
        elif state != EntityResolutionState.RESOLVED and legacy_value is not None:
            raise ContractViolation(
                f"legacy facts.{slot_key} must be null for {state.value} entity {entity_name}"
            )
    for entity_name, field in fields.items():
        if entity_name in output:
            continue
        slot_key = planning_slot_key_for_field(field)
        if slot_key and legacy_facts.get(slot_key) is not None:
            raise ContractViolation(
                f"legacy facts.{slot_key} claims an entity omitted from entity_resolutions"
            )
    return MappingProxyType(output)


def serialize_entity_resolution_evidence(
    evidence: Mapping[str, EntityResolutionEvidence],
) -> Dict[str, Dict[str, Any]]:
    return {name: item.to_public_dict() for name, item in evidence.items()}


def project_authoritative_entity_values(
    response: Mapping[str, Any],
    *,
    entity_schema: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return a response whose schema-owned slots reflect only authoritative evidence."""
    from core.adapters.nlu.entity_schema_builder import (
        promotable_slot_keys_from_entity_schema,
    )

    projected = dict(response)
    facts = dict(projected.get("facts") or {})
    slots = dict(projected.get("slots") or {})
    fields = entity_schema.get("fields")
    raw_resolutions = projected.get("entity_resolutions")
    resolution_names = (
        set(raw_resolutions.keys()) if isinstance(raw_resolutions, Mapping) else set()
    )
    promotable_slot_keys = promotable_slot_keys_from_entity_schema(entity_schema)
    # Boundary validation has already admitted the response. At this internal
    # projection seam, stale compatibility values for omitted entities are
    # discarded before parsing; omission remains a semantic no-op.
    for field in fields if isinstance(fields, list) else []:
        if not isinstance(field, Mapping) or field.get("name") in resolution_names:
            continue
        slot_key = planning_slot_key_for_field(field)
        if slot_key:
            facts.pop(slot_key, None)
            slots.pop(slot_key, None)
    projected["facts"] = facts
    projected["slots"] = slots
    evidence = parse_entity_resolutions(projected, entity_schema=entity_schema)
    for field in fields if isinstance(fields, list) else []:
        if not isinstance(field, Mapping):
            continue
        slot_key = planning_slot_key_for_field(field)
        if not slot_key:
            continue
        facts.pop(slot_key, None)
        slots.pop(slot_key, None)
        if field.get("type") == "catalog":
            entity_name = field.get("name")
            if isinstance(entity_name, str):
                facts.pop(entity_name, None)
                slots.pop(entity_name, None)
        item = evidence.get(str(field.get("name") or ""))
        if (
            slot_key in promotable_slot_keys
            and item is not None
            and item.resolution == EntityResolutionState.RESOLVED
        ):
            facts[slot_key] = item.value
            slots[slot_key] = item.value
    projected["facts"] = facts
    projected["slots"] = slots
    return projected
