"""Compact causal reasoning projection for decision traces.

Optimizes for 30-second root-cause analysis: provenance-first key values,
filtered causal chain, concise availability blocks. Full record detail remains
in the forensic view.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from core.tracing.reason_codes import INPUT_IGNORED_NOT_APPLICABLE

# Decisions omitted from reasoning (forensic only or redundant with summary).
_REASONING_HIDDEN_DECISION_IDS = frozenset(
    {
        "decision.persist.save",
        "decision.persist.reload_verify",
        "decision.turn.outcome",
        "decision.facts.derive_all",
    }
)

# Evidence omitted when duplicated or policy-static.
_REASONING_HIDDEN_EVIDENCE_IDS = frozenset(
    {
        "evidence.facts.inputs",
        "evidence.policy.steps",
    }
)

_REASONING_HIDDEN_EVIDENCE_SUFFIXES = (
    ".cache",
    ".pages",
)

# Skipped confirmation subgraph unless gate was open.
_CONFIRMATION_SKIP_IDS = frozenset(
    {
        "decision.confirmation.classify_turn",
        "decision.confirmation.gate_open",
    }
)

# Business-fact flags hidden when false + not applicable (planner derives routing elsewhere).
_FACTS_NOISE_IDS = frozenset(
    {
        "decision.facts.availability_ready",
        "decision.facts.time_selection_ready",
        "decision.facts.user_confirmation_required",
    }
)

# Rejected candidates worth surfacing (routing failures).
_KEY_REJECTED_CANDIDATE_IDS = frozenset(
    {
        "SEARCH_AVAILABILITY",
        "CONFIRM_BOOKING",
        "CREATE_BOOKING",
        "HOLD_BOOKING",
        "BIND_TIME",
        "no_offers",
        "time_mismatch",
    }
)

_AVAILABILITY_REQUEST_ID = "availability.request"
_AVAILABILITY_RESPONSE_ID = "availability.response"

_FINGERPRINT_SLOTS_ID = "evidence.fingerprint.slots"
_FINGERPRINT_STORED_ID = "evidence.fingerprint.stored"
_FINGERPRINT_COMPUTED_ID = "evidence.fingerprint.computed"
_FINGERPRINT_TRUST_ID = "decision.fingerprint.trust"

_BIND_TIME_ID = "decision.merge.bind_time"
_BIND_TIME_EVIDENCE_ID = "evidence.time_proposal"
_MERGE_SLOT_DIFF_ID = "evidence.merge.slot_diff"


def _records(trace: Mapping[str, Any]) -> List[Dict[str, Any]]:
    raw = trace.get("records")
    return list(raw) if isinstance(raw, list) else []


def _record_by_id(trace: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(record["id"]): record
        for record in _records(trace)
        if record.get("id")
    }


def _format_value(value: Any, *, max_len: int = 120) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        if not value:
            return "(empty)"
        text = ", ".join(str(item) for item in value[:8])
        if len(value) > 8:
            text += f", …+{len(value) - 8}"
        return text
    if isinstance(value, dict):
        try:
            text = json.dumps(value, default=str, ensure_ascii=True)
        except TypeError:
            text = str(value)
    else:
        text = str(value)
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def _is_false_winner(winner: Any) -> bool:
    if winner is False:
        return True
    if winner in (None, "", "null", "closed", "NONE", "skip", "skipped"):
        return True
    return False


def _is_noise_decision(record: Mapping[str, Any]) -> bool:
    record_id = str(record.get("id", ""))
    if record_id in _REASONING_HIDDEN_DECISION_IDS:
        return True

    if record.get("skipped"):
        if record_id in _CONFIRMATION_SKIP_IDS:
            return True
        if record.get("category") == "persistence":
            return True
        # Keep skipped bind/execution only when they explain a failure path.
        if record_id not in {_BIND_TIME_ID, "decision.execution.eligibility"}:
            return True

    if record_id in _FACTS_NOISE_IDS:
        if record.get("reason_code") == INPUT_IGNORED_NOT_APPLICABLE and _is_false_winner(
            record.get("winner")
        ):
            return True

    if record_id == "decision.facts.availability_check_required":
        if record.get("reason_code") == INPUT_IGNORED_NOT_APPLICABLE and not record.get(
            "winner"
        ):
            return True

    if record.get("reason_code") == INPUT_IGNORED_NOT_APPLICABLE:
        if record_id.startswith("decision.confirmation."):
            return True

    if record_id == "decision.confirmation.gate_open" and record.get("winner") == "closed":
        return True

    return False


def _is_noise_evidence(record: Mapping[str, Any]) -> bool:
    record_id = str(record.get("id", ""))
    if record_id in _REASONING_HIDDEN_EVIDENCE_IDS:
        return True
    if any(record_id.endswith(suffix) for suffix in _REASONING_HIDDEN_EVIDENCE_SUFFIXES):
        return True
    return False


def filter_reasoning_records(trace: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Return decision/evidence records that answer causal debugging questions."""
    visible: List[Dict[str, Any]] = []
    for record in _records(trace):
        kind = record.get("kind")
        if kind == "mutation":
            continue
        if kind == "decision":
            if _is_noise_decision(record):
                continue
            visible.append(_compact_decision(record))
            continue
        if kind == "evidence":
            if _is_noise_evidence(record):
                continue
            compact = _compact_evidence(record)
            if compact is not None:
                visible.append(compact)
    return visible


def _compact_evidence(record: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    record_id = str(record.get("id", ""))
    facts = record.get("facts") if isinstance(record.get("facts"), dict) else {}

    if record_id == _AVAILABILITY_REQUEST_ID:
        return {
            "id": record_id,
            "kind": "evidence",
            "evidence_type": record.get("evidence_type"),
            "facts": {
                "endpoint": facts.get("endpoint"),
                "method": facts.get("method"),
                "organization_id": facts.get("organization_id"),
                "service_id": facts.get("service_id"),
                "date": facts.get("date"),
                "start_date": facts.get("start_date"),
                "end_date": facts.get("end_date"),
                "time_constraint": facts.get("time_constraint"),
                "field_provenance": facts.get("field_provenance"),
            },
        }

    if record_id == _AVAILABILITY_RESPONSE_ID:
        return {
            "id": record_id,
            "kind": "evidence",
            "evidence_type": record.get("evidence_type"),
            "facts": {
                "http_status": facts.get("http_status"),
                "available_slot_count": facts.get("available_slot_count"),
                "normalized_slot_count": facts.get("normalized_slot_count"),
                "normalized_status": facts.get("normalized_status"),
                "error": facts.get("error"),
                "field_provenance": facts.get("field_provenance"),
            },
        }

    if record_id == _FINGERPRINT_SLOTS_ID:
        return {
            "id": record_id,
            "kind": "evidence",
            "evidence_type": record.get("evidence_type"),
            "facts": {
                "criteria_slot_keys": facts.get("criteria_slot_keys"),
                "criteria_slots": facts.get("criteria_slots"),
                "intent_name": facts.get("intent_name"),
            },
        }

    if record_id in {_FINGERPRINT_STORED_ID, _FINGERPRINT_COMPUTED_ID}:
        return {
            "id": record_id,
            "kind": "evidence",
            "evidence_type": record.get("evidence_type"),
            "facts": {
                "fingerprint_hash": facts.get("fingerprint_hash"),
                "present": facts.get("present"),
                "skipped": facts.get("skipped"),
            },
        }

    if record_id == _BIND_TIME_EVIDENCE_ID:
        return {
            "id": record_id,
            "kind": "evidence",
            "evidence_type": record.get("evidence_type"),
            "facts": {
                "time_proposal": facts.get("time_proposal"),
                "user_time_norm": facts.get("user_time_norm"),
                "expected_date": facts.get("expected_date"),
            },
        }

    if record_id == _MERGE_SLOT_DIFF_ID:
        return {
            "id": record_id,
            "kind": "evidence",
            "evidence_type": record.get("evidence_type"),
            "facts": {
                key: facts.get(key)
                for key in ("added", "updated", "dropped", "preserved")
                if facts.get(key)
            },
        }

    # Default: pass through compact facts (drop huge blobs).
    compact_facts: Dict[str, Any] = {}
    for key, value in facts.items():
        if key in {
            "raw_response",
            "raw_response_body",
            "query_params",
            "criteria_slots",
        } and record_id not in {_FINGERPRINT_SLOTS_ID}:
            continue
        compact_facts[key] = value
    if not compact_facts:
        return None
    return {
        "id": record_id,
        "kind": "evidence",
        "evidence_type": record.get("evidence_type"),
        "facts": compact_facts,
    }


def _compact_decision(record: Mapping[str, Any]) -> Dict[str, Any]:
    record_id = str(record.get("id", ""))
    payload: Dict[str, Any] = {
        "id": record_id,
        "kind": "decision",
        "decision_type": record.get("decision_type"),
        "winner": record.get("winner"),
        "reason_code": record.get("reason_code"),
        "reason_text": record.get("reason_text"),
        "skipped": bool(record.get("skipped")),
    }

    evaluated = record.get("inputs_evaluated")
    if isinstance(evaluated, dict) and evaluated:
        # Keep only high-signal evaluated inputs.
        keep = {
            key: evaluated[key]
            for key in (
                "missing_slots",
                "skip_reason",
                "matched",
                "availability_resolved",
                "changed_fields",
                "user_time_norm",
                "expected_date",
            )
            if key in evaluated
        }
        if keep:
            payload["inputs_evaluated"] = keep

    candidates_out: List[Dict[str, Any]] = []
    for candidate in record.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        cid = str(candidate.get("id", ""))
        matched = bool(candidate.get("matched"))
        if matched and record_id != _BIND_TIME_ID:
            continue
        if not matched:
            if record_id == "decision.planner.select_action" or cid in _KEY_REJECTED_CANDIDATE_IDS:
                entry: Dict[str, Any] = {
                    "id": cid,
                    "matched": False,
                    "reason_code": candidate.get("reason_code"),
                    "reason_text": candidate.get("reason_text"),
                }
                missing = candidate.get("missing_slots") or candidate.get(
                    "missing_requirements"
                )
                if missing:
                    entry["missing"] = list(missing)
                # Only include predicates when they explain a key routing rejection.
                if record_id == "decision.planner.select_action" and cid in _KEY_REJECTED_CANDIDATE_IDS:
                    predicates = candidate.get("failed_predicates") or []
                    if predicates:
                        entry["failed_predicates"] = predicates[:3]
                candidates_out.append(entry)
    if candidates_out:
        payload["candidates"] = candidates_out

    return payload


def _provenance_entry(
    *,
    field: str,
    value: Any = None,
    source: Optional[str] = None,
    consumer: Optional[str] = None,
    reason: Optional[str] = None,
    omitted: bool = False,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"field": field}
    if omitted:
        entry["omitted"] = True
    else:
        entry["value"] = value
    if source:
        entry["source"] = source
    if consumer:
        entry["consumer"] = consumer
    if reason:
        entry["reason"] = reason
    return entry


def build_provenance(trace: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Derive provenance for key orchestration values from forensic records."""
    by_id = _record_by_id(trace)
    entries: List[Dict[str, Any]] = []

    fp_slots_ev = by_id.get(_FINGERPRINT_SLOTS_ID)
    criteria: Dict[str, Any] = {}
    if fp_slots_ev:
        facts = fp_slots_ev.get("facts") if isinstance(fp_slots_ev.get("facts"), dict) else {}
        raw = facts.get("criteria_slots")
        if isinstance(raw, dict):
            criteria = raw

    merge_diff = by_id.get(_MERGE_SLOT_DIFF_ID)
    diff_facts: Dict[str, Any] = {}
    if merge_diff:
        diff_facts = merge_diff.get("facts") if isinstance(merge_diff.get("facts"), dict) else {}

    # service_id
    if "service_id" in criteria:
        entries.append(
            _provenance_entry(
                field="service_id",
                value=criteria["service_id"],
                source="fingerprint.criteria_slots",
                consumer="fingerprint.trust, availability search",
                reason="normalized search criteria",
            )
        )
    elif diff_facts.get("added") and "service_id" in diff_facts["added"]:
        entries.append(
            _provenance_entry(
                field="service_id",
                value="(see merge)",
                source="luma.slots",
                consumer="session merge",
                reason="new slot from NLU",
            )
        )

    # date_proposal — infer from criteria date when proposal drove fingerprint
    if criteria.get("date"):
        entries.append(
            _provenance_entry(
                field="date_proposal",
                value={"start": criteria.get("date")},
                source="session.date_proposal or NLU",
                consumer="fingerprint criteria, availability search",
                reason="temporal constraint for search",
            )
        )

    # slots.date — durable vs virtual
    status = by_id.get("decision.planner.status")
    missing_slots: List[str] = []
    if status:
        evaluated = status.get("inputs_evaluated")
        if isinstance(evaluated, dict) and isinstance(evaluated.get("missing_slots"), list):
            missing_slots = [str(s) for s in evaluated["missing_slots"]]

    if criteria.get("date"):
        durable_date = "date" not in missing_slots and diff_facts.get("preserved")
        if durable_date and "date" in (diff_facts.get("preserved") or []):
            entries.append(
                _provenance_entry(
                    field="slots.date",
                    value=criteria["date"],
                    source="session.slots.date",
                    consumer="planning, fingerprint",
                )
            )
        else:
            entries.append(
                _provenance_entry(
                    field="slots.date",
                    value=criteria["date"],
                    source="date_proposal (virtual)",
                    consumer="planning expand_slots",
                    reason="not durable until availability bind/confirm",
                )
            )
    elif "date" in missing_slots:
        entries.append(
            _provenance_entry(
                field="slots.date",
                value=None,
                reason="required slot missing",
                omitted=True,
            )
        )

    # time_proposal
    time_ev = by_id.get(_BIND_TIME_EVIDENCE_ID)
    if time_ev:
        facts = time_ev.get("facts") if isinstance(time_ev.get("facts"), dict) else {}
        proposal = facts.get("time_proposal")
        if proposal:
            entries.append(
                _provenance_entry(
                    field="time_proposal",
                    value=proposal,
                    source="NLU time_proposal or time_constraint",
                    consumer="bind_time",
                )
            )

    # availability_fingerprint
    stored = by_id.get(_FINGERPRINT_STORED_ID)
    computed = by_id.get(_FINGERPRINT_COMPUTED_ID)
    trust = by_id.get(_FINGERPRINT_TRUST_ID)
    if stored or computed:
        stored_facts = (
            stored.get("facts") if stored and isinstance(stored.get("facts"), dict) else {}
        )
        computed_facts = (
            computed.get("facts") if computed and isinstance(computed.get("facts"), dict) else {}
        )
        entries.append(
            _provenance_entry(
                field="availability_fingerprint",
                value={
                    "stored": stored_facts.get("fingerprint_hash"),
                    "current": computed_facts.get("fingerprint_hash"),
                },
                source="session.availability_fingerprint",
                consumer="decision.fingerprint.trust",
                reason=trust.get("reason_text") if trust else None,
            )
        )

    # Availability request field provenance (emitter-provided or inferred)
    avail_req = by_id.get(_AVAILABILITY_REQUEST_ID)
    if avail_req:
        facts = avail_req.get("facts") if isinstance(avail_req.get("facts"), dict) else {}
        field_prov = facts.get("field_provenance")
        if isinstance(field_prov, dict):
            for name, detail in field_prov.items():
                if not isinstance(detail, dict):
                    continue
                entries.append(
                    _provenance_entry(
                        field=f"availability_request.{name}",
                        value=detail.get("value"),
                        source=detail.get("source"),
                        consumer=detail.get("consumer", "AvailabilityClient"),
                        reason=detail.get("reason"),
                        omitted=bool(detail.get("omitted")),
                    )
                )
        else:
            if facts.get("service_id") is not None:
                entries.append(
                    _provenance_entry(
                        field="availability_request.service_id",
                        value=facts["service_id"],
                        source="execution search slots",
                        consumer="AvailabilityClient",
                    )
                )
            if facts.get("date"):
                entries.append(
                    _provenance_entry(
                        field="availability_request.date",
                        value=facts["date"],
                        source="date_proposal or slots.date",
                        consumer="AvailabilityClient",
                        reason="temporal constraint for search",
                    )
                )
            if not facts.get("time_constraint"):
                entries.append(
                    _provenance_entry(
                        field="availability_request.time",
                        omitted=True,
                        reason="time not sent; preference applied after search or via constraint window",
                    )
                )

    avail_resp = by_id.get(_AVAILABILITY_RESPONSE_ID)
    if avail_resp:
        facts = avail_resp.get("facts") if isinstance(avail_resp.get("facts"), dict) else {}
        entries.append(
            _provenance_entry(
                field="availability_response.slots",
                value=f"{facts.get('normalized_slot_count', 0)} offers",
                source="availability API",
                consumer="last_execution_result, presented_availability",
                reason=f"http {facts.get('http_status')}",
            )
        )

    return entries


def _turn_context(trace: Mapping[str, Any]) -> Dict[str, Any]:
    from core.tracing.views import (
        _intent_from_trace,
        _missing_slots,
        _outcome_label,
        _planner_status,
        _selected_action,
        _session_merge_label,
    )

    turn = trace.get("turn") if isinstance(trace.get("turn"), dict) else {}
    return {
        "user": turn.get("text", ""),
        "intent": _intent_from_trace(trace),
        "session_merge": _session_merge_label(trace),
        "planner_status": _planner_status(trace),
        "missing_slots": _missing_slots(trace),
        "action": _selected_action(trace),
        "outcome": _outcome_label(trace),
    }


def project_reasoning(trace: Mapping[str, Any]) -> Dict[str, Any]:
    """Structured reasoning projection optimized for causal debugging."""
    from core.tracing.views import (
        _stage_timings,
        _timing_total_ms,
        extract_session_changes,
    )

    return {
        "view": "reasoning",
        "turn_context": _turn_context(trace),
        "provenance": build_provenance(trace),
        "causal_decisions": filter_reasoning_records(trace),
        "session_changes": extract_session_changes(trace),
        "timing": {
            "total_ms": _timing_total_ms(trace),
            "stages": _stage_timings(trace),
        },
        "summary": trace.get("summary", {}),
        # Legacy alias for tests/tools that read .records
        "records": filter_reasoning_records(trace),
    }


def _format_provenance(entries: Sequence[Mapping[str, Any]]) -> List[str]:
    if not entries:
        return []
    lines = ["Key Values", ""]
    for entry in entries:
        field = entry.get("field", "?")
        if entry.get("omitted"):
            lines.append(f"{field}: omitted")
        else:
            lines.append(f"{field}: {_format_value(entry.get('value'))}")
        if entry.get("source"):
            lines.append(f"  source: {entry['source']}")
        if entry.get("consumer"):
            lines.append(f"  consumer: {entry['consumer']}")
        if entry.get("reason"):
            lines.append(f"  reason: {entry['reason']}")
        lines.append("")
    return lines


def _format_causal_decision(record: Mapping[str, Any]) -> List[str]:
    record_id = record.get("id", "?")
    kind = record.get("kind", "record")
    short_id = str(record_id).removeprefix("decision.").removeprefix("evidence.")
    lines = [f"[{kind}] {short_id}"]

    if kind == "evidence":
        facts = record.get("facts") if isinstance(record.get("facts"), dict) else {}
        if record_id == _AVAILABILITY_REQUEST_ID:
            lines[0] = "Availability Request"
            for key in ("service_id", "date", "start_date", "end_date"):
                if facts.get(key) is not None:
                    lines.append(f"  {key}: {facts[key]}")
            tc = facts.get("time_constraint")
            if tc:
                lines.append(f"  time_constraint: {_format_value(tc)}")
            else:
                lines.append("  time: omitted")
            prov = facts.get("field_provenance")
            if isinstance(prov, dict):
                for name, detail in prov.items():
                    if not isinstance(detail, dict):
                        continue
                    if detail.get("omitted"):
                        lines.append(f"    {name}: omitted — {detail.get('reason', '')}")
                    else:
                        lines.append(f"    {name}: {detail.get('value')}")
                        if detail.get("source"):
                            lines.append(f"      source: {detail['source']}")
                        if detail.get("reason"):
                            lines.append(f"      reason: {detail['reason']}")
            return lines

        if record_id == _AVAILABILITY_RESPONSE_ID:
            lines[0] = "Availability Response"
            lines.append(
                f"  slots: {facts.get('normalized_slot_count', 0)} normalized "
                f"({facts.get('available_slot_count', 0)} raw)"
            )
            lines.append(f"  http: {facts.get('http_status')}")
            if facts.get("error"):
                lines.append("  error: true")
            return lines

        for key, value in facts.items():
            if key == "field_provenance":
                continue
            lines.append(f"  {key}: {_format_value(value)}")
        return lines

    winner = record.get("winner")
    code = record.get("reason_code", "")
    text = record.get("reason_text", "")
    lines.append(f"  → {_format_value(winner)} ({code}): {text}")

    for candidate in record.get("candidates") or []:
        if not isinstance(candidate, dict) or candidate.get("matched"):
            continue
        cid = candidate.get("id", "?")
        ccode = candidate.get("reason_code", "")
        ctext = candidate.get("reason_text", "")
        missing = candidate.get("missing")
        suffix = f" missing={missing}" if missing else ""
        lines.append(f"  ✗ {cid}: {ccode} — {ctext}{suffix}")
        for predicate in candidate.get("failed_predicates") or []:
            if isinstance(predicate, dict):
                lines.append(
                    f"      {predicate.get('predicate')}: {predicate.get('reason_code')} "
                    f"(actual={predicate.get('actual')!r})"
                )

    evaluated = record.get("inputs_evaluated")
    if isinstance(evaluated, dict):
        for key, value in evaluated.items():
            lines.append(f"  {key}: {_format_value(value)}")

    return lines


def _format_compact_timing(trace: Mapping[str, Any]) -> str:
    from core.tracing.views import _stage_timings, _timing_total_ms

    parts: List[str] = []
    for key, label in (
        ("luma", "luma"),
        ("merge", "merge"),
        ("business_facts", "facts"),
        ("planner", "planner"),
        ("execution", "exec"),
        ("persistence", "persist"),
    ):
        stages = _stage_timings(trace)
        if key in stages:
            parts.append(f"{label} {stages[key]}ms")
    total = _timing_total_ms(trace)
    if parts:
        return f"Timing: {' | '.join(parts)} | total {total}ms"
    return f"Timing: total {total}ms"


def format_reasoning_text(trace: Mapping[str, Any]) -> str:
    """Human-readable reasoning view optimized for scanability."""
    if not trace or not _records(trace):
        return ""

    projection = project_reasoning(trace)
    ctx = projection.get("turn_context") if isinstance(projection.get("turn_context"), dict) else {}
    lines = ["=== Reasoning Trace ===", ""]

    user = ctx.get("user")
    if user:
        lines.append(f"User: {user!r}")
    intent = ctx.get("intent") or "(unknown)"
    status = ctx.get("planner_status") or "?"
    action = ctx.get("action")
    action_label = "None" if action in (None, "", "null") else str(action)
    lines.append(f"Intent: {intent} → {status} | Action: {action_label}")
    missing = ctx.get("missing_slots") or []
    if missing:
        lines.append(f"Missing: {', '.join(missing)}")
    lines.append("")

    prov_lines = _format_provenance(projection.get("provenance") or [])
    if prov_lines:
        lines.extend(prov_lines)

    decisions = projection.get("causal_decisions") or []
    if decisions:
        lines.extend(["Causal Chain", ""])
        for record in decisions:
            # Skip duplicate availability blocks when provenance section covered request
            rid = str(record.get("id", ""))
            if rid in {_AVAILABILITY_REQUEST_ID, _AVAILABILITY_RESPONSE_ID}:
                lines.extend(_format_causal_decision(record))
                lines.append("")
                continue
            lines.extend(_format_causal_decision(record))
            lines.append("")

    from core.tracing.views import extract_session_changes, _format_session_changes

    session_lines = _format_session_changes(extract_session_changes(trace))
    if session_lines:
        lines.extend(session_lines)

    lines.append(_format_compact_timing(trace))
    return "\n".join(lines).rstrip()


def reasoning_line_count(trace: Mapping[str, Any]) -> int:
    """Non-empty line count for tests comparing verbosity."""
    text = format_reasoning_text(trace)
    return len([line for line in text.splitlines() if line.strip()])
