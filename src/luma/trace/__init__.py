"""Trace utilities for Luma pipeline execution tracing."""

from luma.trace.stage_snapshot import capture_stage_snapshot, StageSnapshot
from luma.trace.trace_contract import validate_stable_fields, TRACE_VERSION
from luma.trace.slot_tracking import (
    log_slot_transformation,
    log_field_removal,
    create_slot_snapshot,
    extract_slot_keys,
    compute_slot_diff,
)

__all__ = [
    "capture_stage_snapshot",
    "StageSnapshot",
    "validate_stable_fields",
    "TRACE_VERSION",
    "log_slot_transformation",
    "log_field_removal",
    "create_slot_snapshot",
    "extract_slot_keys",
    "compute_slot_diff",
]

