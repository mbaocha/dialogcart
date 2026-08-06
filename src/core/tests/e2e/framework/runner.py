"""Execute declarative Scenario objects against BookingConversation fixtures."""

from __future__ import annotations

import copy
import json
from typing import Any, Tuple

from core.tests.e2e.framework.conversation import BookingConversation, Expect, Scenario, Turn

ConversationBundle = Tuple[BookingConversation, Any, Any]


def hard_reload_persisted_session(conv: BookingConversation) -> None:
    """Discard request-scoped session aliases; reload pure Session V2 from the store.

    Uses only the public session lifecycle (``get_session`` / ``clear_session`` /
    ``save_session``). JSON round-trip breaks in-memory nested-dict aliasing so the
    next turn cannot rely on mutated request-scoped objects.
    """
    from core.session.session_manager import clear_session, get_session, save_session

    sess = get_session(conv.organization_id, conv.user_id)
    if sess is None:
        return
    pure = json.loads(json.dumps(sess, default=str))
    clear_session(conv.organization_id, conv.user_id)
    # Deep copy so callers cannot retain aliases into the document we persist.
    save_session(conv.organization_id, conv.user_id, copy.deepcopy(pure))


def run_scenario(
    scenario: Scenario,
    conv: BookingConversation,
    booking_client: Any = None,
    availability_client: Any = None,
) -> BookingConversation:
    """Drive every turn: before hooks → send → Expect → after hooks."""
    from core.tests.e2e.framework.conversation import attach_commit_customer_identity

    ctx = {
        "conv": conv,
        "booking_client": booking_client,
        "availability_client": availability_client,
        "scenario": scenario,
    }
    conv.availability_client = availability_client

    if getattr(scenario, "requires_customer_identity", False):
        attach_commit_customer_identity(conv)

    if scenario.before is not None:
        _call_hook(scenario.before, ctx)

    force_reload = bool(getattr(scenario, "force_session_reload", False))
    for index, turn in enumerate(scenario.turns):
        ctx["turn_index"] = index
        ctx["turn"] = turn
        if turn.before is not None:
            _call_hook(turn.before, ctx)

        conv._availability_calls_before_turn = len(
            availability_client.get_service_availability.call_args_list
        ) if availability_client is not None else 0
        conv.send(turn.user, trace=turn.trace)

        if turn.expect is not None:
            apply_expect(conv, turn.expect)

        if turn.after is not None:
            _call_hook(turn.after, ctx)

        # After normal API persist + hooks: drop aliases and reload for the next turn.
        if force_reload and index < len(scenario.turns) - 1:
            hard_reload_persisted_session(conv)

    if scenario.after is not None:
        _call_hook(scenario.after, ctx)

    return conv


def apply_expect(conv: BookingConversation, expect: Expect) -> None:
    checks = expect.to_assert_turn_kwargs()
    if checks:
        conv.assert_turn(**checks)
    else:
        conv.assert_http_ok()
    expect.apply_extra(conv)


def _call_hook(hook: Any, ctx: dict) -> None:
    try:
        return hook(
            ctx["conv"],
            ctx.get("booking_client"),
            ctx.get("availability_client"),
        )
    except TypeError:
        pass
    try:
        return hook(ctx["conv"], ctx.get("booking_client"))
    except TypeError:
        pass
    try:
        return hook(ctx["conv"])
    except TypeError:
        return hook()


def run_bundle(scenario: Scenario, bundle: ConversationBundle) -> BookingConversation:
    conv, booking_client, availability_client = bundle
    return run_scenario(
        scenario,
        conv,
        booking_client=booking_client,
        availability_client=availability_client,
    )
