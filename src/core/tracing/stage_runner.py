"""Explicit stage runner for orchestration observability.

``StageRunner`` owns turn/stage lifecycle, invariant checks, decision-trace
timing, and result attachment. Business callables return plain values — never
tracing policy metadata.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, TypeVar

from core.tracing.invariant_trace import InvariantResult, attach_trace_to_result, trace_stage
from core.tracing.stage_checks import (
    check_business_facts,
    check_fingerprint,
    check_pagination,
    check_planner,
    check_reload_session,
    check_save_session,
    check_session_load,
    check_tool_execution,
)

logger = logging.getLogger(__name__)
turn_logger = logging.getLogger("core.turn_log")

T = TypeVar("T")


def _record_decision_timing(stage: str, started: float) -> None:
    try:
        from core.tracing.decision_trace import TurnTrace

        trace = TurnTrace.current()
        if trace is not None:
            trace.record_stage_timing(
                stage, (time.perf_counter() - started) * 1000.0
            )
    except ImportError:
        pass


@dataclass
class StageRunner:
    """Owns observability for one conversational turn's orchestration stages."""

    release_direct_decision_trace: bool = True
    #: When True, ``finish`` skips ``attach_trace_to_result`` (legacy paths).
    _omit_invariant_attach: bool = field(default=False, repr=False)

    # ------------------------------------------------------------------ turn
    @classmethod
    @contextmanager
    def turn(
        cls,
        *,
        user_id: str,
        text: str,
        transaction_id: str = "",
        session_state: Any = None,
    ) -> Iterator["StageRunner"]:
        """Begin/end decision + invariant turn traces (and compact turn logs)."""
        from core.tracing.decision_trace import (
            TurnTrace,
            finalize_turn_trace as finalize_decision_turn_trace,
            is_decision_trace_enabled,
            is_request_decision_trace_bound,
        )
        from core.tracing.invariant_trace import TurnInvariantTrace
        from core.tracing.server_log import compact_session_snapshot

        if not is_decision_trace_enabled():
            turn_logger.info(
                json.dumps(
                    {"turn": "INPUT", "user_id": user_id, "text": text},
                    ensure_ascii=True,
                )
            )

        TurnInvariantTrace.begin(user_id, text)
        if is_decision_trace_enabled():
            TurnTrace.begin(
                user_id=user_id,
                text=text,
                transaction_id=str(transaction_id or ""),
                force=True,
            )

        runner = cls(
            release_direct_decision_trace=not is_request_decision_trace_bound()
        )

        if not is_decision_trace_enabled():
            turn_logger.info(
                json.dumps(
                    {
                        "turn": "SESSION_READ",
                        "session": compact_session_snapshot(session_state),
                    },
                    ensure_ascii=True,
                    default=str,
                )
            )

        try:
            yield runner
        finally:
            if runner.release_direct_decision_trace:
                finalize_decision_turn_trace()

    def finish(self, result: Dict[str, Any], **spine_kwargs: Any) -> Dict[str, Any]:
        """Attach traces (when appropriate) and wrap with the execution spine."""
        from core.orchestration.orchestrator import _return_with_execution_spine

        if not self._omit_invariant_attach and isinstance(result, dict):
            attach_trace_to_result(result)
        wrapped = _return_with_execution_spine(result, **spine_kwargs)
        return self._attach_direct_decision_trace(wrapped)

    def finish_without_invariant_attach(
        self, result: Dict[str, Any], **spine_kwargs: Any
    ) -> Dict[str, Any]:
        """Legacy: planning-only / unsupported-route paths omitted invariant attach."""
        self._omit_invariant_attach = True
        return self.finish(result, **spine_kwargs)

    def _attach_direct_decision_trace(self, result: Dict[str, Any]) -> Dict[str, Any]:
        if self.release_direct_decision_trace and isinstance(result, dict):
            from core.tracing.decision_trace import attach_decision_trace_to_result

            attach_decision_trace_to_result(result)
            self.release_direct_decision_trace = False
        return result

    # --------------------------------------------------------------- generic
    def run(
        self,
        stage: str,
        fn: Callable[[], T],
        *,
        decision_timing: Optional[str] = None,
        record_timing: bool = False,
        when: str = "after",
        check_fn: Optional[Callable[[], Sequence[InvariantResult]]] = None,
        allowed_mutations: Optional[List[str]] = None,
        forbidden_mutations: Optional[List[str]] = None,
        state_snapshot: Optional[Dict[str, Any]] = None,
        skipped: bool = False,
        emit: Optional[Callable[[], None]] = None,
        after: Optional[Callable[[Any], None]] = None,
    ) -> T:
        """Run ``fn`` with optional invariant emission and decision timing."""
        if when == "before" and check_fn is not None:
            self._emit_invariant(
                stage,
                check_fn,
                allowed_mutations=allowed_mutations,
                forbidden_mutations=forbidden_mutations,
                state_snapshot=state_snapshot,
                skipped=skipped,
            )
            if emit is not None:
                self._safe_emit(stage, emit)

        started = time.perf_counter() if decision_timing else None
        result: Any = None
        exc: Optional[BaseException] = None
        try:
            result = fn()
            return result
        except BaseException as raised:
            exc = raised
            raise
        finally:
            if when == "after":
                if after is not None and exc is None:
                    try:
                        after(result)
                    except Exception as after_exc:  # pragma: no cover
                        logger.debug("Stage %s after() failed: %s", stage, after_exc)
                elif check_fn is not None:
                    self._emit_invariant(
                        stage,
                        check_fn,
                        allowed_mutations=allowed_mutations,
                        forbidden_mutations=forbidden_mutations,
                        state_snapshot=state_snapshot,
                        skipped=skipped,
                    )
                    if emit is not None:
                        self._safe_emit(stage, emit)

            if decision_timing is not None and started is not None and record_timing:
                if exc is None:
                    _record_decision_timing(decision_timing, started)

    def _emit_invariant(
        self,
        stage: str,
        check_fn: Callable[[], Sequence[InvariantResult]],
        *,
        allowed_mutations: Optional[List[str]] = None,
        forbidden_mutations: Optional[List[str]] = None,
        state_snapshot: Optional[Dict[str, Any]] = None,
        skipped: bool = False,
        owner: Optional[str] = None,
    ) -> None:
        trace_stage(
            stage,
            check_fn,
            allowed_mutations=allowed_mutations,
            forbidden_mutations=forbidden_mutations,
            state_snapshot=state_snapshot,
            skipped=skipped,
            owner=owner,
        )

    @staticmethod
    def _safe_emit(stage: str, emit: Callable[[], None]) -> None:
        try:
            emit()
        except Exception as emit_exc:  # pragma: no cover
            logger.debug("Stage %s emit failed: %s", stage, emit_exc)

    # ---------------------------------------------------- specialized stages
    def session_load(
        self,
        fn: Callable[[], T],
        *,
        session_state: Optional[Dict[str, Any]],
        user_id: str,
    ) -> T:
        """Invariant check before body (payment asserts run after the check)."""
        return self.run(
            "session_load",
            fn,
            when="before",
            check_fn=lambda: check_session_load(
                session_state=session_state, user_id=user_id
            ),
            allowed_mutations=[],
            forbidden_mutations=["session.slots", "session.intent"],
            state_snapshot={
                "found": session_state is not None,
                "status": session_state.get("status") if session_state else None,
                "intent": (
                    session_state.get("intent_name") or session_state.get("intent")
                    if session_state
                    else None
                ),
                "slot_keys": sorted((session_state or {}).get("slots", {}).keys()),
            },
        )

    def pagination(
        self,
        fn: Callable[[], T],
        *,
        session_state: Optional[Dict[str, Any]],
    ) -> T:
        def _after(result: Any) -> None:
            if result is None:
                self._emit_invariant(
                    "pagination",
                    lambda: check_pagination(handled=False),
                    skipped=True,
                    forbidden_mutations=["booking.slots", "availability_fingerprint"],
                )
                return
            fp_before = (session_state or {}).get("availability_fingerprint")
            self._emit_invariant(
                "pagination",
                lambda: check_pagination(
                    handled=True,
                    fingerprint_before=fp_before,
                    fingerprint_after=fp_before,
                    search_executed=False,
                ),
                allowed_mutations=[
                    "availability_presentation",
                    "presented_availability",
                ],
                forbidden_mutations=[
                    "booking.slots",
                    "availability_fingerprint",
                    "last_execution_result",
                ],
                state_snapshot={
                    "page_index": (result.get("availability_pagination") or {}).get(
                        "page_index"
                    ),
                },
            )

        return self.run("pagination", fn, after=_after)

    def planning(self, fn: Callable[[], T]) -> T:
        """Planning has no invariant stage at the engine boundary."""
        return fn()

    def rendering(self, fn: Callable[[], T]) -> T:
        """Rendering has no engine-level invariant stage."""
        return fn()

    @contextmanager
    def execution_timer(self) -> Iterator["_ExecutionTimer"]:
        """Time the route→run→post-process→render window as ``execution``."""
        timer = _ExecutionTimer()
        try:
            yield timer
        finally:
            if timer.should_record and timer.started is not None:
                _record_decision_timing("execution", timer.started)

    def tool_execution_skipped(self, *, plan_action: Any) -> None:
        self._emit_invariant(
            "tool_execution",
            lambda: check_tool_execution(
                plan_action=plan_action,
                execution_result=None,
                can_execute=False,
            ),
            skipped=not plan_action,
            state_snapshot={"plan_action": plan_action, "can_execute": False},
        )

    def tool_execution_missing_client(self, *, plan_action: Any) -> None:
        self._emit_invariant(
            "tool_execution",
            lambda: check_tool_execution(
                plan_action=plan_action,
                execution_result=None,
                can_execute=False,
            ),
            state_snapshot={
                "plan_action": plan_action,
                "reason": "missing_execution_client",
            },
        )

    def tool_execution_failed(self, *, plan_action: Any, error: str) -> None:
        self._emit_invariant(
            "tool_execution",
            lambda: check_tool_execution(
                plan_action=plan_action,
                execution_result=None,
                can_execute=True,
            ),
            state_snapshot={"error": error},
        )

    def tool_execution_executed(
        self, *, plan_action: Any, execution_result: Dict[str, Any]
    ) -> None:
        self._emit_invariant(
            "tool_execution",
            lambda: check_tool_execution(
                plan_action=plan_action,
                execution_result=execution_result,
                can_execute=True,
            ),
            state_snapshot={
                "plan_action": plan_action,
                "execution_status": execution_result.get("status"),
                "execution_type": execution_result.get("type"),
            },
        )

    def business_facts(
        self,
        fn: Callable[[], T],
        *,
        intent_name: str,
        facts_snapshot: Callable[[T], Dict[str, Any]],
        emit: Optional[Callable[[T], None]] = None,
    ) -> T:
        """Time + invariant + optional decision emit around derived facts."""

        def _after(result: T) -> None:
            snap = facts_snapshot(result)

            def _do_emit() -> None:
                if emit is not None:
                    emit(result)

            self._emit_invariant(
                "business_facts",
                lambda: check_business_facts(facts=result, intent_name=intent_name),
                state_snapshot=snap,
            )
            self._safe_emit("business_facts", _do_emit)

        return self.run(
            "business_facts",
            fn,
            decision_timing="business_facts",
            record_timing=True,
            after=_after,
        )

    def planner(self, *, plan: Dict[str, Any]) -> None:
        self._emit_invariant(
            "planner",
            lambda: check_planner(plan=plan),
            allowed_mutations=["plan.status", "plan.stage", "plan.action"],
            forbidden_mutations=["plan.text", "plan.ui_actions", "plan.ui_hint"],
            state_snapshot={
                "status": plan.get("status"),
                "stage": plan.get("stage"),
                "action": plan.get("action"),
                "missing_slots": plan.get("missing_slots"),
            },
        )

    def fingerprint(
        self,
        *,
        stored_fingerprint: Any,
        current_fingerprint: Any,
        availability_resolved: Any,
    ) -> None:
        self._emit_invariant(
            "fingerprint",
            lambda: check_fingerprint(
                stored_fingerprint=stored_fingerprint,
                current_fingerprint=current_fingerprint,
                availability_ready=availability_resolved,
            ),
            state_snapshot={
                "stored_fingerprint": stored_fingerprint,
                "current_fingerprint": current_fingerprint,
                "availability_resolved": availability_resolved,
            },
        )

    def save_session(self, *, new_session_state: Dict[str, Any], user_id: str) -> None:
        self._emit_invariant(
            "save_session",
            lambda: check_save_session(saved=True, user_id=user_id),
            state_snapshot={
                "intent": new_session_state.get("intent_name")
                or new_session_state.get("intent"),
                "status": new_session_state.get("status"),
            },
        )

    def reload_session(
        self,
        *,
        saved_state: Dict[str, Any],
        reloaded_state: Any,
        user_id: str,
    ) -> None:
        self._emit_invariant(
            "reload_session",
            lambda: check_reload_session(
                saved_state=saved_state,
                reloaded_state=reloaded_state,
                user_id=user_id,
            ),
            state_snapshot={"reloaded": reloaded_state is not None},
        )


@dataclass
class _ExecutionTimer:
    started: float = field(default_factory=time.perf_counter)
    should_record: bool = False

    def mark_recorded(self) -> None:
        """Record timing on exit (successful executed path only)."""
        self.should_record = True
