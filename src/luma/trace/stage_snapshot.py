"""
Stage snapshot capture for execution tracing.

Captures input/output/diff snapshots for each pipeline stage to make data inconsistencies obvious.
"""

from typing import Dict, Any, Optional, Set
import copy


def _redact_sensitive_data(data: Any, redact_keys: Optional[Set[str]] = None) -> Any:
    """
    Redact sensitive or verbose data from snapshots.

    Args:
        data: Data structure to redact
        redact_keys: Set of keys to redact (default: common sensitive keys)

    Returns:
        Redacted copy of data
    """
    if redact_keys is None:
        redact_keys = {
            "tenant_aliases", "aliases", "psentence", "osentence",
            "raw_text", "text", "original_text"
        }

    if isinstance(data, dict):
        redacted = {}
        for key, value in data.items():
            if key in redact_keys:
                # Redact by showing type and length only
                if isinstance(value, str):
                    redacted[key] = f"<str:{len(value)}>"
                elif isinstance(value, (list, tuple)):
                    redacted[key] = f"<{type(value).__name__}:{len(value)}>"
                elif isinstance(value, dict):
                    redacted[key] = f"<dict:{len(value)}>"
                else:
                    redacted[key] = f"<{type(value).__name__}>"
            else:
                redacted[key] = _redact_sensitive_data(value, redact_keys)
        return redacted
    elif isinstance(data, list):
        return [_redact_sensitive_data(item, redact_keys) for item in data]
    else:
        return data


def _minimize_snapshot(data: Any, max_depth: int = 3, max_keys: int = 20) -> Any:
    """
    Minimize snapshot by limiting depth and number of keys.

    Args:
        data: Data structure to minimize
        max_depth: Maximum recursion depth
        max_keys: Maximum keys to keep in dictionaries

    Returns:
        Minimized copy of data
    """
    if max_depth <= 0:
        return "<max_depth>"

    if isinstance(data, dict):
        minimized = {}
        items = list(data.items())
        # Keep only first max_keys items
        for key, value in items[:max_keys]:
            minimized[key] = _minimize_snapshot(value, max_depth - 1, max_keys)
        if len(items) > max_keys:
            minimized["_truncated"] = f"{len(items) - max_keys} more keys"
        return minimized
    elif isinstance(data, list):
        # Limit list length
        minimized_list = []
        for item in data[:max_keys]:
            minimized_list.append(_minimize_snapshot(
                item, max_depth - 1, max_keys))
        if len(data) > max_keys:
            minimized_list.append(f"<{len(data) - max_keys} more items>")
        return minimized_list
    else:
        return data


def _compute_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute diff showing only changed keys.

    Args:
        before: State before stage execution
        after: State after stage execution

    Returns:
        Dict with only keys that changed (or were added/removed)
    """
    diff = {}

    # Find keys in after that changed or were added
    for key, after_value in after.items():
        if key not in before:
            diff[key] = {"action": "added", "value": after_value}
        elif before[key] != after_value:
            diff[key] = {
                "action": "changed",
                "before": before[key],
                "after": after_value
            }

    # Find keys in before that were removed
    for key in before:
        if key not in after:
            diff[key] = {"action": "removed", "value": before[key]}

    return diff


def capture_stage_snapshot(
    stage_name: str,
    input_data: Any,
    output_data: Any,
    decision_flags: Optional[Dict[str, Any]] = None,
    minimize: bool = True,
    redact: bool = True
) -> Dict[str, Any]:
    """
    Capture a stage snapshot with input, output, and diff.

    Args:
        stage_name: Name of the stage (e.g., "extraction", "semantic")
        input_data: Input to the stage (will be snapshot)
        output_data: Output from the stage (will be snapshot)
        decision_flags: Optional decision flags for this stage
        minimize: Whether to minimize snapshots (limit depth/keys)
        redact: Whether to redact sensitive data

    Returns:
        Dict with stage_name, input_snapshot, output_snapshot, diff, decision_flags
    """
    # Convert input/output to dicts for snapshotting
    input_dict = _to_dict(input_data)
    output_dict = _to_dict(output_data)

    # Apply redaction and minimization
    if redact:
        input_snapshot = _redact_sensitive_data(input_dict)
        output_snapshot = _redact_sensitive_data(output_dict)
    else:
        input_snapshot = copy.deepcopy(input_dict)
        output_snapshot = copy.deepcopy(output_dict)

    if minimize:
        input_snapshot = _minimize_snapshot(input_snapshot)
        output_snapshot = _minimize_snapshot(output_snapshot)

    # Compute diff (only changed keys)
    diff = _compute_diff(input_dict, output_dict)

    snapshot = {
        "stage_name": stage_name,
        "input_snapshot": input_snapshot,
        "output_snapshot": output_snapshot,
        "diff": diff
    }

    if decision_flags:
        snapshot["decision_flags"] = decision_flags

    return snapshot


def _to_dict(data: Any) -> Dict[str, Any]:
    """
    Convert various data types to dict for snapshotting.

    Args:
        data: Data to convert (dict, object with to_dict, etc.)

    Returns:
        Dict representation
    """
    if isinstance(data, dict):
        return data
    elif hasattr(data, 'to_dict'):
        result = data.to_dict()
        if isinstance(result, dict):
            return result
        else:
            return {"_raw": str(data)}
    elif hasattr(data, '__dict__'):
        return {k: v for k, v in data.__dict__.items() if not k.startswith('_')}
    else:
        # Fallback: convert to string representation
        return {"_raw": str(data)}


class StageSnapshot:
    """
    Context manager for capturing stage snapshots.

    Usage:
        with StageSnapshot(trace, "extraction", input_data):
            output_data = stage_function(input_data)
        # Snapshot is automatically added to trace["stages"]
    """

    def __init__(
        self,
        trace: Dict[str, Any],
        stage_name: str,
        input_data: Any,
        decision_flags: Optional[Dict[str, Any]] = None,
        minimize: bool = True,
        redact: bool = True
    ):
        self.trace = trace
        self.stage_name = stage_name
        self.input_data = input_data
        self.decision_flags = decision_flags
        self.minimize = minimize
        self.redact = redact
        self.output_data = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.output_data is not None:
            snapshot = capture_stage_snapshot(
                stage_name=self.stage_name,
                input_data=self.input_data,
                output_data=self.output_data,
                decision_flags=self.decision_flags,
                minimize=self.minimize,
                redact=self.redact
            )

            # Initialize stages list if needed
            if "stages" not in self.trace:
                self.trace["stages"] = []

            # Add snapshot to stages list
            self.trace["stages"].append(snapshot)

        return False  # Never suppress exceptions



