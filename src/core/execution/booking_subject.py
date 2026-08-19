"""Execution-time projection of configured booking subject entities."""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Dict, Mapping, Optional, Union

from core.adapters.nlu.entity_schema_builder import (
    booking_subject_key_is_forbidden,
    entities_with_role,
    planning_slot_key_for_field,
)

BookingSubjectPrimitive = Union[str, int, float, bool]

_SAFE_KEY = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_MAX_PROPERTIES = 50
_MAX_KEY_LENGTH = 64
_MAX_STRING_LENGTH = 512
_MAX_SERIALIZED_BYTES = 4096


class BookingSubjectValidationError(ValueError):
    """The configured subject cannot satisfy the Commerce request contract."""


def booking_subject_enabled() -> bool:
    """Return the local rollout gate; disabled unless explicitly enabled."""
    return os.getenv("BOOKING_SUBJECT_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def build_booking_subject(
    *,
    slots: Mapping[str, Any],
    entity_schema: Optional[Mapping[str, Any]],
) -> Optional[Dict[str, BookingSubjectPrimitive]]:
    """Build and validate a flat subject snapshot from finalized durable slots."""
    tagged = entities_with_role(entity_schema, "booking_subject")
    subject: Dict[str, BookingSubjectPrimitive] = {}
    for field in tagged:
        name = field.get("name")
        if not isinstance(name, str) or not name:
            raise BookingSubjectValidationError(
                "booking_subject entity must have a canonical string name"
            )
        if booking_subject_key_is_forbidden(name):
            raise BookingSubjectValidationError(
                f"booking_subject key {name!r} is forbidden"
            )
        if len(name) > _MAX_KEY_LENGTH or _SAFE_KEY.fullmatch(name) is None:
            raise BookingSubjectValidationError(
                f"booking_subject key {name!r} is not safe canonical snake_case"
            )

        slot_key = planning_slot_key_for_field(field)
        value = slots.get(slot_key) if slot_key is not None else None
        if value is None:
            if field.get("required") is True:
                raise BookingSubjectValidationError(
                    f"required booking_subject slot {name!r} is missing"
                )
            continue

        if isinstance(value, bool):
            validated: BookingSubjectPrimitive = value
        elif isinstance(value, str):
            if len(value) > _MAX_STRING_LENGTH:
                raise BookingSubjectValidationError(
                    f"booking_subject value for {name!r} exceeds "
                    f"{_MAX_STRING_LENGTH} characters"
                )
            validated = value
        elif isinstance(value, int):
            validated = value
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise BookingSubjectValidationError(
                    f"booking_subject value for {name!r} must be finite"
                )
            validated = value
        else:
            raise BookingSubjectValidationError(
                f"booking_subject value for {name!r} must be a JSON primitive"
            )
        subject[name] = validated

    if len(subject) > _MAX_PROPERTIES:
        raise BookingSubjectValidationError(
            f"booking_subject exceeds {_MAX_PROPERTIES} properties"
        )

    if not subject:
        return None

    serialized = json.dumps(
        subject, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if len(serialized) > _MAX_SERIALIZED_BYTES:
        raise BookingSubjectValidationError(
            f"booking_subject exceeds {_MAX_SERIALIZED_BYTES} serialized bytes"
        )
    return subject
