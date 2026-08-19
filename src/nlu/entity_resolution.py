"""Strict, schema-validated business entity resolution contract."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from typing_extensions import Annotated


class EntityResolutionValidationError(ValueError):
    """Raised when generated entity resolution evidence violates the contract."""


class EntityExtractionValidationError(EntityResolutionValidationError):
    """Raised when Stage 2 generated entity evidence has an invalid shape."""


class MentionState(str, Enum):
    NOT_MENTIONED = "NOT_MENTIONED"
    MENTIONED_VALUE = "MENTIONED_VALUE"
    MENTIONED_UNRESOLVED = "MENTIONED_UNRESOLVED"


class EntityMentionEvidence(BaseModel):
    """Internal extraction evidence; never serialized in the public response."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    entity_name: str
    state: MentionState
    raw_value: Optional[str] = None

    @model_validator(mode="after")
    def state_matches_value(self) -> "EntityMentionEvidence":
        has_value = isinstance(self.raw_value, str) and bool(self.raw_value.strip())
        if self.state == MentionState.MENTIONED_VALUE and not has_value:
            raise ValueError("MENTIONED_VALUE requires a non-empty raw_value")
        if self.state != MentionState.MENTIONED_VALUE and self.raw_value is not None:
            raise ValueError(f"{self.state.value} must not carry raw_value")
        return self


def validate_generated_entity_evidence(raw_facts: Any, raw_mentions: Any, compiled: Any):
    """Strict Stage 2 boundary validation before defaults or projections exist."""
    if not isinstance(raw_facts, Mapping):
        raise EntityExtractionValidationError("generated facts must be an object")
    if not isinstance(raw_mentions, Mapping):
        raise EntityExtractionValidationError("generated entity_mentions must be an object")
    expected = {field.name for field in compiled.fields}
    fact_keys = set(raw_facts.keys())
    if fact_keys != expected:
        raise EntityExtractionValidationError(
            f"generated facts keys must exactly match schema fields; "
            f"missing={sorted(expected - fact_keys)!r}, unknown={sorted(fact_keys - expected)!r}"
        )
    actual = set(raw_mentions.keys())
    if actual != expected:
        raise EntityExtractionValidationError(
            f"entity_mentions keys must exactly match schema fields; "
            f"missing={sorted(expected - actual)!r}, unknown={sorted(actual - expected)!r}"
        )
    evidence: Dict[str, EntityMentionEvidence] = {}
    for entity in compiled.fields:
        mentioned = raw_mentions[entity.name]
        if type(mentioned) is not bool:
            raise EntityExtractionValidationError(f"entity_mentions.{entity.name} must be a boolean")
        value = raw_facts.get(entity.name)
        if value is not None and not isinstance(value, str):
            raise EntityExtractionValidationError(f"facts.{entity.name} must be a string or null")
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise EntityExtractionValidationError(f"facts.{entity.name} must not be empty")
        if not mentioned and value is not None:
            raise EntityExtractionValidationError(
                f"facts.{entity.name} has a value while entity_mentions is false"
            )
        state = (MentionState.NOT_MENTIONED if not mentioned else
                 MentionState.MENTIONED_VALUE if value is not None else
                 MentionState.MENTIONED_UNRESOLVED)
        evidence[entity.name] = EntityMentionEvidence(
            entity_name=entity.name, state=state,
            raw_value=value if state == MentionState.MENTIONED_VALUE else None,
        )
    return evidence


def validate_generated_entity_results(raw_results: Any, compiled: Any):
    """Validate the atomic Stage 2 entity-result representation."""
    if not isinstance(raw_results, Mapping):
        raise EntityExtractionValidationError("generated entity_results must be an object")
    expected = {field.name for field in compiled.fields}
    actual = set(raw_results.keys())
    if actual != expected:
        raise EntityExtractionValidationError(
            f"entity_results keys must exactly match schema fields; "
            f"missing={sorted(expected - actual)!r}, unknown={sorted(actual - expected)!r}"
        )

    evidence: Dict[str, EntityMentionEvidence] = {}
    for entity in compiled.fields:
        raw = raw_results[entity.name]
        if not isinstance(raw, Mapping):
            raise EntityExtractionValidationError(
                f"entity_results.{entity.name} must be an object"
            )
        status = raw.get("status")
        if status == MentionState.MENTIONED_VALUE.value:
            if set(raw.keys()) != {"status", "value"}:
                raise EntityExtractionValidationError(
                    f"entity_results.{entity.name} MENTIONED_VALUE requires only status and value"
                )
            value = raw.get("value")
            if not isinstance(value, str) or not value.strip():
                raise EntityExtractionValidationError(
                    f"entity_results.{entity.name}.value must be a non-empty string"
                )
            evidence[entity.name] = EntityMentionEvidence(
                entity_name=entity.name,
                state=MentionState.MENTIONED_VALUE,
                raw_value=value.strip(),
            )
            continue
        if status in {
            MentionState.NOT_MENTIONED.value,
            MentionState.MENTIONED_UNRESOLVED.value,
        }:
            if set(raw.keys()) != {"status"}:
                raise EntityExtractionValidationError(
                    f"entity_results.{entity.name} {status} must not carry a value"
                )
            evidence[entity.name] = EntityMentionEvidence(
                entity_name=entity.name,
                state=MentionState(status),
            )
            continue
        raise EntityExtractionValidationError(
            f"entity_results.{entity.name}.status is invalid"
        )
    return evidence


class Resolution(str, Enum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"


_NON_NAME_PLACEHOLDERS = frozenset({
    "guest",
    "anonymous",
    "unknown",
    "none",
    "n/a",
    "na",
    "no name",
    "i don't know",
    "i do not know",
    "not sure",
})


def usable_customer_contact_name(value: Any) -> Optional[str]:
    """Validate normalized semantic output for the contact-name entity."""
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized or normalized.casefold() in _NON_NAME_PLACEHOLDERS:
        return None
    return normalized


class _StrictResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ResolvedEntity(_StrictResult):
    resolution: Literal[Resolution.RESOLVED]
    value: Any

    @model_validator(mode="after")
    def value_is_present(self) -> "ResolvedEntity":
        if self.value is None:
            raise ValueError("RESOLVED requires a non-null value")
        return self


class AmbiguousEntity(_StrictResult):
    resolution: Literal[Resolution.AMBIGUOUS]
    candidate_values: List[Any] = Field(min_length=2)

    @model_validator(mode="after")
    def candidates_are_distinct(self) -> "AmbiguousEntity":
        if any(value is None for value in self.candidate_values):
            raise ValueError("AMBIGUOUS candidates must be non-null")
        for index, value in enumerate(self.candidate_values):
            if value in self.candidate_values[:index]:
                raise ValueError("AMBIGUOUS candidates must be distinct")
        return self


class UnresolvedEntity(_StrictResult):
    resolution: Literal[Resolution.UNRESOLVED]


EntityResolutionResult = Annotated[
    Union[ResolvedEntity, AmbiguousEntity, UnresolvedEntity],
    Field(discriminator="resolution"),
]
_RESULT_ADAPTER = TypeAdapter(EntityResolutionResult)


def validate_entity_resolutions(
    raw: Any, compiled: Any
) -> Dict[str, EntityResolutionResult]:
    """Validate shape, declared names, types, and canonical membership."""
    if not isinstance(raw, Mapping):
        raise EntityResolutionValidationError("entity_resolutions must be an object")
    fields = {field.name: field for field in compiled.fields}
    output: Dict[str, EntityResolutionResult] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or name not in fields:
            raise EntityResolutionValidationError(f"unknown entity name: {name!r}")
        try:
            result = _RESULT_ADAPTER.validate_python(value)
        except Exception as exc:
            raise EntityResolutionValidationError(
                f"invalid resolution for entity {name!r}: {exc}"
            ) from exc
        entity = fields[name]
        values = ([result.value] if isinstance(result, ResolvedEntity) else
                  result.candidate_values if isinstance(result, AmbiguousEntity) else [])
        if entity.type == "catalog":
            allowed = list(entity.catalog.values())
            def allowed_catalog_value(candidate: Any) -> bool:
                return any(type(candidate) is type(item) and candidate == item for item in allowed)
            if any(not allowed_catalog_value(candidate) for candidate in values):
                raise EntityResolutionValidationError(
                    f"entity {name!r} contains a value outside its catalog"
                )
        elif entity.type == "enum":
            if any(not isinstance(candidate, str) or candidate not in entity.values
                   for candidate in values):
                raise EntityResolutionValidationError(
                    f"entity {name!r} contains an invalid enum value"
                )
        elif entity.type == "text":
            if any(not isinstance(candidate, str) for candidate in values):
                raise EntityResolutionValidationError(
                    f"entity {name!r} requires string values"
                )
        output[name] = result
    return output


def serialize_entity_resolutions(
    resolutions: Mapping[str, EntityResolutionResult]
) -> Dict[str, Dict[str, Any]]:
    return {
        name: result.model_dump(mode="json", exclude_none=True)
        for name, result in resolutions.items()
    }
