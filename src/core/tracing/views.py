"""Layered diagnostic projections for decision traces (summary / reasoning / forensic)."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Set, Tuple

from core.tracing.decision_trace import trace_to_dict

TraceView = Literal["summary", "reasoning", "forensic"]

TRACE_VIEWS: Tuple[TraceView, ...] = ("summary", "reasoning", "forensic")
DEFAULT_TRACE_VIEW: TraceView = "summary"

_LEGACY_FORENSIC_ALIASES = frozenset({"1", "decision", "full", "forensic"})

_SESSION_DIFF_FIELDS = frozenset(
    {
        "status",
        "intent",
        "intent_name",
        "missing_slots",
        "active_capability",
        "confirmation_state",
    }
)

_STAGE_LABELS = {
    "luma": "Luma",
    "merge": "Merge",
    "business_facts": "Business facts",
    "planner": "Planner",
    "execution": "Execution",
    "persistence": "Persistence",
    "renderer": "Renderer",
}


def parse_trace_view(query_trace: Optional[str]) -> Optional[TraceView]:
    """Parse ``?trace=`` into a view name, or None when tracing is not requested."""
    value = (query_trace or "").strip().lower()
    if not value:
        return None
    if value in _LEGACY_FORENSIC_ALIASES:
        return "forensic"
    if value in TRACE_VIEWS:
        return value  # type: ignore[return-value]
    return None


def resolve_trace_view(
    *,
    query_trace: Optional[str] = None,
    header_value: Optional[str] = None,
    env_enabled: bool = False,
) -> Tuple[bool, TraceView]:
    """Return whether tracing is enabled and which view to render."""
    from core.tracing.decision_trace import _truthy

    view = parse_trace_view(query_trace)
    if view is not None:
        return True, view
    if env_enabled or _truthy(header_value):
        return True, DEFAULT_TRACE_VIEW
    return False, DEFAULT_TRACE_VIEW


def _normalize_trace(trace: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not trace:
        return {}
    if hasattr(trace, "get") and trace.get("records") is not None:
        return trace_to_dict(trace)  # type: ignore[arg-type]
    return dict(trace)


def _records(trace: Mapping[str, Any]) -> List[Dict[str, Any]]:
    records = trace.get("records")
    return list(records) if isinstance(records, list) else []


def _record_by_id(trace: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(record["id"]): record
        for record in _records(trace)
        if record.get("id")
    }


def _format_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, list):
        if not value:
            return "(empty)"
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        try:
            return json.dumps(value, default=str, ensure_ascii=True)
        except TypeError:
            return str(value)
    return str(value)


def extract_session_changes(trace: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Build compact session diff from session-related mutations."""
    changes: List[Dict[str, Any]] = []
    seen_fields: Set[str] = set()

    for record in _records(trace):
        if record.get("kind") != "mutation":
            continue
        if record.get("presentation_only"):
            continue
        field = str(record.get("field", ""))
        if field.startswith("availability_presentation."):
            continue
        if field.startswith("plan."):
            continue

        normalized = field
        if field.startswith("slots."):
            normalized = field.removeprefix("slots.")
        elif field not in _SESSION_DIFF_FIELDS:
            continue

        if normalized in seen_fields:
            continue
        seen_fields.add(normalized)

        previous = record.get("previous")
        new = record.get("new")
        if previous == new:
            continue

        changes.append(
            {
                "field": normalized,
                "previous": previous,
                "new": new,
            }
        )

    return changes


def enrich_forensic_trace(trace: Mapping[str, Any]) -> Dict[str, Any]:
    """Attach derived diagnostic fields to the full forensic payload."""
    payload = _normalize_trace(trace)
    if not payload:
        return {}
    enriched = dict(payload)
    enriched["session_changes"] = extract_session_changes(payload)
    if "stage_timings" not in enriched:
        enriched["stage_timings"] = {}
    return enriched


def _find_record(trace: Mapping[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    return _record_by_id(trace).get(node_id)


def _intent_from_trace(trace: Mapping[str, Any]) -> str:
    outcome = _find_record(trace, "decision.turn.outcome")
    if outcome:
        winner = outcome.get("winner")
        if isinstance(winner, dict) and winner.get("intent"):
            return str(winner["intent"])

    durable = _find_record(trace, "evidence.session.durable_flow")
    if durable:
        facts = durable.get("facts") if isinstance(durable.get("facts"), dict) else {}
        if facts.get("intent_name"):
            return str(facts["intent_name"])

    return ""


def _session_merge_label(trace: Mapping[str, Any]) -> str:
    merge = _find_record(trace, "decision.merge.eligibility")
    if not merge:
        return "No merge trace"
    winner = str(merge.get("winner") or "")
    if winner == "merge":
        return "Merged with prior session"
    reason = str(merge.get("reason_text") or "")
    if "reset" in reason.lower():
        return "No merge (session reset)"
    if "skipped" in reason.lower() or winner == "skip":
        return "No merge (new conversation)"
    return merge.get("reason_text") or "No merge"


def _post_execution_planning_facts(trace: Mapping[str, Any]) -> Dict[str, Any]:
    post_exec = _find_record(trace, "evidence.planning.post_execution")
    if not post_exec:
        return {}
    facts = post_exec.get("facts")
    return dict(facts) if isinstance(facts, dict) else {}


def _planner_status(trace: Mapping[str, Any]) -> str:
    post_exec = _post_execution_planning_facts(trace)
    if post_exec.get("status") is not None:
        return str(post_exec["status"])
    status = _find_record(trace, "decision.planner.status")
    if not status:
        return ""
    winner = status.get("winner")
    return str(winner) if winner is not None else ""


def _missing_slots(trace: Mapping[str, Any]) -> List[str]:
    evidence = _find_record(trace, "evidence.planning.missing_slots")
    if evidence:
        facts = evidence.get("facts") if isinstance(evidence.get("facts"), dict) else {}
        raw = facts.get("missing_slots")
        if isinstance(raw, list):
            return [str(item) for item in raw]

    status = _find_record(trace, "decision.planner.status")
    if status:
        evaluated = status.get("inputs_evaluated")
        if isinstance(evaluated, dict):
            raw = evaluated.get("missing_slots")
            if isinstance(raw, list):
                return [str(item) for item in raw]
    return []


def _selected_action(trace: Mapping[str, Any]) -> Any:
    post_exec = _post_execution_planning_facts(trace)
    if "action" in post_exec:
        return post_exec.get("action")
    action = _find_record(trace, "decision.planner.select_action")
    if action:
        return action.get("winner")
    outcome = _find_record(trace, "decision.turn.outcome")
    if outcome and isinstance(outcome.get("winner"), dict):
        return outcome["winner"].get("action")
    return None


def _selected_stage(trace: Mapping[str, Any]) -> Any:
    post_exec = _post_execution_planning_facts(trace)
    if post_exec.get("stage") is not None:
        return post_exec.get("stage")
    stage = _find_record(trace, "decision.planner.select_stage")
    if stage:
        return stage.get("winner")
    outcome = _find_record(trace, "decision.turn.outcome")
    if outcome and isinstance(outcome.get("winner"), dict):
        return outcome["winner"].get("stage")
    return None


def _primary_reason(trace: Mapping[str, Any]) -> str:
    status = _find_record(trace, "decision.planner.status")
    if status and status.get("reason_text"):
        return str(status["reason_text"])
    action = _find_record(trace, "decision.planner.select_action")
    if action and action.get("reason_text"):
        return str(action["reason_text"])
    execution = _find_record(trace, "decision.execution.eligibility")
    if execution and execution.get("reason_text"):
        return str(execution["reason_text"])
    outcome = _find_record(trace, "decision.turn.outcome")
    if outcome and outcome.get("reason_text"):
        return str(outcome["reason_text"])
    summary = trace.get("summary") if isinstance(trace.get("summary"), dict) else {}
    why_text = summary.get("why_text")
    if isinstance(why_text, list) and why_text:
        return str(why_text[0])
    return ""


def _outcome_label(trace: Mapping[str, Any]) -> str:
    outcome = _find_record(trace, "decision.turn.outcome")
    if outcome and outcome.get("reason_text"):
        return str(outcome["reason_text"])
    summary = trace.get("summary") if isinstance(trace.get("summary"), dict) else {}
    outcome_summary = summary.get("outcome")
    if isinstance(outcome_summary, dict) and outcome_summary.get("status"):
        status = str(outcome_summary["status"])
        action = outcome_summary.get("action")
        if action:
            return f"{status} (action={action!r})"
        return status
    return _primary_reason(trace)


def _timing_total_ms(trace: Mapping[str, Any]) -> int:
    timing = trace.get("timing_ms")
    if isinstance(timing, (int, float)):
        return int(timing)
    return 0


def _stage_timings(trace: Mapping[str, Any]) -> Dict[str, int]:
    raw = trace.get("stage_timings")
    if not isinstance(raw, dict):
        return {}
    timings: Dict[str, int] = {}
    for key, value in raw.items():
        if isinstance(value, (int, float)):
            timings[str(key)] = int(value)
    return timings


def project_summary(trace: Mapping[str, Any]) -> Dict[str, Any]:
    """Structured summary projection (10–20 line equivalent)."""
    payload = _normalize_trace(trace)
    turn = payload.get("turn") if isinstance(payload.get("turn"), dict) else {}
    post_exec = _post_execution_planning_facts(payload)
    return {
        "view": "summary",
        "user": turn.get("text", ""),
        "intent": _intent_from_trace(payload),
        "session_merge": _session_merge_label(payload),
        "planner_status": _planner_status(payload),
        "missing": _missing_slots(payload),
        "execution_stage": _selected_stage(payload),
        "action": _selected_action(payload),
        "awaiting": post_exec.get("awaiting"),
        "time_match_outcome": post_exec.get("time_match_outcome"),
        "reason": _primary_reason(payload),
        "outcome": _outcome_label(payload),
        "session_changes": extract_session_changes(payload),
        "timing": {"total_ms": _timing_total_ms(payload)},
    }


def project_reasoning(trace: Mapping[str, Any]) -> Dict[str, Any]:
    """Structured reasoning projection for planner investigation."""
    from core.tracing.reasoning import project_reasoning as _project_reasoning

    payload = _normalize_trace(trace)
    return _project_reasoning(payload)


def project_trace(trace: Mapping[str, Any], view: TraceView) -> Dict[str, Any]:
    """Project a forensic trace into the requested diagnostic view."""
    forensic = enrich_forensic_trace(trace)
    if not forensic:
        return {}
    if view == "forensic":
        return {**forensic, "view": "forensic"}
    if view == "reasoning":
        return project_reasoning(forensic)
    return project_summary(forensic)


def _format_session_changes(changes: Sequence[Mapping[str, Any]]) -> List[str]:
    if not changes:
        return []
    lines = ["Session Changes", ""]
    for change in changes:
        field = change.get("field", "?")
        lines.append(str(field))
        lines.append("")
        lines.append(_format_value(change.get("previous")))
        lines.append("")
        lines.append("→")
        lines.append("")
        lines.append(_format_value(change.get("new")))
        lines.append("")
    return lines


def _format_timing_summary(trace: Mapping[str, Any]) -> List[str]:
    return ["Timing", "", f"Total:", f"{_timing_total_ms(trace)} ms", ""]


def _format_timing_reasoning(trace: Mapping[str, Any]) -> List[str]:
    lines = ["Timing", ""]
    stages = _stage_timings(trace)
    for key in (
        "luma",
        "merge",
        "business_facts",
        "planner",
        "execution",
        "persistence",
        "renderer",
    ):
        if key in stages:
            label = _STAGE_LABELS.get(key, key.replace("_", " ").title())
            lines.append(f"{label}:")
            lines.append(f"{stages[key]} ms")
            lines.append("")
    lines.append("Total:")
    lines.append(f"{_timing_total_ms(trace)} ms")
    lines.append("")
    return lines


def _format_timing_forensic(trace: Mapping[str, Any]) -> List[str]:
    lines = ["Timing", ""]
    stages = _stage_timings(trace)
    for key, value in stages.items():
        label = _STAGE_LABELS.get(key, key.replace("_", " ").title())
        lines.append(f"{label}:")
        lines.append(f"{value} ms")
    lines.append("")
    lines.append("Total:")
    lines.append(f"{_timing_total_ms(trace)} ms")
    lines.append("")
    for record in _records(trace):
        timing = record.get("timing_ms")
        if timing is None:
            continue
        lines.append(f"{record.get('id')}: {int(timing)} ms")
    if lines[-1] != "":
        lines.append("")
    return lines


def format_summary_text(trace: Mapping[str, Any]) -> str:
    """Human-readable summary view (~10–20 lines)."""
    payload = _normalize_trace(trace)
    if not payload or not payload.get("records"):
        return ""

    projection = project_summary(payload)
    lines = [
        "User:",
        str(projection.get("user") or ""),
        "",
        "Intent:",
        str(projection.get("intent") or "(unknown)"),
        "",
        "Session:",
        str(projection.get("session_merge") or ""),
        "",
        "Planner:",
        str(projection.get("planner_status") or "(unknown)"),
        "",
    ]

    missing = projection.get("missing") or []
    if missing:
        lines.extend(["Missing:"] + [str(item) for item in missing] + [""])

    stage = projection.get("execution_stage")
    if stage is not None:
        lines.extend(["Stage:", str(stage), ""])

    action = projection.get("action")
    lines.extend(
        [
            "Action:",
            "None" if action in (None, "", "null") else str(action),
            "",
            "Reason:",
            str(projection.get("reason") or ""),
            "",
            "Outcome:",
            str(projection.get("outcome") or ""),
            "",
        ]
    )

    session_lines = _format_session_changes(projection.get("session_changes") or [])
    if session_lines:
        lines.extend(session_lines)

    lines.extend(_format_timing_summary(payload))
    return "\n".join(lines).rstrip()


def format_reasoning_text(trace: Mapping[str, Any]) -> str:
    """Human-readable reasoning view optimized for causal debugging."""
    from core.tracing.reasoning import format_reasoning_text as _format_reasoning_text

    payload = _normalize_trace(trace)
    if not payload or not payload.get("records"):
        return ""
    return _format_reasoning_text(payload)


def format_forensic_text(trace: Mapping[str, Any]) -> str:
    """Human-readable forensic view (full detail as text)."""
    from core.tracing.formatters import format_decision_summary

    payload = enrich_forensic_trace(trace)
    if not payload or not payload.get("records"):
        return ""

    lines = [format_decision_summary(payload)]
    session_lines = _format_session_changes(payload.get("session_changes") or [])
    if session_lines:
        lines.append("")
        lines.extend(session_lines)
    lines.append("")
    lines.extend(_format_timing_forensic(payload))
    return "\n".join(part for part in lines if part).rstrip()


def _format_summary_from_projection(projection: Mapping[str, Any]) -> str:
    lines = [
        "User:",
        str(projection.get("user") or ""),
        "",
        "Intent:",
        str(projection.get("intent") or "(unknown)"),
        "",
        "Session:",
        str(projection.get("session_merge") or ""),
        "",
        "Planner:",
        str(projection.get("planner_status") or "(unknown)"),
        "",
    ]
    missing = projection.get("missing") or []
    if missing:
        lines.extend(["Missing:"] + [str(item) for item in missing] + [""])
    stage = projection.get("execution_stage")
    if stage is not None:
        lines.extend(["Stage:", str(stage), ""])
    action = projection.get("action")
    lines.extend(
        [
            "Action:",
            "None" if action in (None, "", "null") else str(action),
            "",
            "Reason:",
            str(projection.get("reason") or ""),
            "",
            "Outcome:",
            str(projection.get("outcome") or ""),
            "",
        ]
    )
    session_lines = _format_session_changes(projection.get("session_changes") or [])
    if session_lines:
        lines.extend(session_lines)
    timing = projection.get("timing") if isinstance(projection.get("timing"), dict) else {}
    total = timing.get("total_ms", 0)
    lines.extend(["Timing", "", "Total:", f"{total} ms", ""])
    return "\n".join(lines).rstrip()


def format_trace_view_text(trace: Optional[Mapping[str, Any]], view: TraceView) -> str:
    """Render the requested diagnostic view as human-readable text."""
    if not trace:
        return ""
    payload = _normalize_trace(trace)
    embedded_view = payload.get("view")
    if embedded_view == "summary" or (view == "summary" and not payload.get("records")):
        if embedded_view == "summary" or payload.get("user") is not None:
            return _format_summary_from_projection(payload)
    if view == "forensic":
        return format_forensic_text(trace)
    if view == "reasoning":
        return format_reasoning_text(trace)
    return format_summary_text(trace)


def build_trace_response_fields(
    forensic_trace: Optional[Mapping[str, Any]],
    view: TraceView,
) -> Dict[str, Any]:
    """Build API response fields from an internal forensic trace."""
    if not forensic_trace:
        return {
            "trace_view": view,
            "decision_trace": None,
            "decision_trace_text": "",
        }
    forensic = enrich_forensic_trace(forensic_trace)
    return {
        "trace_view": view,
        "decision_trace": project_trace(forensic, view),
        "decision_trace_text": format_trace_view_text(forensic, view),
    }
