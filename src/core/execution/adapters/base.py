"""Execution adapter contracts and immutable prepared inputs."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional

from core.execution.command import ExecutionBlocked, ExecutionCommand

logger = logging.getLogger(__name__)


def _freeze_mapping(value: Optional[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return None
    return MappingProxyType(deepcopy(dict(value)))


@dataclass(frozen=True)
class PreparedExecution:
    """Operational execution inputs — no planner Decision state."""

    action: str
    slots: Mapping[str, Any]
    stage: Optional[str] = None
    sku_to_catalog_id: Optional[Mapping[str, Any]] = None
    facts: Optional[Mapping[str, Any]] = None
    execution_proposal_context: Optional[Mapping[str, Any]] = None
    entity_schema: Optional[Mapping[str, Any]] = None
    turn_operation: Optional[str] = None
    blocked: Optional[ExecutionBlocked] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "slots", _freeze_mapping(dict(self.slots)) or MappingProxyType({})
        )
        object.__setattr__(
            self, "sku_to_catalog_id", _freeze_mapping(self.sku_to_catalog_id)
        )
        object.__setattr__(self, "facts", _freeze_mapping(self.facts))
        object.__setattr__(
            self,
            "execution_proposal_context",
            _freeze_mapping(self.execution_proposal_context),
        )
        object.__setattr__(self, "entity_schema", _freeze_mapping(self.entity_schema))


def apply_organization_id(
    slots: Dict[str, Any], *, organization_id: int
) -> Dict[str, Any]:
    """Enforce authoritative organization_id on execution slots."""
    slot_org = slots.get("organization_id")
    if slot_org is not None and int(slot_org) != int(organization_id):
        logger.warning(
            "Ignoring conflicting slots.organization_id=%s (request=%s)",
            slot_org,
            organization_id,
        )
    slots["organization_id"] = organization_id
    return slots


def inject_customer_id(
    slots: Dict[str, Any],
    *,
    session_state: Optional[Dict[str, Any]],
    kwargs: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Inject already-resolved customer_id into slots (no lookup)."""
    from core.adapters.customer_resolver import coerce_positive_customer_id

    kw = kwargs or {}
    customer_id = coerce_positive_customer_id(kw.get("customer_id"))
    if customer_id is None and isinstance(session_state, dict):
        customer_id = coerce_positive_customer_id(session_state.get("customer_id"))
    if customer_id is None:
        customer_id = coerce_positive_customer_id(slots.get("customer_id"))
    if customer_id is not None:
        slots["customer_id"] = customer_id
    return slots


def load_catalog_mapping(
    *,
    organization_id: int,
    organization_client: Optional[Any],
    catalog_client: Optional[Any] = None,
) -> Dict[str, Any]:
    try:
        from core.execution.catalog_resolver import load_sku_to_catalog_id_for_org

        return load_sku_to_catalog_id_for_org(
            organization_id,
            organization_client,
            catalog_client=catalog_client,
        )
    except Exception as e:
        logger.debug("Could not load sku_to_catalog_id for execution: %s", e)
        return {}


class ExecutionAdapter(ABC):
    """Prepares operational inputs for a family of execution actions."""

    @abstractmethod
    def prepare(
        self,
        command: ExecutionCommand,
        session_state: Optional[Dict[str, Any]],
        organization_id: int,
        *,
        organization_client: Optional[Any] = None,
        catalog_client: Optional[Any] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        plan_snapshot: Optional[Mapping[str, Any]] = None,
    ) -> PreparedExecution:
        """Return immutable prepared execution inputs (or a typed block).

        ``plan_snapshot`` is a read-only copy of the Decision plan used only for
        operational proposal resolution (e.g. availability). Adapters must not
        mutate it or treat it as planner ownership.
        """
