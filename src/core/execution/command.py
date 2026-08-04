"""Typed execution command and operational block results.

Planning/Decision authorizes an action and emits ``ExecutionCommand``.
ExecutionCoordinator consumes the command and validates operational
prerequisites only — it must not re-evaluate planning eligibility.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Optional


def _freeze_mapping(value: Optional[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return None
    return MappingProxyType(deepcopy(dict(value)))


@dataclass(frozen=True)
class ExecutionCommand:
    """Authorized execution intent produced by Decision.

    Does not carry mutable plan, clients, session stores, or response text.
    """

    action: str
    client_name: str
    intent_name: str
    mode: str
    slots: Mapping[str, Any]
    organization_id: int
    execution_proposal_context: Optional[Mapping[str, Any]] = None
    entity_schema: Optional[Mapping[str, Any]] = None
    turn_operation: Optional[str] = None
    stage: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "slots", _freeze_mapping(dict(self.slots)) or MappingProxyType({}))
        object.__setattr__(
            self,
            "execution_proposal_context",
            _freeze_mapping(self.execution_proposal_context),
        )
        object.__setattr__(self, "entity_schema", _freeze_mapping(self.entity_schema))


@dataclass(frozen=True)
class ExecutionBlocked:
    """Operational block — Execution does not invent conversational wording."""

    reason: str
    required_input: Optional[str] = None
    action: Optional[str] = None


class ExecutionCommandError(ValueError):
    """Decision selected an action that cannot be mapped to a policy step."""
