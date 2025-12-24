"""
Config package shim.

Maintains compatibility with the legacy module `luma/config.py` while exposing
new config submodules (core, temporal).
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from typing import Any, Callable

# Load legacy config.py without importing this package name to avoid circular import
_legacy_path = Path(__file__).resolve().parent.parent / "config.py"
_legacy_spec = spec_from_file_location("luma._legacy_config", _legacy_path)
_legacy_module = module_from_spec(_legacy_spec)
if _legacy_spec and _legacy_spec.loader:
    _legacy_spec.loader.exec_module(_legacy_module)  # type: ignore[attr-defined]
    sys.modules.setdefault("luma._legacy_config", _legacy_module)

# Re-export legacy config objects
config = getattr(_legacy_module, "config", None)
LumaConfig = getattr(_legacy_module, "LumaConfig", None)
debug_print: Callable[..., Any] = getattr(_legacy_module, "debug_print", lambda *a, **k: None)

# Export new config submodules
from . import core, temporal  # noqa: E402

__all__ = ["config", "LumaConfig", "debug_print", "core", "temporal"]
