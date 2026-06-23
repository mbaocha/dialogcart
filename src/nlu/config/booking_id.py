"""Booking reference ID format — global default with optional tenant override."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Pattern, Tuple

# 2+ letters + 3+ digits (e.g. ABC123, abc123). Matching is case-insensitive.
DEFAULT_BOOKING_ID_PATTERN = r"^[A-Za-z]{2,}\d{3,}$"
DEFAULT_BOOKING_ID_SCAN_PATTERN = r"\b[A-Za-z]{2,}\d{3,}\b"

_DEFAULT_FLAGS = re.IGNORECASE
_DEFAULT_VALIDATE_RE = re.compile(DEFAULT_BOOKING_ID_PATTERN, _DEFAULT_FLAGS)
_DEFAULT_SCAN_RE = re.compile(DEFAULT_BOOKING_ID_SCAN_PATTERN, _DEFAULT_FLAGS)


def get_booking_id_settings(
    tenant_context: Optional[Dict[str, Any]] = None,
) -> Tuple[Pattern[str], Pattern[str], List[str]]:
    """Return (validate_re, scan_re, examples) for the tenant.

    tenant_context.booking_id may contain:
      - pattern: full-match regex string (overrides default validation)
      - scan_pattern: regex to find candidates in raw text (defaults derived from pattern)
      - examples: optional strings shown in the Haiku prompt as hints only

    All patterns are compiled case-insensitively unless the tenant pattern uses
    explicit inline flags.
    """
    tenant_context = tenant_context or {}
    raw = tenant_context.get("booking_id") or {}

    pattern_str = raw.get("pattern") or DEFAULT_BOOKING_ID_PATTERN
    scan_str = raw.get("scan_pattern")

    try:
        validate_re = re.compile(pattern_str, _DEFAULT_FLAGS)
    except re.error:
        validate_re = _DEFAULT_VALIDATE_RE

    if scan_str:
        try:
            scan_re = re.compile(scan_str, _DEFAULT_FLAGS)
        except re.error:
            scan_re = _derive_scan_re(pattern_str)
    else:
        scan_re = _derive_scan_re(pattern_str)

    examples_raw = raw.get("examples") or []
    examples = [str(x) for x in examples_raw if x][:5]
    return validate_re, scan_re, examples


def _derive_scan_re(pattern_str: str) -> Pattern[str]:
    """Best-effort scan regex from a full-match validation pattern."""
    if pattern_str in (DEFAULT_BOOKING_ID_PATTERN, r"^[A-Z]{2,}\d{3,}$"):
        return _DEFAULT_SCAN_RE
    inner = pattern_str.strip()
    if inner.startswith("^"):
        inner = inner[1:]
    if inner.endswith("$"):
        inner = inner[:-1]
    try:
        return re.compile(rf"\b(?:{inner})\b", _DEFAULT_FLAGS)
    except re.error:
        return _DEFAULT_SCAN_RE


def is_valid_booking_id(value: str, validate_re: Pattern[str]) -> bool:
    return bool(value and validate_re.fullmatch(str(value).strip()))


def scan_booking_id_from_text(
    text: str, validate_re: Pattern[str], scan_re: Pattern[str]
) -> Optional[str]:
    """Return the first booking-id-shaped token in *text* that passes validation."""
    if not text:
        return None
    for match in scan_re.finditer(text):
        candidate = match.group(0)
        if is_valid_booking_id(candidate, validate_re):
            return candidate
    for token in re.findall(r"\b[\w-]+\b", text):
        if is_valid_booking_id(token, validate_re):
            return token
    return None
