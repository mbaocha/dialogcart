"""Booking-domain execution preparation."""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from core.execution.adapters.base import (
    ExecutionAdapter,
    PreparedExecution,
    apply_organization_id,
    inject_customer_id,
    load_catalog_mapping,
)
from core.execution.command import ExecutionBlocked, ExecutionCommand

logger = logging.getLogger(__name__)

_CUSTOMER_GATE_ACTIONS = frozenset({"CONFIRM_APPOINTMENT", "CREATE_BOOKING_HOLD"})


class BookingAdapter(ExecutionAdapter):
    """Prepare booking / reservation / fetch / modify / cancel inputs."""

    def prepare(
        self,
        command: ExecutionCommand,
        session_state: Optional[Dict[str, Any]],
        organization_id: int,
        *,
        organization_client: Optional[Any] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        plan_snapshot: Optional[Mapping[str, Any]] = None,
    ) -> PreparedExecution:
        _ = plan_snapshot  # Booking prep does not consume Decision plan fields.
        slots = dict(command.slots)
        slots = apply_organization_id(slots, organization_id=organization_id)
        slots = inject_customer_id(
            slots, session_state=session_state, kwargs=kwargs
        )

        stage = command.stage
        if command.action == "CONFIRM_APPOINTMENT":
            stage = "CONFIRM"
            slots = self._inject_bound_datetime(slots, session_state=session_state)

        facts = None
        if command.action == "FINALIZE_RESERVATION":
            facts = self._finalize_facts(
                session_state=session_state,
                organization_client=organization_client,
                organization_id=organization_id,
            )

        blocked = None
        if command.action in _CUSTOMER_GATE_ACTIONS:
            from core.adapters.customer_resolver import coerce_positive_customer_id

            if coerce_positive_customer_id(slots.get("customer_id")) is None:
                logger.info(
                    "Blocking %s: tenant customer_id is unresolved",
                    command.action,
                )
                blocked = ExecutionBlocked(
                    reason="CUSTOMER_ID_REQUIRED",
                    required_input="phone_or_email",
                    action=command.action,
                )

        return PreparedExecution(
            action=command.action,
            slots=slots,
            stage=stage,
            sku_to_catalog_id=load_catalog_mapping(
                organization_id=organization_id,
                organization_client=organization_client,
            ),
            facts=facts,
            entity_schema=(
                dict(command.entity_schema)
                if command.entity_schema is not None
                else None
            ),
            turn_operation=command.turn_operation,
            blocked=blocked,
        )

    @staticmethod
    def _inject_bound_datetime(
        slots: Dict[str, Any],
        *,
        session_state: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if "datetime_range" in slots and isinstance(
            slots.get("datetime_range"), dict
        ):
            return slots

        resolved_datetime_range = None
        if isinstance(session_state, dict):
            resolved_datetime_range = session_state.get("resolved_datetime_range")
            if not resolved_datetime_range:
                planning = session_state.get("planning")
                if isinstance(planning, dict):
                    resolved_datetime_range = planning.get("bound_datetime")

        if resolved_datetime_range and isinstance(resolved_datetime_range, dict):
            slots["datetime_range"] = resolved_datetime_range
            logger.debug(
                "[DATETIME_RANGE] Injected resolved_datetime_range into "
                f"slots for CONFIRM_APPOINTMENT: "
                f"start={resolved_datetime_range.get('start')}, "
                f"end={resolved_datetime_range.get('end')}"
            )
        return slots

    @staticmethod
    def _finalize_facts(
        *,
        session_state: Optional[Dict[str, Any]],
        organization_client: Optional[Any],
        organization_id: int,
    ) -> Optional[Dict[str, Any]]:
        plan_facts: Dict[str, Any] = {}
        if session_state and isinstance(session_state, dict):
            plan_facts = session_state.get("facts", {})
            if not isinstance(plan_facts, dict):
                plan_facts = {}

        if organization_client:
            if not plan_facts.get("org"):
                try:
                    org_details = organization_client.get_details(organization_id)
                    if isinstance(org_details, dict):
                        org_data = org_details.get("organization") or org_details
                        if org_data and isinstance(org_data, dict):
                            if not plan_facts:
                                plan_facts = {}
                            plan_facts["org"] = org_data
                except Exception as e:
                    logger.debug(
                        "Failed to fetch org data for FINALIZE_RESERVATION "
                        f"payment verification: {e}"
                    )

        if plan_facts:
            logger.debug(
                "Added facts to plan for FINALIZE_RESERVATION execution "
                "(payment verification)"
            )
            return plan_facts
        return None
