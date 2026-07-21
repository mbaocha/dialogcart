"""Renderer boundary: only non-empty strings become user-facing text."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.rendering import llm_renderer
from core.rendering.llm_renderer import LlmRenderRequest, render_llm


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
