"""Importing E2E turn_understanding must not mutate sys.modules['anthropic']."""

from __future__ import annotations

import importlib
import sys

import pytest

_HELPER_MODULE = "core.tests.e2e.framework.turn_understanding"
_MISSING = object()


def _unload_helper() -> None:
    sys.modules.pop(_HELPER_MODULE, None)


@pytest.fixture(autouse=True)
def _restore_anthropic_and_helper():
    had_anthropic = "anthropic" in sys.modules
    anthropic_before = sys.modules.get("anthropic", _MISSING)
    _unload_helper()
    try:
        yield
    finally:
        _unload_helper()
        if had_anthropic and anthropic_before is not _MISSING:
            sys.modules["anthropic"] = anthropic_before
        else:
            sys.modules.pop("anthropic", None)


def test_import_turn_understanding_preserves_existing_anthropic_module():
    sentinel = object()
    sys.modules["anthropic"] = sentinel  # type: ignore[assignment]
    _unload_helper()
    importlib.import_module(_HELPER_MODULE)
    assert sys.modules["anthropic"] is sentinel


def test_import_turn_understanding_does_not_insert_anthropic():
    sys.modules.pop("anthropic", None)
    _unload_helper()
    assert "anthropic" not in sys.modules
    importlib.import_module(_HELPER_MODULE)
    assert "anthropic" not in sys.modules
