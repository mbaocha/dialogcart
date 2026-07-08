"""JSON Schema validation for decision trace payloads."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "decision_trace_v1_1.json"


class DecisionTraceSchemaError(ImportError):
    """Raised when jsonschema is required but not installed."""


@lru_cache(maxsize=1)
def load_decision_trace_schema() -> Dict[str, Any]:
    with _SCHEMA_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_decision_trace(payload: Mapping[str, Any]) -> None:
    """Validate a decision trace dict against the v1.1 JSON schema.

    Raises:
        DecisionTraceSchemaError: if the ``jsonschema`` package is not installed.
        jsonschema.ValidationError: if the payload does not match the schema.
    """
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - environment specific
        raise DecisionTraceSchemaError(
            "jsonschema is required for decision trace validation"
        ) from exc

    jsonschema.validate(payload, load_decision_trace_schema())
