"""Domain-specific slot manipulation for session merge and effective-slot computation."""

from typing import Any, Dict, FrozenSet, Set

# Shared durable slots valid in both service and reservation domains.
SHARED_DOMAIN_SLOTS: FrozenSet[str] = frozenset(
    {"service_id", "date_range", "booking_id", "booking_code"}
)

# Service-domain durable slots (CREATE_APPOINTMENT, MODIFY_BOOKING).
SERVICE_DOMAIN_SLOTS: FrozenSet[str] = frozenset(
    {"date", "time", "has_datetime"} | SHARED_DOMAIN_SLOTS
)

# Reservation-domain durable slots (CREATE_RESERVATION, MODIFY_RESERVATION).
RESERVATION_DOMAIN_SLOTS: FrozenSet[str] = frozenset(
    {"start_date", "end_date"} | SHARED_DOMAIN_SLOTS
)

# MODIFY_BOOKING may carry reservation-shaped delta slots from NLU promotion.
MODIFY_BOOKING_DELTA_SLOTS: FrozenSet[str] = frozenset(
    {"start_date", "end_date", "duration"}
)

# Internal planning/execution keys — not cross-domain booking facts; always preserved.
INTERNAL_SLOT_PASSTHROUGH: FrozenSet[str] = frozenset({"_canonical_service_id"})

SERVICE_DOMAIN_INTENTS: FrozenSet[str] = frozenset(
    {"CREATE_APPOINTMENT", "MODIFY_BOOKING"}
)
RESERVATION_DOMAIN_INTENTS: FrozenSet[str] = frozenset(
    {"CREATE_RESERVATION", "MODIFY_RESERVATION"}
)


def _domain_valid_slots(intent_name: str) -> Set[str] | None:
    """Return allowed durable slot names for intent domain, or None if intent is unknown."""
    if intent_name in SERVICE_DOMAIN_INTENTS:
        valid = set(SERVICE_DOMAIN_SLOTS)
        if intent_name == "MODIFY_BOOKING":
            valid |= MODIFY_BOOKING_DELTA_SLOTS
        return valid
    if intent_name in RESERVATION_DOMAIN_INTENTS:
        return set(RESERVATION_DOMAIN_SLOTS)
    return None


def filter_slots_by_domain(
    slots: Dict[str, Any],
    intent_name: str,
    *,
    apply_domain_filter: bool = True,
) -> Dict[str, Any]:
    """
    Filter slots to only include those valid for the intent's domain.

    ARCHITECTURAL INVARIANT: Domain slot isolation
    - Service domain (CREATE_APPOINTMENT, MODIFY_BOOKING): date, time, service_id, …
    - Reservation domain (CREATE_RESERVATION, MODIFY_RESERVATION): start_date, end_date, service_id, …
    - service_id, date_range, booking_id are shared across domains
    - date/time from service must NOT leak into reservation
    - start_date/end_date from reservation must NOT leak into service
    - Generic 'date' must NOT satisfy start_date/end_date for CREATE_RESERVATION

    This must be called BEFORE computing effective_collected_slots to prevent
    cross-domain slot leakage. Callers decide explicitly via apply_domain_filter;
    this is independent of planning-only outcome shaping.

    Args:
        slots: Slots dictionary to filter
        intent_name: Intent name to determine domain
        apply_domain_filter: When False, return a copy of all slots unchanged

    Returns:
        Filtered slots dictionary (domain-valid slots plus internal passthrough keys)
    """
    import logging

    logger = logging.getLogger(__name__)

    if not apply_domain_filter:
        logger.debug(
            "[DOMAIN_FILTER] apply_domain_filter=False: returning all slots unchanged"
        )
        return slots.copy() if slots else {}

    if not intent_name or not slots:
        return slots.copy() if slots else {}

    valid_slots = _domain_valid_slots(intent_name)
    if valid_slots is None:
        # Unknown intent - keep all slots (let other filters handle it)
        return slots.copy()

    # Filter slots to only valid ones for domain
    filtered = {}
    dropped = []

    for slot_name, slot_value in slots.items():
        if (
            slot_name in valid_slots
            or slot_name in INTERNAL_SLOT_PASSTHROUGH
            or slot_name.startswith("_")
        ):
            filtered[slot_name] = slot_value
        else:
            dropped.append(slot_name)
            logger.debug(
                f"[DOMAIN_FILTER] Dropping slot '{slot_name}' (not valid for {intent_name} domain, "
                f"valid_slots={valid_slots})"
            )

    if dropped:
        logger.info(
            f"[DOMAIN_FILTER] filter_slots_by_domain: "
            f"intent={intent_name}, "
            f"dropped_slots={dropped}, preserved_slots={list(filtered.keys())}"
        )

    return filtered


def filter_collected_slots_for_intent(
    collected_slots: Dict[str, Any], old_intent: str, new_intent: str
) -> Dict[str, Any]:
    """
    Filter collected slots when intent changes.

    ARCHITECTURAL INVARIANT: Intent change is a hard boundary
    - On intent change, drop slots that are not valid for the new intent
    - Preserve slots that overlap semantically (e.g., service_id if applicable)
    - Do NOT reuse old slot names (e.g. date/time must NOT leak into reservation)
    - start_date/end_date must NOT satisfy service date implicitly
    - Only keep slots that are in the new intent's slot universe

    This function must be STRICT to prevent cross-domain slot leakage.

    Args:
        collected_slots: Previously collected slots (effective_slots from session)
        old_intent: Previous intent name
        new_intent: New intent name

    Returns:
        Filtered collected slots (only slots valid for new intent)
    """
    import logging

    logger = logging.getLogger(__name__)

    if old_intent == new_intent:
        # Same intent - keep all slots
        return collected_slots.copy() if collected_slots else {}

    # Intent changed - only keep slots that are valid for new intent
    # CRITICAL: This must be strict to prevent slot leakage

    # Define valid slot universe for each intent (STRICT)
    valid_slots_by_intent = {
        "CREATE_APPOINTMENT": {
            "date",
            "time",
            "service_id",
            "has_datetime",
            "date_range",
        },
        "CREATE_RESERVATION": {"start_date", "end_date", "service_id", "date_range"},
        "MODIFY_BOOKING": {"booking_id"},
        "CANCEL_BOOKING": {"booking_id"},
    }

    valid_slots_new = valid_slots_by_intent.get(new_intent, set())

    # CRITICAL: Strict filtering - only keep slots in valid universe
    filtered = {}
    dropped = []

    for slot_name, slot_value in (collected_slots or {}).items():
        if slot_name in valid_slots_new:
            filtered[slot_name] = slot_value
        else:
            dropped.append(slot_name)
            logger.debug(
                f"[INTENT_CHANGE] Dropping slot '{slot_name}' (not valid for {new_intent}, "
                f"valid_slots={valid_slots_new})"
            )

    # Log filtering results
    if dropped:
        logger.info(
            f"[INTENT_CHANGE] filter_collected_slots_for_intent: "
            f"old_intent={old_intent}, new_intent={new_intent}, "
            f"dropped_slots={dropped}, preserved_slots={list(filtered.keys())}"
        )

    return filtered


def promote_slots_for_intent(
    raw_slots: Dict[str, Any], intent_name: str, context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Promote raw slots to intent-specific slots (in-memory, non-persistent).

    ARCHITECTURAL INVARIANT: Promotion must be IDEMPOTENT and ADDITIVE.
    - Promotion starts with ALL existing merged slots (session.slots + luma slots)
    - Promotion may ADD derived slots (e.g., date → start_date)
    - Promotion must NEVER remove or overwrite existing slots
    - Promotion must NOT depend on current-turn presence alone
    - If start_date exists in merged slots, it must remain even if no date_roles appear this turn
    - date_roles may ADD meaning but must not be required to PRESERVE slots
    - Promotion logic must be safe to run repeatedly with no side effects

    This runs BEFORE computing missing_slots but is NEVER persisted.
    Promotion rules are intent-scoped and role-aware.

    Args:
        raw_slots: Merged slots (session slots + luma slots) - durable facts that must be preserved
        intent_name: Intent name for promotion rules
        context: Context from Luma response (for date_roles, etc.)

    Returns:
        Promoted slots dict (raw_slots + promoted slots, non-persistent)
        All input slots are preserved - promotion is additive only
    """
    import logging

    logger = logging.getLogger(__name__)

    # CRITICAL: Start with copy of ALL existing merged slots - promotion is additive, never destructive
    promoted = raw_slots.copy() if raw_slots else {}

    if intent_name == "CREATE_RESERVATION":
        # Promotion rules for reservations
        date_roles = context.get("date_roles", []) if context else []

        # date_range → start_date + end_date (ADD only if both present AND slots don't already exist)
        date_range = raw_slots.get("date_range")
        if isinstance(date_range, dict):
            range_start = date_range.get("start")
            range_end = date_range.get("end")
            if range_start and range_end:
                if "start_date" not in promoted:
                    promoted["start_date"] = range_start
                if "end_date" not in promoted:
                    promoted["end_date"] = range_end

        # date → start_date (ADD only if date_roles explicitly indicates START_DATE AND start_date doesn't exist)
        if "date" in raw_slots and "START_DATE" in date_roles:
            if "start_date" not in promoted:
                promoted["start_date"] = raw_slots["date"]

        # date → end_date (ADD only if date_roles explicitly indicates END_DATE AND end_date doesn't exist)
        if "date" in raw_slots and "END_DATE" in date_roles:
            if "end_date" not in promoted:
                promoted["end_date"] = raw_slots["date"]

    elif intent_name == "CREATE_APPOINTMENT":
        # Promotion rules for service appointments
        # date_range → date (ADD only if date not already present)
        if "date_range" in raw_slots and "date" not in promoted:
            date_range = raw_slots.get("date_range")
            if isinstance(date_range, dict):
                promoted["date"] = (
                    date_range.get("start") or date_range.get("value") or date_range
                )
            else:
                promoted["date"] = date_range

        # date + time → has_datetime (for execution readiness)
        if "has_datetime" not in promoted:
            if ("date" in promoted and "time" in raw_slots) or (
                "date" in raw_slots and "time" in raw_slots
            ):
                promoted["has_datetime"] = True

    # CRITICAL: Verify all input slots are preserved
    input_slot_keys = set(raw_slots.keys()) if raw_slots else set()
    promoted_slot_keys = set(promoted.keys())
    if input_slot_keys:
        lost_slots = input_slot_keys - promoted_slot_keys
        if lost_slots:
            logger.error(
                f"[PROMOTION] VIOLATION: Slots lost during promotion! "
                f"Lost slots: {list(lost_slots)}, "
                f"input_slots={list(input_slot_keys)}, "
                f"promoted_slots={list(promoted_slot_keys)}"
            )
            # Restore lost slots (fail-safe)
            for key in lost_slots:
                promoted[key] = raw_slots[key]

    return promoted
