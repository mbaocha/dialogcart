"""Deterministic replay seam for handler-rendering results in tests."""

from __future__ import annotations

import copy
from typing import Any, Dict

from core.rendering.llm_renderer import (
    HandlerRenderResult,
    LlmRenderRequest,
    coerce_handler_render_result,
)
from core.tests.harness.clients import normalize_script_key


class RecordingRenderClient:
    """Record and replay renderer results keyed by normalized user request.

    Recordings contain the complete typed production handler result.
    """

    def __init__(self, recordings: Dict[str, Any] | None = None) -> None:
        self._recordings: Dict[str, Any] = {}
        self.last_request: LlmRenderRequest | None = None
        self.last_response: Any = None
        for request_text, response in (recordings or {}).items():
            self.record(request_text, response)

    def record(self, request_text: str, response: Any) -> None:
        key = normalize_script_key(request_text)
        if key in self._recordings:
            raise ValueError(
                "Duplicate handler-render recording after normalization: "
                f"{request_text!r} -> {key!r}"
            )
        self._recordings[key] = copy.deepcopy(response)

    def render(self, request: LlmRenderRequest) -> HandlerRenderResult:
        self.last_request = copy.deepcopy(request)
        request_text = request.user_request or ""
        key = normalize_script_key(request_text)
        if key not in self._recordings:
            raise AssertionError(
                "No recorded handler-render response for "
                f"{request_text!r}. Normalized key: {key!r}. "
                f"Available keys: {sorted(self._recordings)!r}"
            )
        response = coerce_handler_render_result(copy.deepcopy(self._recordings[key]))
        self.last_response = copy.deepcopy(response)
        return response
