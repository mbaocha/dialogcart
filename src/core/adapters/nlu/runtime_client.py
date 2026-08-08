"""Construct the process-wide Luma client for Core API runtime use."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .luma_client import LumaClient
from .recording_luma_client import RecordingLumaClient

LUMA_MODE_ENV = "DIALOGCART_LUMA_MODE"
LUMA_RECORDING_DIR_ENV = "DIALOGCART_LUMA_RECORDING_DIR"
VALID_LUMA_MODES = ("record", "replay", "live")
DEFAULT_LUMA_MODE = "live"
DEFAULT_RUNTIME_RECORDINGS_DIR = (
    Path(__file__).resolve().parents[3] / ".dialogcart" / "recordings" / "luma"
)


def resolve_luma_mode(value: Optional[str] = None) -> str:
    mode = (value if value is not None else os.getenv(LUMA_MODE_ENV, DEFAULT_LUMA_MODE))
    mode = mode.strip().lower()
    if mode not in VALID_LUMA_MODES:
        valid = "|".join(VALID_LUMA_MODES)
        raise ValueError(f"Invalid {LUMA_MODE_ENV}={mode!r}; expected {valid}")
    return mode


def resolve_runtime_recordings_dir(value: Optional[str | Path] = None) -> Path:
    configured = value if value is not None else os.getenv(LUMA_RECORDING_DIR_ENV)
    return Path(configured) if configured else DEFAULT_RUNTIME_RECORDINGS_DIR


def build_runtime_luma_client(
    *,
    mode: Optional[str] = None,
    recordings_dir: Optional[str | Path] = None,
    live_client: Optional[LumaClient] = None,
):
    selected_mode = resolve_luma_mode(mode)
    live = live_client or LumaClient()
    if selected_mode == "live":
        return live
    return RecordingLumaClient(
        live,
        recordings_dir=resolve_runtime_recordings_dir(recordings_dir),
        mode=selected_mode,
    )
