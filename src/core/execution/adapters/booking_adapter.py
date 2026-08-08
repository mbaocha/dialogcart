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


def _slot_identifier_present(slots: Mapping[str, Any]) -> bool:
    booking_id = slots.get("booking_id")
    booking_code = slots.get("booking_code")
    if booking_id is not None and str(booking_id).strip() != "":
        return True
    if booking_code is not None and str(booking_code).strip() != "":
        return True
    return False


def _try_infer_most_recent_booking(
    slots: Dict[str, Any],
    *,
    kwargs: Optional[Dict[str, Any]],
) -> bool:
    """If booking client can resolve a most-recent booking, inject ids into slots.

    Returns True when identification is now present on ``slots``.
    """
    if _slot_identifier_present(slots):
        return True
    booking_client = (kwargs or {}).get("booking_client")
    if booking_client is None or not hasattr(booking_client, "get_most_recent_booking"):
        return False
    organization_id = slots.get("organization_id")
    if not organization_id:
        return False
    try:
        most_recent = booking_client.get_most_recent_booking(
            organization_id=organization_id,
            customer_id=slots.get("customer_id"),
        )
    except Exception as exc:
        logger.debug("FETCH_BOOKING most-recent inference failed: %s", exc)
        return False
    if not most_recent:
        return False
    booking_data = (
        most_recent.get("booking")
        if isinstance(most_recent, dict) and "booking" in most_recent
        else most_recent
    )
    if not isinstance(booking_data, dict):
        return False
    booking_id = booking_data.get("id") or booking_data.get("booking_id")
    booking_code = booking_data.get("booking_code")
    if booking_id is not None and str(booking_id).strip() != "":
        slots["booking_id"] = booking_id
    if booking_code is not None and str(booking_code).strip() != "":
        slots["booking_code"] = booking_code
    return _slot_identifier_present(slots)


def _inject_committed_booking_ids(
    slots: Dict[str, Any],
    *,
    session_state: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Copy Session V2 booking.* ids into the execution-input slots copy only."""
    if _slot_identifier_present(slots):
        return slots
    if not isinstance(session_state, dict):
        return slots
    booking = session_state.get("booking")
    booking_id = None
    booking_code = None
    if isinstance(booking, dict):
        booking_id = booking.get("booking_id")
        booking_code = booking.get("booking_code")
    if booking_id is None:
        booking_id = session_state.get("booking_id")
    if booking_code is None:
        booking_code = session_state.get("booking_code")
    if booking_id is not None and str(booking_id).strip() != "":
        slots["booking_id"] = booking_id
    if booking_code is not None and str(booking_code).strip() != "":
        slots["booking_code"] = booking_code
    return slots


class BookingAdapter(ExecutionAdapter):
    """Prepare booking / reservation / fetch / modify / cancel inputs."""

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
        _ = plan_snapshot  # Booking prep does not consume Decision plan fields.
        slots = dict(command.slots)
        slots = apply_organization_id(slots, organization_id=organization_id)
        slots = inject_customer_id(
            slots, session_state=session_state, kwargs=kwargs
        )
        # Execution-input only — does not mutate Decision plan.slots.
        slots = _inject_committed_booking_ids(slots, session_state=session_state)

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
        elif command.action == "FETCH_BOOKING":
            if not _try_infer_most_recent_booking(slots, kwargs=kwargs):
                logger.info(
                    "Blocking FETCH_BOOKING: booking identification unresolved"
                )
                blocked = ExecutionBlocked(
                    reason="BOOKING_IDENTIFICATION_REQUIRED",
                    required_input="booking_id",
                    action=command.action,
                )

        return PreparedExecution(
            action=command.action,
            slots=slots,
            stage=stage,
            sku_to_catalog_id=load_catalog_mapping(
                organization_id=organization_id,
                organization_client=organization_client,
                catalog_client=catalog_client,
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
