"""Anthropic prompt-cache support for Stage 2 CREATE and AVAILABILITY."""
from __future__ import annotations

import hashlib
import json
import logging
from threading import Lock
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

HAIKU_45_MIN_CACHE_TOKENS = 4096
CACHE_TTL = "5m"

_eligibility_cache: Dict[Tuple[str, str], Tuple[bool, Optional[int]]] = {}
_eligibility_lock = Lock()


def prefix_fingerprint(tool: Mapping[str, Any], stable_text: str) -> str:
    """Return an opaque identity for the exact cache-relevant material."""
    payload = json.dumps(
        {"tool": tool, "stable_system": stable_text},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def cache_eligibility(
    client: Any,
    *,
    model: str,
    tool: Mapping[str, Any],
    stable_text: str,
) -> Tuple[bool, Optional[int], str]:
    """Provider-tokenize the static prefix once; fail closed when unavailable."""
    fingerprint = prefix_fingerprint(tool, stable_text)
    key = (model, fingerprint)
    with _eligibility_lock:
        cached = _eligibility_cache.get(key)
    if cached is not None:
        return cached[0], cached[1], fingerprint

    token_count: Optional[int] = None
    try:
        counter = getattr(getattr(client, "messages", None), "count_tokens", None)
        if callable(counter):
            result = counter(
                model=model,
                tools=[dict(tool)],
                system=[{"type": "text", "text": stable_text}],
                messages=[],
            )
            raw_count = getattr(result, "input_tokens", None)
            if raw_count is None and isinstance(result, Mapping):
                raw_count = result.get("input_tokens")
            if raw_count is not None:
                token_count = int(raw_count)
    except Exception:
        logger.warning(
            "Stage2 cache token count failed model=%s prefix=%s; cache disabled",
            model,
            fingerprint,
            exc_info=True,
        )

    eligible = bool(
        token_count is not None and token_count >= HAIKU_45_MIN_CACHE_TOKENS
    )
    with _eligibility_lock:
        _eligibility_cache[key] = (eligible, token_count)
    return eligible, token_count, fingerprint


def system_blocks(stable_text: str, dynamic_text: str, *, eligible: bool) -> List[dict]:
    """Build ordered system blocks with a breakpoint on the stable prefix."""
    stable: Dict[str, Any] = {"type": "text", "text": stable_text}
    if eligible:
        stable["cache_control"] = {"type": "ephemeral"}
    return [stable, {"type": "text", "text": dynamic_text}]


def log_usage(
    response: Any,
    *,
    model: str,
    group: str,
    prefix: str,
    prefix_tokens: Optional[int],
    cache_eligible: bool,
    cache_control_applied: bool,
) -> None:
    """Log cache usage without prompts, customer text, or tenant configuration."""
    usage = getattr(response, "usage", None)

    def _value(name: str) -> int:
        value = getattr(usage, name, None)
        if value is None and isinstance(usage, Mapping):
            value = usage.get(name)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    creation_tokens = _value("cache_creation_input_tokens")
    read_tokens = _value("cache_read_input_tokens")
    if not cache_eligible:
        zero_reason = "prefix_below_minimum_or_count_unavailable"
    elif not cache_control_applied:
        zero_reason = "cache_control_not_applied"
    elif not creation_tokens and not read_tokens:
        zero_reason = "provider_behavior_or_request_shape_instability"
    else:
        zero_reason = "none"

    logger.info(
        "[STAGE2_LLM_USAGE] group=%s model=%s input_tokens=%d "
        "cache_creation_input_tokens=%d cache_read_input_tokens=%d "
        "output_tokens=%d schema_fingerprint=%s prefix_tokens=%s "
        "cache_eligible=%s cache_control_applied=%s cache_ttl=%s zero_reason=%s",
        group,
        model,
        _value("input_tokens"),
        creation_tokens,
        read_tokens,
        _value("output_tokens"),
        prefix,
        prefix_tokens,
        cache_eligible,
        cache_control_applied,
        CACHE_TTL,
        zero_reason,
    )
