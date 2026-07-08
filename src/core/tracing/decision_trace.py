"""
Decision trace framework for DialogCart Core (Phase 0).

Append-only evidence / decision / mutation records assembled into a frozen DAG.

Enable with ``DIALOGCART_TRACE_DECISIONS=1``, header ``X-Debug-Decision-Trace: true``,
or query param ``?trace=summary`` (also ``reasoning``, ``forensic``, legacy ``decision``).
"""

from __future__ import annotations

import copy
import json
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

from core.tracing.reason_codes import TRACE_EMPTY

TRACE_ENV_VAR = "DIALOGCART_TRACE_DECISIONS"
TRACE_HEADER = "X-Debug-Decision-Trace"
TRACE_QUERY_PARAM = "trace"
TRACE_VERSION = "1.1"
DEFAULT_ROOT_ID = "decision.turn.outcome"

_VALID_SUBSYSTEMS = frozenset(
    {"api", "session", "planning", "orchestration", "execution"}
)
_EDGE_KINDS = frozenset({"child", "depends_on", "causes"})

_current_trace: ContextVar[Optional["TurnTrace"]] = ContextVar(
    "dialogcart_decision_trace", default=None
)
_last_trace: ContextVar[Optional[Mapping[str, Any]]] = ContextVar(
    "dialogcart_last_decision_trace", default=None
)
_request_trace_enabled: ContextVar[Optional[bool]] = ContextVar(
    "dialogcart_decision_trace_request_enabled", default=None
)
_request_trace_view: ContextVar[Optional[str]] = ContextVar(
    "dialogcart_decision_trace_request_view", default=None
)


@dataclass(frozen=True)
class FailedPredicate:
    predicate: str
    reason_code: str
    actual: Any = None


@dataclass(frozen=True)
class Candidate:
    id: str
    matched: bool
    reason_code: str
    reason_text: str
    blocking_requirements: Tuple[str, ...] = ()
    missing_requirements: Tuple[str, ...] = ()
    failed_predicates: Tuple[FailedPredicate, ...] = ()
    satisfied_requirements: Tuple[str, ...] = ()
    missing_slots: Tuple[str, ...] = ()


@dataclass(frozen=True)
class IgnoredInput:
    reason_code: str
    reason_text: str


@dataclass(frozen=True)
class InvariantAttachment:
    invariant_id: str
    invariant_ok: bool
    message: str = ""


class DecisionTraceError(ValueError):
    """Raised when trace integrity rules are violated."""


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_decision_trace_enabled(
    *,
    header_value: Optional[str] = None,
    query_trace: Optional[str] = None,
) -> bool:
    """Return True when decision tracing is enabled for this request."""
    request_flag = _request_trace_enabled.get()
    if request_flag is not None:
        return request_flag

    from core.tracing.views import resolve_trace_view

    enabled, _view = resolve_trace_view(
        query_trace=query_trace,
        header_value=header_value,
        env_enabled=_truthy(os.getenv(TRACE_ENV_VAR, "")),
    )
    return enabled


def get_request_trace_view() -> str:
    """Return the diagnostic view requested for this turn (default summary)."""
    from core.tracing.views import DEFAULT_TRACE_VIEW

    view = _request_trace_view.get()
    if view in {"summary", "reasoning", "forensic"}:
        return view
    return DEFAULT_TRACE_VIEW


def set_request_decision_trace_enabled(enabled: bool, *, view: Optional[str] = None) -> None:
    """Set per-request trace flag (used by API layer after parsing header/query)."""
    _request_trace_enabled.set(enabled)
    if view is not None:
        _request_trace_view.set(view)


def clear_request_decision_trace_enabled() -> None:
    _request_trace_enabled.set(None)
    _request_trace_view.set(None)


def clear_request_decision_trace_context() -> None:
    """End-of-request cleanup; keeps the last finalized trace for debugging."""
    _current_trace.set(None)
    _request_trace_enabled.set(None)
    _request_trace_view.set(None)


def reset_decision_trace_state() -> None:
    """Clear per-context trace collectors (for tests and request teardown)."""
    clear_request_decision_trace_context()
    _last_trace.set(None)


def _candidate_to_dict(candidate: Candidate) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": candidate.id,
        "matched": candidate.matched,
        "reason_code": candidate.reason_code,
        "reason_text": candidate.reason_text,
    }
    if candidate.blocking_requirements:
        payload["blocking_requirements"] = list(candidate.blocking_requirements)
    if candidate.missing_requirements:
        payload["missing_requirements"] = list(candidate.missing_requirements)
    if candidate.failed_predicates:
        payload["failed_predicates"] = [
            {
                "predicate": item.predicate,
                "reason_code": item.reason_code,
                **({} if item.actual is None else {"actual": item.actual}),
            }
            for item in candidate.failed_predicates
        ]
    if candidate.satisfied_requirements:
        payload["satisfied_requirements"] = list(candidate.satisfied_requirements)
    if candidate.missing_slots:
        payload["missing_slots"] = list(candidate.missing_slots)
    return payload


def _ignored_to_dict(
    ignored: Optional[Mapping[str, IgnoredInput]],
) -> Dict[str, Any]:
    if not ignored:
        return {}
    return {
        key: {
            "reason_code": value.reason_code,
            "reason_text": value.reason_text,
        }
        for key, value in ignored.items()
    }


def _invariant_to_dict(item: InvariantAttachment) -> Dict[str, Any]:
    return {
        "invariant_id": item.invariant_id,
        "invariant_ok": item.invariant_ok,
        "message": item.message,
    }


def _unwrap_frozen(value: Any) -> Any:
    """Convert a frozen trace mapping into plain JSON-serializable data."""
    if isinstance(value, MappingProxyType):
        return {key: _unwrap_frozen(inner) for key, inner in value.items()}
    if isinstance(value, tuple):
        return [_unwrap_frozen(item) for item in value]
    if isinstance(value, dict):
        return {key: _unwrap_frozen(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_unwrap_frozen(item) for item in value]
    return value


def trace_to_dict(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a plain dict suitable for JSON serialization and schema validation."""
    return json.loads(json.dumps(_unwrap_frozen(payload), default=str))


def _freeze_mapping(value: Any) -> Any:
    """Recursively freeze mappings for finalized trace payloads."""
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_mapping(inner) for key, inner in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_mapping(item) for item in value)
    return value


def _deep_copy_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def empty_decision_trace(
    *,
    user_id: str = "",
    text: str = "",
    transaction_id: str = "",
) -> Dict[str, Any]:
    """Return a valid empty trace payload (tracing enabled, no subsystem emitters)."""
    return {
        "version": TRACE_VERSION,
        "turn": {
            "user_id": user_id,
            "text": text,
            **({"transaction_id": transaction_id} if transaction_id else {}),
        },
        "root_id": None,
        "records": [],
        "edges": [],
        "summary": {
            "outcome": {},
            "why_text": [],
            "why_chain": [],
        },
        "first_failed_invariant": None,
        "timing_ms": 0,
        "stage_timings": {},
        "session_changes": [],
    }


@contextmanager
def measure_stage(stage: str) -> Iterator[None]:
    """Record wall-clock duration for a pipeline stage on the active trace."""
    trace = TurnTrace.current()
    if trace is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        trace.record_stage_timing(stage, (time.perf_counter() - started) * 1000.0)


class TurnTrace:
    """Collects append-only decision trace records for one conversational turn."""

    def __init__(
        self,
        *,
        user_id: str = "",
        text: str = "",
        transaction_id: str = "",
    ) -> None:
        self._user_id = user_id
        self._text = text
        self._transaction_id = transaction_id
        self._records: List[Dict[str, Any]] = []
        self._edges: List[Dict[str, str]] = []
        self._record_ids: set[str] = set()
        self._decision_ids: set[str] = set()
        self._adjacency: Dict[str, set[str]] = {}
        self._mutation_sequence = 0
        self._root_id: Optional[str] = None
        self._finalized = False
        self._frozen_payload: Optional[Mapping[str, Any]] = None
        self._started_at = time.perf_counter()
        self._stage_timings: Dict[str, int] = {}
        self._scope_stack: List[str] = []

    @staticmethod
    def begin(
        *,
        user_id: str = "",
        text: str = "",
        transaction_id: str = "",
        force: bool = False,
    ) -> Optional["TurnTrace"]:
        if not force and not is_decision_trace_enabled():
            return None
        existing = _current_trace.get()
        if existing is not None and not existing._finalized and not force:
            return existing
        trace = TurnTrace(
            user_id=user_id,
            text=text,
            transaction_id=transaction_id,
        )
        _current_trace.set(trace)
        return trace

    @staticmethod
    def current() -> Optional["TurnTrace"]:
        if not is_decision_trace_enabled():
            return None
        return _current_trace.get()

    @property
    def finalized(self) -> bool:
        return self._finalized

    def record_stage_timing(self, stage: str, duration_ms: float) -> None:
        """Accumulate timing for a named pipeline stage."""
        self._assert_mutable()
        key = (stage or "").strip()
        if not key:
            return
        elapsed = max(0, int(duration_ms))
        self._stage_timings[key] = self._stage_timings.get(key, 0) + elapsed

    def set_root_id(self, root_id: str) -> None:
        self._assert_mutable()
        self._root_id = root_id

    @contextmanager
    def scope(self, parent_id: str) -> Iterator[None]:
        self._assert_mutable()
        self._assert_record_exists(parent_id)
        self._scope_stack.append(parent_id)
        try:
            yield
        finally:
            self._scope_stack.pop()

    def has_record(self, record_id: str) -> bool:
        return record_id in self._record_ids

    def emit_evidence(
        self,
        evidence_type: str,
        *,
        subsystem: str,
        facts: Mapping[str, Any],
        node_id: Optional[str] = None,
        source: str = "",
        observed_at_stage: str = "",
        parent_id: Optional[str] = None,
    ) -> str:
        self._assert_mutable()
        self._validate_subsystem(subsystem)
        record_id = node_id or f"evidence.{evidence_type.lower()}"
        self._assert_unique_id(record_id)

        record: Dict[str, Any] = {
            "id": record_id,
            "kind": "evidence",
            "subsystem": subsystem,
            "evidence_type": evidence_type,
            "facts": _deep_copy_jsonable(dict(facts)),
            "child_ids": [],
        }
        if source:
            record["source"] = source
        if observed_at_stage:
            record["observed_at_stage"] = observed_at_stage

        self._append_record(record)
        self._link_parent_child(parent_id, record_id)
        return record_id

    def decide(
        self,
        decision_type: str,
        *,
        subsystem: str,
        winner: Any,
        reason_code: str,
        reason_text: str,
        node_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        depends_on: Sequence[str] = (),
        candidates: Optional[Sequence[Candidate]] = None,
        inputs_evaluated: Optional[Mapping[str, Any]] = None,
        inputs_ignored: Optional[Mapping[str, IgnoredInput]] = None,
        invariants: Optional[Sequence[InvariantAttachment]] = None,
        skipped: bool = False,
        supersedes_id: Optional[str] = None,
        is_root: bool = False,
        category: Optional[str] = None,
    ) -> str:
        self._assert_mutable()
        self._validate_subsystem(subsystem)
        record_id = node_id or f"decision.{decision_type.lower()}"
        self._assert_unique_id(record_id)

        for dependency_id in depends_on:
            self._assert_record_exists(dependency_id)

        record: Dict[str, Any] = {
            "id": record_id,
            "kind": "decision",
            "subsystem": subsystem,
            "decision_type": decision_type,
            "winner": _deep_copy_jsonable(winner),
            "reason_code": reason_code,
            "reason_text": reason_text,
            "depends_on": list(depends_on),
            "child_ids": [],
            "invariants": [
                _invariant_to_dict(item) for item in (invariants or ())
            ],
            "skipped": skipped,
            "supersedes_id": supersedes_id,
        }
        if category:
            record["category"] = category
        if candidates:
            record["candidates"] = [_candidate_to_dict(c) for c in candidates]
        if inputs_evaluated:
            record["inputs_evaluated"] = _deep_copy_jsonable(dict(inputs_evaluated))
        if inputs_ignored:
            record["inputs_ignored"] = _ignored_to_dict(inputs_ignored)

        self._append_record(record)
        self._decision_ids.add(record_id)
        for dependency_id in depends_on:
            self._add_edge(dependency_id, record_id, "depends_on")
        self._link_parent_child(parent_id, record_id)
        if is_root:
            self._root_id = record_id
        return record_id

    def emit_mutation(
        self,
        decision_id: str,
        *,
        subsystem: str,
        field: str,
        previous: Any,
        new: Any,
        reason_code: str,
        reason_text: str,
        node_id: Optional[str] = None,
        presentation_only: bool = False,
    ) -> str:
        self._assert_mutable()
        self._validate_subsystem(subsystem)
        if decision_id not in self._decision_ids:
            raise DecisionTraceError(
                f"emit_mutation requires an existing decision id; got {decision_id!r}"
            )

        mutation_id = node_id or f"mutation.{field.replace('.', '_')}.{self._mutation_sequence + 1}"
        self._assert_unique_id(mutation_id)

        sequence = self._mutation_sequence
        self._mutation_sequence += 1

        record: Dict[str, Any] = {
            "id": mutation_id,
            "kind": "mutation",
            "subsystem": subsystem,
            "decision_id": decision_id,
            "field": field,
            "previous": _deep_copy_jsonable(previous),
            "new": _deep_copy_jsonable(new),
            "reason_code": reason_code,
            "reason_text": reason_text,
            "sequence": sequence,
        }
        if presentation_only:
            record["presentation_only"] = True

        self._append_record(record)
        self._add_edge(decision_id, mutation_id, "causes")
        return mutation_id

    def finalize(self) -> Mapping[str, Any]:
        if self._finalized and self._frozen_payload is not None:
            return self._frozen_payload

        payload = self._build_payload()
        plain = _deep_copy_jsonable(payload)
        self._frozen_payload = MappingProxyType(
            {key: _freeze_mapping(val) for key, val in plain.items()}
        )
        self._finalized = True
        _last_trace.set(self._frozen_payload)
        _current_trace.set(None)
        return self._frozen_payload

    def _build_payload(self) -> Dict[str, Any]:
        child_map = self._child_ids_by_parent()
        enriched_records = []
        for record in self._records:
            enriched = copy.deepcopy(record)
            children = child_map.get(record["id"])
            if children:
                enriched["child_ids"] = children
            enriched_records.append(enriched)

        summary = self._build_summary(enriched_records)
        timing_ms = int((time.perf_counter() - self._started_at) * 1000)

        first_failed = None
        for record in enriched_records:
            if record.get("kind") != "decision":
                continue
            for invariant in record.get("invariants") or []:
                if not invariant.get("invariant_ok", True):
                    first_failed = invariant.get("invariant_id")
                    break
            if first_failed:
                break

        turn: Dict[str, Any] = {
            "user_id": self._user_id,
            "text": self._text,
        }
        if self._transaction_id:
            turn["transaction_id"] = self._transaction_id

        from core.tracing.views import extract_session_changes

        return {
            "version": TRACE_VERSION,
            "turn": turn,
            "root_id": self._root_id,
            "records": enriched_records,
            "edges": copy.deepcopy(self._edges),
            "summary": summary,
            "first_failed_invariant": first_failed,
            "timing_ms": timing_ms,
            "stage_timings": dict(self._stage_timings),
            "session_changes": extract_session_changes(
                {"records": enriched_records}
            ),
        }

    def _build_summary(self, records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        decisions = [r for r in records if r.get("kind") == "decision"]
        mutations = [r for r in records if r.get("kind") == "mutation"]
        evidence = [r for r in records if r.get("kind") == "evidence"]

        outcome: Dict[str, Any] = {}
        why_text: List[str] = []
        why_chain: List[Dict[str, Any]] = []

        root = next((d for d in decisions if d.get("id") == self._root_id), None)
        if root is None and decisions:
            root = decisions[-1]

        if root is not None:
            winner = root.get("winner")
            if isinstance(winner, dict):
                outcome = dict(winner)
            why_text.append(root.get("reason_text", ""))

        for record in decisions:
            text = record.get("reason_text")
            if text and text not in why_text:
                why_text.append(text)
            if len(why_text) >= 8:
                break

        if not why_text:
            why_text = [TRACE_EMPTY]

        for record in evidence:
            why_chain.append(
                {
                    "step": "evidence",
                    "id": record["id"],
                    "evidence_type": record.get("evidence_type"),
                    "facts": record.get("facts", {}),
                }
            )
        for record in decisions:
            why_chain.append(
                {
                    "step": "decision",
                    "id": record["id"],
                    "decision_type": record.get("decision_type"),
                    "winner": record.get("winner"),
                    "reason_code": record.get("reason_code"),
                }
            )
        for record in mutations:
            why_chain.append(
                {
                    "step": "mutation",
                    "id": record["id"],
                    "field": record.get("field"),
                    "previous": record.get("previous"),
                    "new": record.get("new"),
                    "reason_code": record.get("reason_code"),
                }
            )

        return {
            "outcome": outcome,
            "why_text": why_text[:8],
            "why_chain": why_chain,
        }

    def _child_ids_by_parent(self) -> Dict[str, List[str]]:
        child_map: Dict[str, List[str]] = {}
        for edge in self._edges:
            if edge.get("kind") != "child":
                continue
            child_map.setdefault(edge["from"], []).append(edge["to"])
        return child_map

    def _append_record(self, record: Mapping[str, Any]) -> None:
        record_id = record["id"]
        self._record_ids.add(record_id)
        self._adjacency.setdefault(record_id, set())
        self._records.append(copy.deepcopy(dict(record)))

    def _link_parent_child(
        self,
        parent_id: Optional[str],
        child_id: str,
    ) -> None:
        effective_parent = parent_id
        if effective_parent is None and self._scope_stack:
            effective_parent = self._scope_stack[-1]
        if effective_parent:
            self._assert_record_exists(effective_parent)
            self._add_edge(effective_parent, child_id, "child")

    def _add_edge(self, from_id: str, to_id: str, kind: str) -> None:
        if kind not in _EDGE_KINDS:
            raise DecisionTraceError(f"Unknown edge kind: {kind!r}")
        self._assert_record_exists(from_id)
        self._assert_record_exists(to_id)
        if self._creates_cycle(from_id, to_id):
            raise DecisionTraceError(
                f"Edge {from_id!r} -> {to_id!r} ({kind}) would create a cycle"
            )
        self._edges.append({"from": from_id, "to": to_id, "kind": kind})
        self._adjacency.setdefault(from_id, set()).add(to_id)
        self._adjacency.setdefault(to_id, set())

    def _creates_cycle(self, from_id: str, to_id: str) -> bool:
        if from_id == to_id:
            return True
        stack = [to_id]
        visited = set()
        while stack:
            node = stack.pop()
            if node == from_id:
                return True
            if node in visited:
                continue
            visited.add(node)
            stack.extend(self._adjacency.get(node, ()))
        return False

    def _assert_unique_id(self, record_id: str) -> None:
        if record_id in self._record_ids:
            raise DecisionTraceError(f"Duplicate trace record id: {record_id!r}")

    def _assert_record_exists(self, record_id: str) -> None:
        if record_id not in self._record_ids:
            raise DecisionTraceError(f"Unknown trace record id: {record_id!r}")

    def _assert_mutable(self) -> None:
        if self._finalized:
            raise DecisionTraceError("Cannot modify a finalized decision trace")

    @staticmethod
    def _validate_subsystem(subsystem: str) -> None:
        if subsystem not in _VALID_SUBSYSTEMS:
            raise DecisionTraceError(f"Invalid subsystem: {subsystem!r}")


def emit_evidence(
    evidence_type: str,
    *,
    subsystem: str,
    facts: Mapping[str, Any],
    node_id: Optional[str] = None,
    source: str = "",
    observed_at_stage: str = "",
    parent_id: Optional[str] = None,
) -> Optional[str]:
    trace = TurnTrace.current()
    if trace is None:
        return None
    return trace.emit_evidence(
        evidence_type,
        subsystem=subsystem,
        facts=facts,
        node_id=node_id,
        source=source,
        observed_at_stage=observed_at_stage,
        parent_id=parent_id,
    )


def decide(
    decision_type: str,
    *,
    subsystem: str,
    winner: Any,
    reason_code: str,
    reason_text: str,
    node_id: Optional[str] = None,
    parent_id: Optional[str] = None,
    depends_on: Sequence[str] = (),
    candidates: Optional[Sequence[Candidate]] = None,
    inputs_evaluated: Optional[Mapping[str, Any]] = None,
    inputs_ignored: Optional[Mapping[str, IgnoredInput]] = None,
    invariants: Optional[Sequence[InvariantAttachment]] = None,
    skipped: bool = False,
    supersedes_id: Optional[str] = None,
    is_root: bool = False,
    category: Optional[str] = None,
) -> Optional[str]:
    trace = TurnTrace.current()
    if trace is None:
        return None
    return trace.decide(
        decision_type,
        subsystem=subsystem,
        winner=winner,
        reason_code=reason_code,
        reason_text=reason_text,
        node_id=node_id,
        parent_id=parent_id,
        depends_on=depends_on,
        candidates=candidates,
        inputs_evaluated=inputs_evaluated,
        inputs_ignored=inputs_ignored,
        invariants=invariants,
        skipped=skipped,
        supersedes_id=supersedes_id,
        is_root=is_root,
        category=category,
    )


def emit_mutation(
    decision_id: str,
    *,
    subsystem: str,
    field: str,
    previous: Any,
    new: Any,
    reason_code: str,
    reason_text: str,
    node_id: Optional[str] = None,
    presentation_only: bool = False,
) -> Optional[str]:
    trace = TurnTrace.current()
    if trace is None:
        return None
    return trace.emit_mutation(
        decision_id,
        subsystem=subsystem,
        field=field,
        previous=previous,
        new=new,
        reason_code=reason_code,
        reason_text=reason_text,
        node_id=node_id,
        presentation_only=presentation_only,
    )


def finalize_turn_trace() -> Optional[Mapping[str, Any]]:
    trace = TurnTrace.current()
    if trace is None:
        return None
    return trace.finalize()


def get_last_decision_trace() -> Optional[Mapping[str, Any]]:
    return _last_trace.get()


def attach_decision_trace_to_result(result: Dict[str, Any]) -> None:
    """Attach finalized decision trace to a handle_message / API result."""
    if not is_decision_trace_enabled():
        return
    trace = TurnTrace.current()
    if trace is None:
        result["decision_trace"] = empty_decision_trace()
        return
    if trace.finalized:
        frozen = get_last_decision_trace()
        if frozen is not None:
            result["decision_trace"] = trace_to_dict(frozen)
        return
    result["decision_trace"] = trace_to_dict(trace.finalize())


def build_decision_trace_for_request(
    *,
    user_id: str,
    text: str,
    transaction_id: str = "",
) -> Mapping[str, Any]:
    """Begin (if needed) and finalize an empty trace for API responses."""
    if not is_decision_trace_enabled():
        return empty_decision_trace(
            user_id=user_id,
            text=text,
            transaction_id=transaction_id,
        )
    trace = TurnTrace.begin(
        user_id=user_id,
        text=text,
        transaction_id=transaction_id,
    )
    if trace is None:
        return empty_decision_trace(
            user_id=user_id,
            text=text,
            transaction_id=transaction_id,
        )
    return trace.finalize()
