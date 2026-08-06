"""
Deterministic cancellation / rescheduling policy normalization for rendering.

Commerce owns the raw policy objects. This module converts them into
customer-facing summary facts for Business Knowledge prompts so the LLM
never interprets refundType / policy-type enums.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

_CANCELLATION_KEYS = (
    "cancellation_policy",
    "cancellation_rules",
)

_RESCHEDULING_KEYS = (
    "rescheduling_policy",
    "reschedule_policy",
)


def _as_positive_int(raw: Any) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, float) and raw.is_integer():
        value = int(raw)
        return value if value > 0 else None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            value = int(text)
        except ValueError:
            return None
        return value if value > 0 else None
    return None


def _hours_phrase(hours: int) -> str:
    unit = "hour" if hours == 1 else "hours"
    return f"{hours} {unit}"


def _refund_type(raw: Dict[str, Any]) -> Optional[str]:
    refund_type = raw.get("refundType", raw.get("refund_type"))
    if isinstance(refund_type, str) and refund_type.strip():
        return refund_type.strip().lower()

    fee = raw.get("fee")
    if isinstance(fee, str) and fee.strip():
        fee_text = fee.strip().lower()
        if fee_text in {"free", "0", "0%", "none", "no", "non-refundable", "nonrefundable"}:
            if fee_text in {"free", "0", "0%"}:
                return "free"
            return "none"
        if "%" in fee_text:
            return "partial"

    if raw.get("refundPercent") is not None or raw.get("refund_percent") is not None:
        return "partial"
    return None


def _cancel_before_hours(raw: Dict[str, Any]) -> Optional[int]:
    for key in (
        "cancelBeforeHours",
        "cancel_before_hours",
        "notice_hours",
        "noticeHours",
        "hours",
    ):
        hours = _as_positive_int(raw.get(key))
        if hours is not None:
            return hours
    return None


def _refund_percent(raw: Dict[str, Any]) -> Optional[int]:
    for key in ("refundPercent", "refund_percent"):
        percent = _as_positive_int(raw.get(key))
        if percent is not None:
            return percent

    fee = raw.get("fee")
    if isinstance(fee, str) and "%" in fee:
        digits = "".join(ch for ch in fee if ch.isdigit())
        if digits:
            try:
                value = int(digits)
            except ValueError:
                return None
            return value if value > 0 else None
    return None


def normalize_cancellation_policy(raw: Any) -> Optional[str]:
    """
    Convert a Commerce cancellation policy object into a customer-facing summary.

    Returns None when ``raw`` is missing or not interpretable.
    """
    if not isinstance(raw, dict) or not raw:
        return None

    refund_type = _refund_type(raw)
    hours = _cancel_before_hours(raw)
    if refund_type is None and hours is None and raw.get("fee") is None:
        return None

    if refund_type in {"free", "full"}:
        if hours is not None:
            return (
                "Free cancellation if cancelled at least "
                f"{_hours_phrase(hours)} before your appointment."
            )
        return "Free cancellation."

    if refund_type in {"partial", "percent", "percentage"}:
        percent = _refund_percent(raw)
        if percent is not None and hours is not None:
            return (
                f"{percent}% refund if cancelled at least "
                f"{_hours_phrase(hours)} before your appointment."
            )
        if percent is not None:
            return f"{percent}% refund on cancellation."
        if hours is not None:
            return (
                "Partial refund if cancelled at least "
                f"{_hours_phrase(hours)} before your appointment."
            )
        return "Partial refund on cancellation."

    if refund_type in {"none", "no", "no_refund", "non_refundable", "nonrefundable"}:
        if hours is not None:
            return (
                f"Appointments cancelled within {_hours_phrase(hours)} "
                "are non-refundable."
            )
        return "Appointments are non-refundable."

    # Unknown refundType — do not invent wording.
    return None


def _reschedule_hours(raw: Dict[str, Any]) -> Optional[int]:
    for key in (
        "hours",
        "beforeHours",
        "before_hours",
        "untilHours",
        "until_hours",
        "withinHours",
        "within_hours",
        "rescheduleBeforeHours",
        "reschedule_before_hours",
    ):
        hours = _as_positive_int(raw.get(key))
        if hours is not None:
            return hours
    return None


def normalize_rescheduling_policy(raw: Any) -> Optional[str]:
    """
    Convert a Commerce rescheduling policy object into a customer-facing summary.

    Returns None when ``raw`` is missing or not interpretable.
    """
    if not isinstance(raw, dict) or not raw:
        return None

    policy_type = raw.get("type", raw.get("policyType", raw.get("policy_type")))
    if not isinstance(policy_type, str) or not policy_type.strip():
        return None

    kind = policy_type.strip().lower().replace("-", "_").replace(" ", "_")
    hours = _reschedule_hours(raw)

    if kind in {"always", "anytime", "any_time"}:
        return "Appointments may be rescheduled at any time."

    if kind in {"until", "before", "up_to", "upto", "until_hours", "before_hours"}:
        if hours is None:
            return None
        return (
            "Appointments may be rescheduled up to "
            f"{_hours_phrase(hours)} before the appointment."
        )

    if kind in {
        "within",
        "not_within",
        "no_within",
        "cannot_within",
        "no_reschedule_within",
        "within_hours",
    }:
        if hours is None:
            return None
        return (
            "Appointments cannot be rescheduled within "
            f"{_hours_phrase(hours)} of the appointment."
        )

    if kind in {"never", "none", "not_allowed", "disallowed"}:
        return "Appointments cannot be rescheduled."

    return None


def _extract_policy(
    structured_context: Dict[str, Any], keys: Tuple[str, ...]
) -> Tuple[Optional[str], Any]:
    for key in keys:
        if key in structured_context:
            return key, structured_context.get(key)
    return None, None


def apply_policy_summaries(structured_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inject cancellation_summary / rescheduling_summary into a prompt view.

    Mutates and returns ``structured_context``. Successful normalization replaces
    the ambiguous raw policy objects in the prompt view; the caller's original
    facts dict is left unchanged by the prepare layer.
    """
    cancel_key, cancel_raw = _extract_policy(structured_context, _CANCELLATION_KEYS)
    if cancel_key is not None:
        summary = normalize_cancellation_policy(cancel_raw)
        if summary is not None:
            structured_context.pop(cancel_key, None)
            structured_context["cancellation_summary"] = summary

    reschedule_key, reschedule_raw = _extract_policy(
        structured_context, _RESCHEDULING_KEYS
    )
    if reschedule_key is not None:
        summary = normalize_rescheduling_policy(reschedule_raw)
        if summary is not None:
            structured_context.pop(reschedule_key, None)
            structured_context["rescheduling_summary"] = summary

    return structured_context
