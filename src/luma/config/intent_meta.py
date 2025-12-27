"""
Intent metadata loading and slot validation.

Loads intent metadata from intent_signals.yaml and provides slot validation.
"""
from pathlib import Path
from typing import Dict, Any, List
import yaml

from ..config.temporal import APPOINTMENT_TEMPORAL_TYPE, RESERVATION_TEMPORAL_TYPE, INTENT_TEMPORAL_SHAPE, INTENT_REQUIRE_END_DATE, TimeMode


# Cache for intent metadata (required_slots, etc.)
_INTENT_META_CACHE: Dict[str, Dict[str, Any]] = {}


def load_intent_meta() -> Dict[str, Dict[str, Any]]:
    """Load intent metadata (including required_slots) from intent_signals.yaml."""
    global _INTENT_META_CACHE
    if _INTENT_META_CACHE:
        return _INTENT_META_CACHE
    path = (
        Path(__file__).resolve().parent.parent
        / "store"
        / "normalization"
        / "intent_signals.yaml"
    )
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    intents_cfg = raw.get("intents", raw) if isinstance(raw, dict) else {}
    for intent, cfg in intents_cfg.items():
        if isinstance(cfg, dict):
            _INTENT_META_CACHE[intent] = cfg
    return _INTENT_META_CACHE


def validate_required_slots(intent_name: str, resolved_slots: Dict[str, Any], entities: Dict[str, Any]) -> List[str]:
    """
    Validate required slots from intent_signals.yaml.
    Returns list of missing slots.
    """
    intent_meta = load_intent_meta().get(intent_name, {}) or {}
    required_slots = intent_meta.get("required_slots") or []
    missing: List[str] = []
    temporal_shape = INTENT_TEMPORAL_SHAPE.get(intent_name)

    def _slot_present(slot: str) -> bool:
        val = resolved_slots.get(slot)
        if val is None or val == "" or val == []:
            val = entities.get(slot)
        if val:
            return True
        # Special cases
        if slot == "date":
            return bool(resolved_slots.get("date_refs"))
        if slot == "time":
            return bool(
                resolved_slots.get("time_refs")
                or resolved_slots.get("time_constraint")
                or resolved_slots.get("time_range")
                or resolved_slots.get("datetime_range")
            )
        if slot == "start_date":
            refs = resolved_slots.get("date_refs") or []
            return len(refs) >= 1
        if slot == "end_date":
            refs = resolved_slots.get("date_refs") or []
            return len(refs) >= 2
        if slot == "booking_id":
            bid = entities.get("booking_id")
            return bool(bid)
        # FIX: service_id can be satisfied by services list in resolved_booking
        # The decision layer resolves services to tenant_service_id, but resolved_booking
        # stores services as a list of service objects, not as service_id
        if slot == "service_id":
            services = resolved_slots.get("services") or []
            # Check if services list is non-empty (services are resolved)
            if services and isinstance(services, list) and len(services) > 0:
                # Also check if any service has tenant_service_id (fully resolved)
                for service in services:
                    if isinstance(service, dict) and service.get("tenant_service_id"):
                        return True
                # Even if no tenant_service_id, non-empty services list means service was extracted
                return True
        return False

    for slot in required_slots:
        # Fix 2: For reservations, exclude service_id from required slots
        # Reservations can proceed with just dates (service is optional/implicit)
        if intent_name == "CREATE_RESERVATION" and slot == "service_id":
            continue
        if not _slot_present(slot):
            missing.append(slot)

    # Temporal-shape based enforcement
    if temporal_shape == APPOINTMENT_TEMPORAL_TYPE:
        # Requires both date and time; fuzzy time not allowed
        has_date = bool(resolved_slots.get("date_refs"))
        has_time = False
        time_refs = resolved_slots.get("time_refs") or []
        if time_refs:
            has_time = True
        else:
            tc = resolved_slots.get("time_constraint") or {}
            tc_mode = tc.get("mode")
            if tc_mode in {TimeMode.EXACT.value, TimeMode.WINDOW.value, TimeMode.RANGE.value}:
                has_time = True
            elif tc_mode == TimeMode.FUZZY.value:
                has_time = False  # fuzzy not allowed for appointments
        if not has_date and "date" not in missing:
            missing.append("date")
        if not has_time and "time" not in missing:
            missing.append("time")
    elif temporal_shape == RESERVATION_TEMPORAL_TYPE:
        # Requires start_date; end_date if configured
        date_refs = resolved_slots.get("date_refs") or []
        if len(date_refs) < 1 and "start_date" not in missing:
            missing.append("start_date")
        require_end = INTENT_REQUIRE_END_DATE.get(
            intent_name) or INTENT_REQUIRE_END_DATE.get("CREATE_RESERVATION")
        # Enforce two refs (or explicit end_date) when require_end is True
        end_present = bool(resolved_slots.get(
            "end_date")) or len(date_refs) >= 2
        if require_end and not end_present and "end_date" not in missing:
            missing.append("end_date")

    return missing
