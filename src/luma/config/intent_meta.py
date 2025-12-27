"""
Intent metadata loading and slot validation.

Loads intent metadata from intent_signals.yaml and provides slot validation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Optional
import yaml

from ..config.temporal import APPOINTMENT_TEMPORAL_TYPE, RESERVATION_TEMPORAL_TYPE, TimeMode


# Cache for intent metadata (required_slots, etc.)
_INTENT_META_CACHE: Dict[str, Dict[str, Any]] = {}


@dataclass(frozen=True)
class IntentMeta:
    """
    Immutable intent metadata structure.

    Represents policy metadata for an intent loaded from intent_signals.yaml.
    All fields are optional to support intents with varying metadata.
    """
    intent_name: str
    is_booking: Optional[bool] = None
    temporal_shape: Optional[str] = None
    requires_end_date: Optional[bool] = None
    supports_date_roles: Optional[bool] = None
    intent_signals: Optional[Dict[str, Any]] = None
    intent_defining_slots: Optional[frozenset[str]] = None
    required_slots: Optional[frozenset[str]] = None

    @classmethod
    def from_dict(cls, intent_name: str, data: Dict[str, Any]) -> "IntentMeta":
        """
        Create IntentMeta from a dictionary (as loaded from YAML).

        Args:
            intent_name: The intent name
            data: Dictionary containing intent metadata from YAML

        Returns:
            IntentMeta instance with fields populated from data
        """
        # Convert lists to frozen sets for immutability
        intent_defining_slots = None
        if "intent_defining_slots" in data:
            slots = data.get("intent_defining_slots") or []
            intent_defining_slots = frozenset(slots) if slots else None

        required_slots = None
        if "required_slots" in data:
            slots = data.get("required_slots") or []
            required_slots = frozenset(slots) if slots else None

        # Extract intent_signals if present
        intent_signals = data.get("intent_signals")

        return cls(
            intent_name=intent_name,
            is_booking=data.get("is_booking"),
            temporal_shape=data.get("temporal_shape"),
            requires_end_date=data.get("requires_end_date"),
            supports_date_roles=data.get("supports_date_roles"),
            intent_signals=intent_signals,
            intent_defining_slots=intent_defining_slots,
            required_slots=required_slots,
        )


class IntentRegistry:
    """
    Read-only registry for intent metadata.

    Provides a formal interface to access intent policy metadata loaded from
    intent_signals.yaml. Uses the existing load_intent_meta() function to avoid
    duplicating parsing logic.
    """

    def __init__(self):
        """Initialize the registry (lazy-loads on first access)."""
        self._meta_cache: Optional[Dict[str, IntentMeta]] = None

    def _ensure_loaded(self) -> None:
        """Ensure intent metadata is loaded and cached."""
        if self._meta_cache is None:
            raw_meta = load_intent_meta()
            self._meta_cache = {
                intent_name: IntentMeta.from_dict(intent_name, data)
                for intent_name, data in raw_meta.items()
            }

    def get(self, intent_name: str) -> Optional[IntentMeta]:
        """
        Get metadata for an intent by name.

        Args:
            intent_name: The intent name (e.g., "CREATE_APPOINTMENT")

        Returns:
            IntentMeta instance if found, None otherwise
        """
        self._ensure_loaded()
        return self._meta_cache.get(intent_name) if self._meta_cache else None

    def all_intents(self) -> Dict[str, IntentMeta]:
        """
        Get all registered intents.

        Returns:
            Dictionary mapping intent names to IntentMeta instances
        """
        self._ensure_loaded()
        return self._meta_cache.copy() if self._meta_cache else {}


# Global registry instance (singleton pattern for convenience)
_registry_instance: Optional[IntentRegistry] = None


def get_intent_registry() -> IntentRegistry:
    """
    Get the global IntentRegistry instance.

    Returns:
        Singleton IntentRegistry instance
    """
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = IntentRegistry()
    return _registry_instance


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
    # Get temporal shape from IntentRegistry (sole policy source)
    registry = get_intent_registry()
    intent_meta_obj = registry.get(intent_name)
    temporal_shape = intent_meta_obj.temporal_shape if intent_meta_obj else None

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
        # Get requires_end_date from IntentRegistry (sole policy source)
        require_end = intent_meta_obj.requires_end_date if intent_meta_obj else False
        # Enforce two refs (or explicit end_date) when require_end is True
        end_present = bool(resolved_slots.get(
            "end_date")) or len(date_refs) >= 2
        if require_end and not end_present and "end_date" not in missing:
            missing.append("end_date")

    return missing
