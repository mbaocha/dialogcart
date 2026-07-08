"""
Reusable invariant tracing for DialogCart turn pipeline.

Enable with environment variable ``DIALOGCART_TRACE_INVARIANTS=1``.
"""

from __future__ import annotations

import json
import os
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

TRACE_ENV_VAR = "DIALOGCART_TRACE_INVARIANTS"

STAGE_ORDER: Sequence[str] = (
    "session_load",
    "merge",
    "planner",
    "business_facts",
    "fingerprint",
    "tool_execution",
    "pagination",
    "persistence",
    "save_session",
    "reload_session",
)

STAGE_OWNERS: Dict[str, str] = {
    "session_load": "session",
    "merge": "session",
    "planner": "planning",
    "business_facts": "planning",
    "fingerprint": "orchestration",
    "tool_execution": "execution",
    "pagination": "orchestration",
    "persistence": "session",
    "save_session": "session",
    "reload_session": "session",
}


def is_trace_enabled() -> bool:
    return os.getenv(TRACE_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class InvariantResult:
    invariant_id: str
    invariant_ok: bool
    message: str = ""


@dataclass
class StageRecord:
    stage: str
    owner: str
    invariant_id: str
    invariant_ok: bool
    allowed_mutations: List[str] = field(default_factory=list)
    forbidden_mutations: List[str] = field(default_factory=list)
    state_snapshot: Dict[str, Any] = field(default_factory=dict)
    message: str = ""
    skipped: bool = False
    affected: bool = False


@dataclass
class TurnTraceSummary:
    user_id: str = ""
    text: str = ""
    stages: List[StageRecord] = field(default_factory=list)
    first_failed_invariant: Optional[str] = None
    first_failed_owner: Optional[str] = None
    first_failed_stage: Optional[str] = None
    first_failed_message: Optional[str] = None
    downstream_stages_affected: List[str] = field(default_factory=list)
    downstream_stages_skipped: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "text": self.text,
            "stages": [asdict(s) for s in self.stages],
            "first_failed_invariant": self.first_failed_invariant,
            "first_failed_owner": self.first_failed_owner,
            "first_failed_stage": self.first_failed_stage,
            "first_failed_message": self.first_failed_message,
            "downstream_stages_affected": self.downstream_stages_affected,
            "downstream_stages_skipped": self.downstream_stages_skipped,
        }


_current_trace: ContextVar[Optional["TurnInvariantTrace"]] = ContextVar(
    "dialogcart_invariant_trace", default=None
)
_last_trace_summary: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "dialogcart_last_invariant_trace_summary", default=None
)
_defer_finalize: ContextVar[bool] = ContextVar(
    "dialogcart_invariant_trace_defer_finalize", default=False
)


class TurnInvariantTrace:
    """Collects per-stage invariant results for one conversational turn."""

    def __init__(self, user_id: str = "", text: str = "") -> None:
        self.summary = TurnTraceSummary(user_id=user_id, text=text)
        self._first_failure_index: Optional[int] = None
        self._finalized = False

    @staticmethod
    def begin(user_id: str, text: str) -> Optional["TurnInvariantTrace"]:
        if not is_trace_enabled():
            return None
        existing = _current_trace.get()
        if existing is not None and not existing._finalized:
            return existing
        trace = TurnInvariantTrace(user_id=user_id, text=text)
        _current_trace.set(trace)
        return trace

    @staticmethod
    def current() -> Optional["TurnInvariantTrace"]:
        if not is_trace_enabled():
            return None
        return _current_trace.get()

    @staticmethod
    def defer_finalize(deferred: bool = True) -> None:
        _defer_finalize.set(deferred)

    @staticmethod
    def should_defer_finalize() -> bool:
        return _defer_finalize.get()

    def record_stage(
        self,
        stage: str,
        *,
        owner: Optional[str] = None,
        invariant_id: str,
        invariant_ok: bool,
        allowed_mutations: Optional[List[str]] = None,
        forbidden_mutations: Optional[List[str]] = None,
        state_snapshot: Optional[Dict[str, Any]] = None,
        message: str = "",
        skipped: bool = False,
    ) -> None:
        if self._finalized:
            return

        stage_owner = owner or STAGE_OWNERS.get(stage, "core")
        affected = (
            self._first_failure_index is not None
            and not skipped
            and invariant_ok
        )

        record = StageRecord(
            stage=stage,
            owner=stage_owner,
            invariant_id=invariant_id,
            invariant_ok=invariant_ok,
            allowed_mutations=list(allowed_mutations or []),
            forbidden_mutations=list(forbidden_mutations or []),
            state_snapshot=dict(state_snapshot or {}),
            message=message,
            skipped=skipped,
            affected=affected,
        )
        self.summary.stages.append(record)

        if skipped:
            if stage not in self.summary.downstream_stages_skipped:
                self.summary.downstream_stages_skipped.append(stage)
            return

        if affected and stage not in self.summary.downstream_stages_affected:
            self.summary.downstream_stages_affected.append(stage)

        if not invariant_ok and self._first_failure_index is None:
            self._first_failure_index = len(self.summary.stages) - 1
            self.summary.first_failed_invariant = invariant_id
            self.summary.first_failed_owner = stage_owner
            self.summary.first_failed_stage = stage
            self.summary.first_failed_message = message

    def record_checks(
        self,
        stage: str,
        checks: Sequence[InvariantResult],
        *,
        allowed_mutations: Optional[List[str]] = None,
        forbidden_mutations: Optional[List[str]] = None,
        state_snapshot: Optional[Dict[str, Any]] = None,
        skipped: bool = False,
        owner: Optional[str] = None,
    ) -> None:
        if not checks:
            self.record_stage(
                stage,
                owner=owner,
                invariant_id=f"{stage}.noop",
                invariant_ok=True,
                allowed_mutations=allowed_mutations,
                forbidden_mutations=forbidden_mutations,
                state_snapshot=state_snapshot,
                skipped=skipped,
            )
            return

        for check in checks:
            self.record_stage(
                stage,
                owner=owner,
                invariant_id=check.invariant_id,
                invariant_ok=check.invariant_ok,
                allowed_mutations=allowed_mutations,
                forbidden_mutations=forbidden_mutations,
                state_snapshot=state_snapshot,
                message=check.message,
                skipped=skipped,
            )
            if not check.invariant_ok:
                break

    def finalize(self) -> Dict[str, Any]:
        if self._finalized:
            return self.summary.to_dict()
        self._mark_unvisited_stages_skipped()
        self._finalized = True
        summary = self.summary.to_dict()
        _last_trace_summary.set(summary)
        _current_trace.set(None)
        _defer_finalize.set(False)
        return summary

    def _mark_unvisited_stages_skipped(self) -> None:
        visited = {record.stage for record in self.summary.stages}
        for stage in STAGE_ORDER:
            if stage not in visited:
                self.summary.stages.append(
                    StageRecord(
                        stage=stage,
                        owner=STAGE_OWNERS.get(stage, "core"),
                        invariant_id=f"{stage}.not_reached",
                        invariant_ok=True,
                        skipped=True,
                        message="stage not reached this turn",
                    )
                )


def trace_stage(
    stage: str,
    check_fn: Callable[[], Sequence[InvariantResult]],
    *,
    allowed_mutations: Optional[List[str]] = None,
    forbidden_mutations: Optional[List[str]] = None,
    state_snapshot: Optional[Dict[str, Any]] = None,
    skipped: bool = False,
    owner: Optional[str] = None,
) -> None:
    """Run invariant checks for a pipeline stage when tracing is enabled."""
    trace = TurnInvariantTrace.current()
    if trace is None:
        return
    if skipped:
        trace.record_stage(
            stage,
            owner=owner,
            invariant_id=f"{stage}.skipped",
            invariant_ok=True,
            allowed_mutations=allowed_mutations,
            forbidden_mutations=forbidden_mutations,
            state_snapshot=state_snapshot,
            skipped=True,
            message="stage not applicable this turn",
        )
        return

    try:
        checks = list(check_fn())
    except Exception as exc:  # pragma: no cover - defensive
        checks = [
            InvariantResult(
                invariant_id=f"{stage}.check_error",
                invariant_ok=False,
                message=str(exc),
            )
        ]

    trace.record_checks(
        stage,
        checks,
        allowed_mutations=allowed_mutations,
        forbidden_mutations=forbidden_mutations,
        state_snapshot=state_snapshot,
        owner=owner,
    )


def finalize_turn_trace() -> Optional[Dict[str, Any]]:
    trace = TurnInvariantTrace.current()
    if trace is None or TurnInvariantTrace.should_defer_finalize():
        return None
    return trace.finalize()


def attach_trace_to_result(result: Dict[str, Any]) -> None:
    """Attach finalized trace summary to a handle_message / API result."""
    if not is_trace_enabled():
        return
    trace = TurnInvariantTrace.current()
    if trace is None:
        return
    if TurnInvariantTrace.should_defer_finalize():
        result["_invariant_trace_pending"] = True
        return
    summary = trace.finalize()
    if summary:
        result["_invariant_trace"] = summary


def format_invariant_summary(summary: Optional[Dict[str, Any]] = None) -> str:
    """Human-readable invariant trace for test failure output."""
    if summary is None:
        trace = TurnInvariantTrace.current()
        if trace is not None:
            summary = trace.summary.to_dict()
        else:
            return ""

    if not summary:
        return ""

    lines = ["=== Invariant Trace Summary ==="]
    if summary.get("user_id"):
        lines.append(f"user_id: {summary['user_id']}")
    if summary.get("text"):
        lines.append(f"text: {summary['text']!r}")

    first = summary.get("first_failed_invariant")
    if first:
        lines.append(
            f"FIRST FAILURE: {first} "
            f"(stage={summary.get('first_failed_stage')}, "
            f"owner={summary.get('first_failed_owner')})"
        )
        if summary.get("first_failed_message"):
            lines.append(f"  message: {summary['first_failed_message']}")
    else:
        lines.append("FIRST FAILURE: none (all checked invariants passed)")

    affected = summary.get("downstream_stages_affected") or []
    skipped = summary.get("downstream_stages_skipped") or []
    if affected:
        lines.append(f"downstream affected: {', '.join(affected)}")
    if skipped:
        lines.append(f"stages skipped: {', '.join(skipped)}")

    lines.append("stage pipeline:")
    for record in summary.get("stages") or []:
        status = "SKIP" if record.get("skipped") else (
            "FAIL" if not record.get("invariant_ok") else (
                "AFFECTED" if record.get("affected") else "OK"
            )
        )
        lines.append(
            f"  [{status}] {record.get('stage')} "
            f"({record.get('owner')}) "
            f"{record.get('invariant_id')}"
        )
        snapshot = record.get("state_snapshot") or {}
        if snapshot:
            try:
                snap = json.dumps(snapshot, default=str, ensure_ascii=True)
            except TypeError:
                snap = str(snapshot)
            if len(snap) > 240:
                snap = snap[:237] + "..."
            lines.append(f"    snapshot: {snap}")
        if record.get("message") and status == "FAIL":
            lines.append(f"    detail: {record['message']}")

    return "\n".join(lines)


def get_last_trace_summary() -> Optional[Dict[str, Any]]:
    """Return the most recently finalized trace summary for this context."""
    return _last_trace_summary.get()


def invariant_failure_context() -> str:
    """Return formatted summary for the active or last finalized trace."""
    trace = TurnInvariantTrace.current()
    if trace is not None:
        return format_invariant_summary(trace.summary.to_dict())
    last = get_last_trace_summary()
    if last:
        return format_invariant_summary(last)
    return ""
