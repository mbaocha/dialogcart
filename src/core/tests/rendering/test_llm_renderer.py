"""Renderer boundary: only non-empty strings become user-facing text."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.rendering import llm_renderer
from core.rendering.llm_renderer import (
    HandlerEntitySelection,
    LlmRenderRequest,
    render_handler_response,
    render_llm,
)


@pytest.fixture
def render_request():
    return LlmRenderRequest(
        render_instruction="Ask for a time.",
        facts={"structured_context": {"business_name": "Test Salon"}},
    )


def _patch_anthropic_response(monkeypatch, text_value):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    content_block = SimpleNamespace(text=text_value)
    response = SimpleNamespace(content=[content_block])
    client = MagicMock()
    client.messages.create.return_value = response
    anthropic_mod = MagicMock()
    anthropic_mod.Anthropic.return_value = client
    monkeypatch.setitem(__import__("sys").modules, "anthropic", anthropic_mod)
    return client


def test_render_llm_rejects_magicmock_text(monkeypatch, render_request):
    _patch_anthropic_response(monkeypatch, MagicMock())
    assert render_llm(render_request) == llm_renderer._FALLBACK_TEXT


def test_render_llm_rejects_non_string_text(monkeypatch, render_request):
    _patch_anthropic_response(monkeypatch, {"not": "a string"})
    assert render_llm(render_request) == llm_renderer._FALLBACK_TEXT


def test_render_llm_strips_valid_string(monkeypatch, render_request):
    _patch_anthropic_response(monkeypatch, "  Hello there.  ")
    assert render_llm(render_request) == "Hello there."


def test_render_llm_empty_string_returns_fallback(monkeypatch, render_request):
    _patch_anthropic_response(monkeypatch, "")
    assert render_llm(render_request) == llm_renderer._FALLBACK_TEXT


def test_render_llm_whitespace_only_returns_fallback(monkeypatch, render_request):
    _patch_anthropic_response(monkeypatch, "   \n\t  ")
    assert render_llm(render_request) == llm_renderer._FALLBACK_TEXT


def test_handler_renderer_returns_complete_typed_result(monkeypatch, render_request):
    _patch_anthropic_response(
        monkeypatch,
        '{"text":"Premium Full Service is best.","selected_entities":'
        '[{"entity_type":"service","catalog_id":27,'
        '"display_name":"Premium Full Service"}],"metadata":{"reason":"rattle"}}',
    )

    result = render_handler_response(render_request)

    assert result.text == "Premium Full Service is best."
    assert result.selected_entities == [
        HandlerEntitySelection(
            entity_type="service",
            catalog_id=27,
            display_name="Premium Full Service",
        )
    ]
    assert result.metadata == {"reason": "rattle"}


def test_handler_renderer_accepts_json_code_fence(monkeypatch, render_request):
    _patch_anthropic_response(
        monkeypatch,
        '```json\n{"text":"Premium Full Service is best.",'
        '"selected_entities":[],"metadata":{}}\n```',
    )

    result = render_handler_response(render_request)

    assert result.text == "Premium Full Service is best."
    assert result.selected_entities == []
    assert result.metadata == {}


def test_handler_renderer_accepts_unlabelled_json_code_fence(monkeypatch, render_request):
    _patch_anthropic_response(
        monkeypatch,
        '```\n{"text":"Premium Full Service is best.",'
        '"selected_entities":[],"metadata":{}}\n```',
    )

    result = render_handler_response(render_request)

    assert result.text == "Premium Full Service is best."


@pytest.mark.parametrize(
    "provider_text",
    [
        'Here is the result: {"text":"Hello"}',
        '```json\n{"text":"First"}\n```\n```json\n{"text":"Second"}\n```',
        '```json\n{"text":}\n```',
        '```json\n[{"text":"Hello"}]\n```',
        '```json\n{"text":"Hello"}\n``` trailing',
    ],
)
def test_handler_renderer_rejects_non_single_object_payloads(
    monkeypatch, render_request, provider_text
):
    _patch_anthropic_response(monkeypatch, provider_text)

    result = render_handler_response(render_request)

    assert result.text == provider_text
    assert result.selected_entities == []


def test_handler_renderer_unstructured_text_has_no_proposal(monkeypatch, render_request):
    _patch_anthropic_response(monkeypatch, "A normal legacy-style response.")

    result = render_handler_response(render_request)

    assert result.text == "A normal legacy-style response."
    assert result.selected_entities == []
