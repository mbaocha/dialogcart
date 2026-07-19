"""Declarative conversation DSL and BookingConversation HTTP harness."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Union
from unittest.mock import Mock

import pytest

from core.execution.clients.availability_client import AvailabilityClient
from core.session.session_manager import get_session
from core.planning.time_resolution import TIME_MATCH_EXACT, TIME_MATCH_MISMATCH
from core.tests.e2e.framework.trace_helpers import (
    augment_assertion_message,
    maybe_print_decision_trace,
    stash_decision_trace_from_body,
)

_UNSET = object()

_TIME_MATCH_ALIASES = {
    "EXACT": TIME_MATCH_EXACT,
    "TIME_MATCH_EXACT": TIME_MATCH_EXACT,
    "MISMATCH": TIME_MATCH_MISMATCH,
    "TIME_MATCH_MISMATCH": TIME_MATCH_MISMATCH,
}


@dataclass(frozen=True)
class Expect:
    """Optional assertions applied after a turn. Only set fields are checked.

    Pass ``None`` explicitly (e.g. ``confirmation=None``) to assert absence.
    Omit a field to skip that check.
    """

    planner: Any = _UNSET
    planner_status: Any = _UNSET
    response_status: Any = _UNSET
    status: Any = _UNSET
    stage: Any = _UNSET
    action: Any = _UNSET
    awaiting: Any = _UNSET
    confirmation: Any = _UNSET
    confirmation_state: Any = _UNSET
    intent: Any = _UNSET
    missing_slots: Any = _UNSET
    session_slots: Any = _UNSET
    outcome_slots: Any = _UNSET
    execution: Any = _UNSET
    execution_type: Any = _UNSET
    has_availability_slots: Any = _UNSET
    response_text_present: Any = _UNSET
    time_match: Any = _UNSET
    time_match_outcome: Any = _UNSET
    date_proposal: Any = _UNSET
    date_proposal_start: Any = _UNSET
    time_proposal: Any = _UNSET
    time_proposal_contains: Any = _UNSET
    slot_contains: Any = _UNSET
    slot_absent: Any = _UNSET
    availability_invalidated: Any = _UNSET

    def to_assert_turn_kwargs(self) -> Dict[str, Any]:
        """Map Expect fields onto BookingConversation.assert_turn kwargs."""
        checks: Dict[str, Any] = {}

        planner = self._first_set(self.planner_status, self.planner)
        if planner is not _UNSET:
            checks["planner_status"] = planner

        response_status = self._first_set(self.response_status, self.status)
        if response_status is not _UNSET:
            checks["response_status"] = response_status

        if self.stage is not _UNSET:
            checks["stage"] = self.stage
        if self.action is not _UNSET:
            checks["action"] = self.action
        if self.awaiting is not _UNSET:
            checks["awaiting"] = self.awaiting

        confirmation = self._first_set(self.confirmation, self.confirmation_state)
        if confirmation is not _UNSET:
            checks["confirmation"] = confirmation

        if self.intent is not _UNSET:
            checks["intent"] = self.intent
        if self.missing_slots is not _UNSET:
            checks["missing_slots"] = list(self.missing_slots or [])
        if self.session_slots is not _UNSET:
            checks["session_slots"] = dict(self.session_slots or {})
        if self.outcome_slots is not _UNSET:
            checks["outcome_slots"] = dict(self.outcome_slots or {})

        execution = self._first_set(self.execution_type, self.execution)
        if execution is not _UNSET:
            checks["execution_type"] = execution

        if self.has_availability_slots is not _UNSET:
            checks["has_availability_slots"] = self.has_availability_slots
        if self.response_text_present is not _UNSET:
            checks["response_text_present"] = self.response_text_present

        time_match = self._first_set(self.time_match_outcome, self.time_match)
        if time_match is not _UNSET:
            checks["time_match_outcome"] = _normalize_time_match(str(time_match))

        date_proposal = self._first_set(self.date_proposal_start, self.date_proposal)
        if date_proposal is not _UNSET:
            checks["date_proposal_start"] = date_proposal

        time_proposal = self._first_set(
            self.time_proposal_contains, self.time_proposal
        )
        if time_proposal is not _UNSET:
            checks["time_proposal_contains"] = time_proposal

        if self.slot_absent is not _UNSET:
            checks["slot_absent"] = list(self.slot_absent or [])
        if self.availability_invalidated is not _UNSET:
            checks["availability_invalidated"] = self.availability_invalidated

        return checks

    def apply_extra(self, conv: Any) -> None:
        """Assertions that assert_turn does not cover (slot_contains fragments)."""
        if self.slot_contains is not _UNSET and self.slot_contains:
            for key, fragment in self.slot_contains.items():
                conv.assert_slot_contains(key, fragment, in_session=True)

    @staticmethod
    def _first_set(*values: Any) -> Any:
        for value in values:
            if value is not _UNSET:
                return value
        return _UNSET


def _normalize_time_match(value: str) -> str:
    key = str(value).strip().upper()
    return _TIME_MATCH_ALIASES.get(key, value)


def expect_field_names() -> List[str]:
    return [f.name for f in fields(Expect)]


# Hooks receive (conv, booking_client, availability_client)
TurnHook = Callable[..., Any]


@dataclass
class Turn:
    """One user message and the expectations / hooks for that turn."""

    user: str
    expect: Optional[Expect] = None
    before: Optional[TurnHook] = None
    after: Optional[TurnHook] = None
    trace: Optional[str] = None

    def __init__(
        self,
        user: str,
        expect: Optional[Expect] = None,
        *,
        before: Optional[TurnHook] = None,
        after: Optional[TurnHook] = None,
        trace: Optional[str] = None,
        **expect_kwargs: Any,
    ) -> None:
        # Allow Turn("hi", Expect(...)) and Turn("hi", planner="READY", ...)
        if expect is None and expect_kwargs:
            expect = Expect(**expect_kwargs)
        elif expect is not None and expect_kwargs:
            raise TypeError(
                "Turn: pass either an Expect instance or keyword expectations, not both"
            )
        self.user = user
        self.expect = expect
        self.before = before
        self.after = after
        self.trace = trace


def coerce_turn(value: Union[Turn, tuple, list, dict]) -> Turn:
    """Normalize shorthand turn values used in Scenario(...)."""
    if isinstance(value, Turn):
        return value
    if isinstance(value, (tuple, list)):
        if len(value) == 1:
            return Turn(str(value[0]))
        if len(value) == 2:
            user, second = value
            if isinstance(second, Expect):
                return Turn(str(user), second)
            if isinstance(second, dict):
                return Turn(str(user), Expect(**second))
            raise TypeError(f"Unsupported turn shorthand: {value!r}")
        raise TypeError(f"Turn tuple must have 1 or 2 items, got {value!r}")
    if isinstance(value, dict):
        return Turn(**value)
    raise TypeError(f"Cannot coerce {type(value)!r} to Turn")


ScenarioHook = Callable[..., Any]


@dataclass
class Scenario:
    """Named conversation made of turns; executed by the scenario runner."""

    name: str
    turns: List[Turn] = field(default_factory=list)
    fixture: str = "booking"
    tags: List[str] = field(default_factory=list)
    before: Optional[ScenarioHook] = None
    after: Optional[ScenarioHook] = None
    id: Optional[str] = None

    def __init__(
        self,
        name: str,
        *turns: Union[Turn, tuple, list, dict],
        fixture: str = "booking",
        tags: Optional[Sequence[str]] = None,
        before: Optional[ScenarioHook] = None,
        after: Optional[ScenarioHook] = None,
        id: Optional[str] = None,
    ) -> None:
        self.name = name
        self.turns = [coerce_turn(t) for t in turns]
        self.fixture = fixture
        self.tags = list(tags or [])
        self.before = before
        self.after = after
        self.id = id or _slugify(name)

    def pytest_id(self) -> str:
        return self.id


def _slugify(name: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "scenario"

os.environ.setdefault("CORE_EXECUTION_MODE", "test")

HAIRCUT_CATALOG = {
    "premium haircut": "haircut",
    "flexi haircut + prunning": "haircut",
}

FROZEN_TIME = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
ORG_ID = int(os.getenv("ORG_ID", "1"))
PREMIUM_SERVICE = "premium haircut"
FLEXI_SERVICE = "flexi haircut + prunning"


def _resolve_search_date(raw: Optional[str]) -> str:
    if raw and isinstance(raw, str):
        cleaned = raw.split("T")[0].strip()
        if len(cleaned) == 10 and cleaned[4] == "-" and cleaned[7] == "-":
            return cleaned
    return (FROZEN_TIME + timedelta(days=2)).strftime("%Y-%m-%d")


def create_slot_availability_client(
    *,
    start_hours: tuple[int, ...] = (10, 11),
    frozen_time: datetime = FROZEN_TIME,
) -> Mock:
    """Availability mock returning only the given UTC start hours on the search date."""
    mock_client = Mock(spec=AvailabilityClient)

    def get_service_availability(**kwargs):
        date = _resolve_search_date(kwargs.get("date"))
        return {
            "slots": [
                {
                    "start": f"{date}T{hour:02d}:00:00Z",
                    "end": f"{date}T{hour:02d}:30:00Z",
                    "available": True,
                }
                for hour in start_hours
            ]
        }

    mock_client.get_service_availability.side_effect = get_service_availability
    return mock_client


def create_multi_slot_availability_client(
    frozen_time: datetime = FROZEN_TIME,
) -> Mock:
    """Availability with 10:00 and 11:00 slots only (12:00 unavailable)."""
    return create_slot_availability_client(start_hours=(10, 11), frozen_time=frozen_time)


def create_empty_availability_client(
    frozen_time: datetime = FROZEN_TIME,
) -> Mock:
    """Availability mock that always returns no slots."""
    mock_client = Mock(spec=AvailabilityClient)

    def get_service_availability(**kwargs):
        return {"slots": []}

    mock_client.get_service_availability.side_effect = get_service_availability
    return mock_client


def create_paginated_availability_client(
    frozen_time: datetime = FROZEN_TIME,
    *,
    slot_hours: tuple[int, ...] = tuple(range(9, 18)),
) -> Mock:
    """Availability with more slots than the default presentation cap (6)."""
    mock_client = Mock(spec=AvailabilityClient)

    def get_service_availability(**kwargs):
        date = _resolve_search_date(kwargs.get("date"))
        return {
            "slots": [
                {
                    "start": f"{date}T{hour:02d}:00:00Z",
                    "end": f"{date}T{hour:02d}:30:00Z",
                    "available": True,
                }
                for hour in slot_hours
            ]
        }

    mock_client.get_service_availability.side_effect = get_service_availability
    return mock_client


def _normalize_slot_start(slot: Dict[str, Any]) -> Optional[str]:
    if not isinstance(slot, dict):
        return None
    start = slot.get("starts_at") or slot.get("start") or slot.get("start_time")
    return str(start) if start else None


def extract_presented_times(
    body: Dict[str, Any],
    session: Optional[Dict[str, Any]],
) -> List[str]:
    """Return normalized start-time keys for the availability page shown to the user."""
    sess = session or {}
    presented = sess.get("presented_availability")
    if isinstance(presented, dict):
        slots = presented.get("slots") or []
        starts = [_normalize_slot_start(s) for s in slots]
        starts = [s for s in starts if s]
        if starts:
            return starts
        times = presented.get("times")
        if isinstance(times, list) and times:
            return [str(t) for t in times]

    execution = _execution_view(body, sess)
    slots = execution.get("slots") or []
    starts = [_normalize_slot_start(s) for s in slots]
    return [s for s in starts if s]


_NO_MORE_TIMES_PHRASES = (
    "no more",
    "no additional",
    "no other",
    "no further",
    "that's all",
    "thats all",
    "all available",
)


def _response_text(body: Dict[str, Any]) -> str:
    outcome = body.get("outcome") or {}
    return str(outcome.get("text") or body.get("text") or "")


def _response_indicates_no_more_times(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _NO_MORE_TIMES_PHRASES)


def assert_different_availability_page(
    first_page: List[str],
    second_page: List[str],
    *,
    response_text: str = "",
    turn: int = 0,
) -> None:
    """
    Pagination contract: never repeat the same page; never repeat prior slots.

    When no further slots exist, the response must say so explicitly.
    """
    if first_page == second_page:
        pytest.fail(
            f"turn {turn}: 'show more' repeated the same availability page "
            f"({first_page!r}); expected a different page or an explicit "
            f"'no more times' indication"
        )

    overlap = set(first_page) & set(second_page)
    if overlap:
        pytest.fail(
            f"turn {turn}: 'show more' repeated previously shown slots: "
            f"{sorted(overlap)!r}"
        )

    if not second_page:
        assert _response_indicates_no_more_times(response_text), (
            f"turn {turn}: no additional slots returned but response did not "
            f"explicitly indicate there are no more available times: "
            f"{response_text!r}"
        )


def assert_no_booking_execution(
    conv: "BookingConversation",
    booking_client: Mock,
) -> None:
    assert not booking_client.create_booking.called, (
        f"turn {conv.turn}: booking should not have been created"
    )
    conv.assert_confirmation(None)
    execution = _execution_view(conv.last_body, conv.session())
    assert execution.get("type") != "booking", (
        f"turn {conv.turn}: unexpected booking execution"
    )


_EXECUTION_OUTCOME_STATUSES = frozenset(
    {"success", "succeeded", "failed", "partial", "EXECUTED"}
)


def _planner_fields_from_decision_trace(
    body: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Extract planner status/stage/action from decision_trace when HTTP omits plan."""
    if not isinstance(body, dict):
        return {}
    trace = body.get("decision_trace")
    if not isinstance(trace, dict):
        return {}

    # Summary projection embeds planner fields directly (no forensic records).
    if trace.get("view") == "summary" or "planner_status" in trace:
        fields: Dict[str, Any] = {}
        if trace.get("planner_status") is not None:
            fields["status"] = trace.get("planner_status")
        if "execution_stage" in trace:
            fields["stage"] = trace.get("execution_stage")
        if "action" in trace:
            fields["action"] = trace.get("action")
        if trace.get("awaiting") is not None:
            fields["awaiting"] = trace.get("awaiting")
        if trace.get("time_match_outcome") is not None:
            fields["time_match_outcome"] = trace.get("time_match_outcome")
        return fields

    try:
        from core.tracing.views import _find_record
    except ImportError:
        return {}

    fields = {}
    post_exec = _find_record(trace, "evidence.planning.post_execution")
    if post_exec:
        facts = post_exec.get("facts") if isinstance(post_exec.get("facts"), dict) else {}
        if facts.get("status") is not None:
            fields["status"] = facts.get("status")
        if "stage" in facts:
            fields["stage"] = facts.get("stage")
        if "action" in facts:
            fields["action"] = facts.get("action")
        if facts.get("awaiting") is not None:
            fields["awaiting"] = facts.get("awaiting")
        if facts.get("time_match_outcome") is not None:
            fields["time_match_outcome"] = facts.get("time_match_outcome")

    status_rec = _find_record(trace, "decision.planner.status")
    if status_rec and status_rec.get("winner") is not None and "status" not in fields:
        fields["status"] = status_rec.get("winner")
    stage_rec = _find_record(trace, "decision.planner.select_stage")
    if stage_rec and "winner" in stage_rec and "stage" not in fields:
        fields["stage"] = stage_rec.get("winner")
    action_rec = _find_record(trace, "decision.planner.select_action")
    if action_rec and "winner" in action_rec and "action" not in fields:
        fields["action"] = action_rec.get("winner")
    if post_exec and "awaiting" not in fields:
        facts = post_exec.get("facts") if isinstance(post_exec.get("facts"), dict) else {}
        if facts.get("awaiting") is not None:
            fields["awaiting"] = facts.get("awaiting")
    return fields


def _coerce_planner_status(value: Any) -> Optional[str]:
    """Return value only when it is a planner status, not an execution outcome."""
    if value is None:
        return None
    text = str(value)
    if text in _EXECUTION_OUTCOME_STATUSES:
        return None
    return text


def _plan_view(outcome: Dict[str, Any], body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Canonical planner view for E2E assertions.

    Prefer the root decision ``body.plan`` over nested ``outcome.plan``.
    After execution, HTTP ``MessageResponse`` often omits root ``plan`` and the
    outcome is an execution artifact (``status=succeeded``). Planner status must
    never be taken from that execution outcome status — use decision_trace
    (``decision.planner.status``) when the plan object is missing.
    """
    nested = outcome.get("plan") if isinstance(outcome.get("plan"), dict) else {}
    root_plan = (body or {}).get("plan") if isinstance((body or {}).get("plan"), dict) else {}
    # Root decision plan wins over nested execution stub.
    merged = {**nested, **root_plan}
    trace_fields = _planner_fields_from_decision_trace(body)

    status = _coerce_planner_status(trace_fields.get("status"))
    if status is None:
        status = _coerce_planner_status(merged.get("status"))
    if status is None:
        # Non-executed turns copy planner status onto outcome.status.
        status = _coerce_planner_status(outcome.get("status"))

    stage = trace_fields.get("stage")
    if stage is None:
        stage = merged.get("stage")
    if stage is None:
        stage = outcome.get("stage")

    if "action" in trace_fields:
        action = trace_fields.get("action")
    elif "action" in merged:
        action = merged.get("action")
    else:
        # Execution artifacts expose the executed action; that is fine for asserts.
        action = outcome.get("action")

    return {
        "status": status,
        "stage": stage,
        "action": action,
        "awaiting": trace_fields.get("awaiting")
        or outcome.get("awaiting")
        or merged.get("awaiting"),
        "time_match_outcome": trace_fields.get("time_match_outcome")
        or merged.get("time_match_outcome")
        or outcome.get("time_match_outcome"),
    }


def _confirmation_state(session: Optional[Dict[str, Any]]) -> Optional[str]:
    if not session:
        return None
    if session.get("confirmation_state"):
        return session.get("confirmation_state")
    booking = session.get("booking")
    if isinstance(booking, dict):
        return booking.get("confirmation_state")
    return None


def _execution_view(body: Dict[str, Any], session: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    outcome = body.get("outcome") or {}
    if outcome.get("type"):
        return outcome
    last = (session or {}).get("last_execution_result")
    return last if isinstance(last, dict) else {}


class BookingConversation:
    """Thin wrapper around POST /api/message for readable multi-turn tests."""

    def __init__(self, api_client, user_id: str, organization_id: int = ORG_ID):
        self.api_client = api_client
        self.user_id = user_id
        self.organization_id = organization_id
        self.last_http = None
        self.last_body: Dict[str, Any] = {}
        self.turn = 0

    def send(self, text: str, *, trace: Optional[str] = None) -> Dict[str, Any]:
        self.turn += 1
        # MessageResponse omits root ``plan``; after execution ``outcome.status`` is
        # the tool result (``succeeded``). Always request decision_trace so
        # ``assert_planner_status`` can read ``decision.planner.status``.
        params = {"trace": trace or "summary"}
        self.last_http = self.api_client.post(
            "/api/message",
            params=params,
            json={
                "user_id": self.user_id,
                "text": text,
                "organization_id": self.organization_id,
                "domain": "service",
                "timezone": "UTC",
            },
        )
        self.last_body = self.last_http.json()
        stash_decision_trace_from_body(self.last_body)
        maybe_print_decision_trace(self.last_body)
        return self.last_body

    @property
    def outcome(self) -> Dict[str, Any]:
        return self.last_body.get("outcome") or {}

    @property
    def plan(self) -> Dict[str, Any]:
        return _plan_view(self.outcome, self.last_body)

    def session(self) -> Optional[Dict[str, Any]]:
        return get_session(self.organization_id, self.user_id)

    def _assert(self, condition: bool, message: str) -> None:
        if not condition:
            raise AssertionError(
                augment_assertion_message(message, body=self.last_body)
            )

    def assert_http_ok(self) -> None:
        self._assert(self.last_http is not None, "no request sent yet")
        self._assert(
            self.last_http is not None and self.last_http.status_code == 200,
            (
                f"turn {self.turn}: expected HTTP 200, got {self.last_http.status_code}: "
                f"{self.last_body}"
            ),
        )
        self._assert(
            self.last_body.get("success") is True,
            f"turn {self.turn}: expected success=True, got {self.last_body}",
        )

    def assert_status(self, response_status: str) -> None:
        actual = self.outcome.get("status")
        self._assert(
            actual == response_status,
            f"turn {self.turn}: response status expected {response_status!r}, got {actual!r}",
        )

    def assert_planner_status(self, planner_status: str) -> None:
        actual = self.plan.get("status")
        self._assert(
            actual == planner_status,
            f"turn {self.turn}: planner status expected {planner_status!r}, got {actual!r}",
        )

    def assert_stage(self, stage: str) -> None:
        actual = self.plan.get("stage")
        self._assert(
            actual == stage,
            f"turn {self.turn}: stage expected {stage!r}, got {actual!r}",
        )

    def assert_awaiting(self, awaiting: str) -> None:
        actual = self.plan.get("awaiting")
        self._assert(
            actual == awaiting,
            f"turn {self.turn}: awaiting expected {awaiting!r}, got {actual!r}",
        )

    def assert_action(self, action: Optional[str]) -> None:
        actual = self.plan.get("action")
        self._assert(
            actual == action,
            f"turn {self.turn}: action expected {action!r}, got {actual!r}",
        )

    def assert_slots(
        self,
        *,
        outcome: Optional[Dict[str, Any]] = None,
        session: Optional[Dict[str, Any]] = None,
    ) -> None:
        if outcome:
            actual = self.outcome.get("slots") or {}
            for key, value in outcome.items():
                self._assert(
                    actual.get(key) == value,
                    (
                        f"turn {self.turn}: outcome slot {key!r} expected {value!r}, "
                        f"got {actual.get(key)!r}"
                    ),
                )
        if session:
            sess = self.session() or {}
            actual = sess.get("slots") or {}
            for key, value in session.items():
                self._assert(
                    actual.get(key) == value,
                    (
                        f"turn {self.turn}: session slot {key!r} expected {value!r}, "
                        f"got {actual.get(key)!r}"
                    ),
                )

    def assert_slot_contains(self, key: str, fragment: str, *, in_session: bool = True) -> None:
        source = (self.session() or {}).get("slots", {}) if in_session else self.outcome.get("slots", {})
        value = str(source.get(key, ""))
        self._assert(
            fragment in value,
            f"turn {self.turn}: expected {key!r} to contain {fragment!r}, got {value!r}",
        )

    def assert_slot_absent(self, key: str) -> None:
        slots = (self.session() or {}).get("slots") or {}
        value = slots.get(key)
        self._assert(
            value in (None, ""),
            f"turn {self.turn}: expected session slot {key!r} to be absent, got {value!r}",
        )

    def assert_time_proposal(self, *, value_fragment: Optional[str] = None) -> None:
        sess = self.session() or {}
        proposal = sess.get("time_proposal")
        if not isinstance(proposal, dict):
            facts = sess.get("facts")
            if isinstance(facts, dict):
                proposal = facts.get("time_proposal")
        self._assert(
            isinstance(proposal, dict),
            f"turn {self.turn}: expected time_proposal in session, got {proposal!r}",
        )
        if value_fragment is not None:
            actual = str(proposal.get("value", ""))
            self._assert(
                value_fragment in actual,
                (
                    f"turn {self.turn}: time_proposal.value expected to contain "
                    f"{value_fragment!r}, got {actual!r}"
                ),
            )

    def assert_time_match_outcome(self, expected: str) -> None:
        actual = self.plan.get("time_match_outcome")
        if actual is None:
            actual = self.outcome.get("time_match_outcome")
        if actual is None:
            facts = self.outcome.get("facts")
            if isinstance(facts, dict):
                actual = facts.get("time_match_outcome")
        if actual is None:
            actual = (self.session() or {}).get("time_match_outcome")
        execution = _execution_view(self.last_body, self.session())
        if actual is None:
            resolution = execution.get("time_resolution")
            if isinstance(resolution, dict):
                actual = resolution.get("outcome")
        if actual is None:
            facts = self.outcome.get("facts")
            if isinstance(facts, dict):
                resolution = facts.get("time_resolution")
                if isinstance(resolution, dict):
                    actual = resolution.get("outcome")
        self._assert(
            actual == expected,
            f"turn {self.turn}: time_match_outcome expected {expected!r}, got {actual!r}",
        )

    def assert_response_text_present(self) -> None:
        text = self.last_body.get("text")
        self._assert(
            isinstance(text, str) and bool(text.strip()),
            f"turn {self.turn}: expected non-empty response text, got {text!r}",
        )

    def assert_availability_search_without_time_constraint(
        self, availability_client: Mock
    ) -> None:
        for call in availability_client.get_service_availability.call_args_list:
            kwargs = call.kwargs or {}
            extra = kwargs.get("extra_params") or {}
            self._assert(
                "time_constraint" not in extra,
                (
                    f"turn {self.turn}: availability search must not send "
                    f"time_constraint in extra_params, got {extra!r}"
                ),
            )

    def assert_date_proposal(self, start: str) -> None:
        sess = self.session() or {}
        proposal = sess.get("date_proposal")
        if not isinstance(proposal, dict):
            facts = sess.get("facts")
            if isinstance(facts, dict):
                proposal = facts.get("date_proposal")
        if not isinstance(proposal, dict):
            proposal = {}
        actual = str(proposal.get("start", "")).split("T")[0].split(" ")[0]
        self._assert(
            actual == start,
            f"turn {self.turn}: date_proposal.start expected {start!r}, got {actual!r}",
        )

    def assert_availability_invalidated(self) -> None:
        """Bound datetime and prior search artifacts should be cleared after revision."""
        sess = self.session() or {}
        self._assert(
            not sess.get("resolved_datetime_range"),
            (
                f"turn {self.turn}: expected resolved_datetime_range cleared, "
                f"got {sess.get('resolved_datetime_range')!r}"
            ),
        )
        self._assert(
            _confirmation_state(sess) is None,
            f"turn {self.turn}: expected confirmation cleared after revision",
        )

    def assert_missing_slots(self, expected: list) -> None:
        sess = self.session() or {}
        actual = sess.get("missing_slots") or self.outcome.get("missing_slots") or []
        self._assert(
            sorted(actual) == sorted(expected),
            f"turn {self.turn}: missing_slots expected {expected}, got {actual}",
        )

    def assert_confirmation(self, state: Optional[str]) -> None:
        actual = _confirmation_state(self.session())
        self._assert(
            actual == state,
            f"turn {self.turn}: confirmation_state expected {state!r}, got {actual!r}",
        )

    def assert_execution(
        self,
        *,
        result_type: Optional[str] = None,
        has_availability_slots: Optional[bool] = None,
    ) -> None:
        execution = _execution_view(self.last_body, self.session())
        if result_type is not None:
            self._assert(
                execution.get("type") == result_type,
                (
                    f"turn {self.turn}: execution type expected {result_type!r}, "
                    f"got {execution.get('type')!r}"
                ),
            )
        if has_availability_slots is True:
            slots = execution.get("slots") or []
            self._assert(
                len(slots) >= 1,
                f"turn {self.turn}: expected availability slots in execution result",
            )
        if has_availability_slots is False:
            slots = execution.get("slots") or []
            self._assert(
                len(slots) == 0,
                f"turn {self.turn}: expected no availability slots, got {len(slots)}",
            )

    def assert_intent(self, intent_name: str) -> None:
        sess = self.session() or {}
        actual = sess.get("intent_name") or self.outcome.get("intent_name")
        self._assert(
            actual == intent_name,
            f"turn {self.turn}: intent expected {intent_name!r}, got {actual!r}",
        )

    def assert_turn(self, **checks: Any) -> None:
        """Run standard per-turn assertions from keyword expectations."""
        self.assert_http_ok()
        if "response_status" in checks:
            self.assert_status(checks["response_status"])
        if "planner_status" in checks:
            self.assert_planner_status(checks["planner_status"])
        if "stage" in checks:
            self.assert_stage(checks["stage"])
        if "awaiting" in checks:
            self.assert_awaiting(checks["awaiting"])
        if "action" in checks:
            self.assert_action(checks["action"])
        if "outcome_slots" in checks:
            self.assert_slots(outcome=checks["outcome_slots"])
        if "session_slots" in checks:
            self.assert_slots(session=checks["session_slots"])
        if "confirmation" in checks:
            self.assert_confirmation(checks["confirmation"])
        if "missing_slots" in checks:
            self.assert_missing_slots(checks["missing_slots"])
        if "intent" in checks:
            self.assert_intent(checks["intent"])
        if checks.get("execution_type"):
            self.assert_execution(result_type=checks["execution_type"])
        if checks.get("has_availability_slots") is not None:
            self.assert_execution(has_availability_slots=checks["has_availability_slots"])
        if checks.get("date_proposal_start"):
            self.assert_date_proposal(checks["date_proposal_start"])
        if checks.get("slot_absent"):
            for key in checks["slot_absent"]:
                self.assert_slot_absent(key)
        if checks.get("time_match_outcome"):
            self.assert_time_match_outcome(checks["time_match_outcome"])
        if checks.get("time_proposal_contains"):
            self.assert_time_proposal(value_fragment=checks["time_proposal_contains"])
        if checks.get("response_text_present"):
            self.assert_response_text_present()
        if checks.get("availability_invalidated"):
            self.assert_availability_invalidated()


def _presentation_page_index(session: Optional[Dict[str, Any]]) -> int:
    presentation = (session or {}).get("availability_presentation") or {}
    return int(presentation.get("page_index") or 0)


def _response_pagination_page_index(body: Dict[str, Any]) -> Optional[int]:
    outcome = body.get("outcome") or {}
    pagination = (
        body.get("availability_pagination")
        or outcome.get("availability_pagination")
        or {}
    )
    if not isinstance(pagination, dict):
        return None
    page_index = pagination.get("page_index")
    return int(page_index) if page_index is not None else None


def _reach_july_9_availability(conv: BookingConversation) -> List[str]:
    """Book haircut, select premium, revise to July 9; return first page times."""
    conv.send("book haircut")
    conv.assert_turn(
        response_status="NEEDS_CLARIFICATION",
        intent="CREATE_APPOINTMENT",
    )
    conv.send("premium")
    conv.assert_turn(
        response_status="succeeded",
        planner_status="READY",
        stage="AVAILABILITY",
        action="SEARCH_AVAILABILITY",
        session_slots={"service_id": PREMIUM_SERVICE},
        execution_type="availability",
        has_availability_slots=True,
    )
    conv.send("actually July 9")
    conv.assert_turn(
        intent="CREATE_APPOINTMENT",
        response_status="succeeded",
        action="SEARCH_AVAILABILITY",
        execution_type="availability",
        has_availability_slots=True,
        date_proposal_start="2026-07-09",
    )
    first_page = extract_presented_times(conv.last_body, conv.session())
    assert first_page, f"turn {conv.turn}: expected first availability page"
    assert _presentation_page_index(conv.session()) == 0
    return first_page


