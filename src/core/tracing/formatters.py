"""Human-readable formatters and graph export for decision traces."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from core.tracing.decision_trace import trace_to_dict

KEY_REJECTED_DECISION_IDS = frozenset(
    {
        "decision.planner.select_action",
        "decision.planner.status",
        "decision.planner.execution_route",
        "decision.pagination.handle_turn",
        "decision.pagination.page_target",
        "decision.browse.resolve_direction",
        "decision.merge.bind_time",
        "decision.execution.eligibility",
        "decision.fingerprint.trust",
        "decision.confirmation.classify_turn",
    }
)

_VALID_CATEGORIES = frozenset({"routing", "inference", "presentation", "persistence"})


def _normalize_trace(trace: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not trace:
        return {}
    if hasattr(trace, "get") and trace.get("records") is not None:
        return trace_to_dict(trace)  # type: ignore[arg-type]
    return dict(trace)


def _records(trace: Mapping[str, Any]) -> List[Dict[str, Any]]:
    records = trace.get("records")
    return list(records) if isinstance(records, list) else []


def _edges(trace: Mapping[str, Any]) -> List[Dict[str, Any]]:
    edges = trace.get("edges")
    return list(edges) if isinstance(edges, list) else []


def _filter_records_with_edges(
    records: Sequence[Dict[str, Any]],
    categories: Optional[Set[str]],
    edges: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    if not categories:
        return list(records)

    seed_ids = {
        str(record["id"])
        for record in records
        if record.get("kind") != "evidence" and record.get("category") in categories
    }
  # include evidence linked to seeded decisions
    changed = True
    visible = set(seed_ids)
    while changed:
        changed = False
        for edge in edges:
            if edge.get("kind") != "depends_on":
                continue
            src, dst = edge.get("from"), edge.get("to")
            if dst in visible and src not in visible:
                visible.add(str(src))
                changed = True
            if src in visible and dst not in visible:
                visible.add(str(dst))
                changed = True

    for record in records:
        if record.get("kind") == "evidence":
            visible.add(str(record.get("id", "")))

    return [record for record in records if str(record.get("id", "")) in visible]


def _winner_label(winner: Any) -> str:
    if winner is None:
        return "null"
    if isinstance(winner, dict):
        try:
            return json.dumps(winner, default=str, ensure_ascii=True)
        except TypeError:
            return str(winner)
    return repr(winner)


def _rejected_candidates(
    records: Sequence[Dict[str, Any]],
    *,
    decision_ids: Optional[Set[str]] = None,
    limit: int = 12,
) -> List[str]:
    lines: List[str] = []
    for record in records:
        if record.get("kind") != "decision":
            continue
        record_id = str(record.get("id", ""))
        if decision_ids and record_id not in decision_ids:
            continue
        for candidate in record.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            if candidate.get("matched"):
                continue
            cid = candidate.get("id", "?")
            code = candidate.get("reason_code", "")
            text = candidate.get("reason_text", "")
            missing = candidate.get("missing_slots") or candidate.get("missing_requirements")
            suffix = f" missing={missing}" if missing else ""
            lines.append(f"  {record_id} / {cid}: {code} — {text}{suffix}")
            if len(lines) >= limit:
                return lines
    return lines


def _reason_code_chain(summary: Mapping[str, Any]) -> List[str]:
    codes: List[str] = []
    for step in summary.get("why_chain") or []:
        if not isinstance(step, dict):
            continue
        if step.get("step") != "decision":
            continue
        code = step.get("reason_code")
        if code and code not in codes:
            codes.append(str(code))
    return codes


def format_decision_summary(
    trace: Optional[Mapping[str, Any]],
    *,
    categories: Optional[Iterable[str]] = None,
) -> str:
    """Render a concise human-readable decision trace summary."""
    payload = _normalize_trace(trace)
    if not payload or not payload.get("records"):
        return ""

    category_set: Optional[Set[str]] = None
    if categories:
        category_set = {c for c in categories if c in _VALID_CATEGORIES}

    records = _filter_records_with_edges(
        _records(payload), category_set, _edges(payload)
    )
    if not records:
        return ""

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    lines = ["=== Decision Trace Summary ==="]

    turn = payload.get("turn") if isinstance(payload.get("turn"), dict) else {}
    if turn.get("user_id"):
        lines.append(f"user_id: {turn['user_id']}")
    if turn.get("text"):
        lines.append(f"text: {turn['text']!r}")

    lines.append("")
    lines.append("Outcome")
    outcome = summary.get("outcome") or {}
    if outcome:
        for key, value in outcome.items():
            lines.append(f"  {key}: {value!r}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("Why")
    for item in summary.get("why_text") or []:
        if item:
            lines.append(f"  - {item}")

    chain_codes = _reason_code_chain(summary)
    if chain_codes:
        lines.append("")
        lines.append("Why chain (reason_code)")
        lines.append(f"  {' → '.join(chain_codes)}")

    first_failed = payload.get("first_failed_invariant")
    lines.append("")
    lines.append("Failed invariants")
    if first_failed:
        lines.append(f"  FIRST: {first_failed}")
    else:
        lines.append("  none")

    lines.append("")
    lines.append("Winning decisions")
    for record in records:
        if record.get("kind") != "decision":
            continue
        if record.get("skipped"):
            continue
        lines.append(
            f"  {record.get('id')} [{record.get('category', 'uncategorized')}] "
            f"{record.get('decision_type')}: winner={_winner_label(record.get('winner'))} "
            f"({record.get('reason_code')})"
        )

    rejected = _rejected_candidates(records)
    lines.append("")
    lines.append("Rejected candidates")
    if rejected:
        lines.extend(rejected)
    else:
        lines.append("  none")

    lines.append("")
    lines.append("Mutations")
    mutation_lines = 0
    for record in records:
        if record.get("kind") != "mutation":
            continue
        lines.append(
            f"  {record.get('field')}: {record.get('previous')!r} → {record.get('new')!r} "
            f"({record.get('reason_code')})"
        )
        mutation_lines += 1
    if mutation_lines == 0:
        lines.append("  none")

    return "\n".join(lines)


def format_decision_failure_context(
    *,
    trace: Optional[Mapping[str, Any]] = None,
    body: Optional[Mapping[str, Any]] = None,
) -> str:
    """Build E2E-friendly failure output from a response body or trace payload."""
    payload = None
    if body and isinstance(body, dict) and body.get("decision_trace"):
        payload = body.get("decision_trace")
    elif trace is not None:
        payload = trace

    normalized = _normalize_trace(payload)
    if not normalized or not normalized.get("records"):
        return ""

    text = format_decision_summary(normalized)
    if not text:
        return ""

    records = _records(normalized)
    key_rejected = _rejected_candidates(
        records, decision_ids=KEY_REJECTED_DECISION_IDS, limit=8
    )
    if key_rejected:
        text = f"{text}\n\nKey rejected candidates\n" + "\n".join(key_rejected)
    return text


def _mermaid_id(node_id: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in node_id)
    return safe.strip("_") or "node"


def decision_trace_to_mermaid(
    trace: Optional[Mapping[str, Any]],
    *,
    categories: Optional[Iterable[str]] = None,
) -> str:
    """Render evidence → decision → mutation edges as a Mermaid flowchart."""
    payload = _normalize_trace(trace)
    if not payload:
        return "flowchart TD\n  empty[No decision trace]"

    category_set: Optional[Set[str]] = None
    if categories:
        category_set = {c for c in categories if c in _VALID_CATEGORIES}

    records = _filter_records_with_edges(
        _records(payload), category_set, _edges(payload)
    )
    record_by_id = {str(r["id"]): r for r in records if r.get("id")}
    if not record_by_id:
        return "flowchart TD\n  empty[No matching records]"

    id_map = {rid: _mermaid_id(rid) for rid in record_by_id}
    lines = ["flowchart TD"]

    for record_id, record in record_by_id.items():
        node = id_map[record_id]
        kind = record.get("kind", "record")
        label = record_id.replace('"', "'")
        if kind == "decision":
            code = record.get("reason_code", "")
            lines.append(f'  {node}["{label}<br/>{code}"]')
        elif kind == "mutation":
            field = record.get("field", "")
            lines.append(f'  {node}["{label}<br/>{field}"]')
        else:
            lines.append(f'  {node}["{label}"]')

    seen_edges: Set[tuple[str, str, str]] = set()
    for edge in _edges(payload):
        kind = edge.get("kind")
        if kind not in {"depends_on", "causes"}:
            continue
        src = str(edge.get("from", ""))
        dst = str(edge.get("to", ""))
        if src not in id_map or dst not in id_map:
            continue
        key = (kind, src, dst)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        arrow = "-.->" if kind == "causes" else "-->"
        lines.append(f"  {id_map[src]} {arrow} {id_map[dst]}")

    return "\n".join(lines)
