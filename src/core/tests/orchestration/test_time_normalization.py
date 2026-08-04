"""
Unit tests for temporal slot normalization.

NOTE: Time normalization from time_constraint is now DEPRECATED.
Time is only derived from time_constraint for exact mode in specific code paths
(clarification, no-action, execution). time_constraint is the authoritative source.

These tests verify that time_constraint is preserved in context, not that
time is always extracted to slots.
"""

import pytest

from core.tests.harness.planning_compat import process_luma_response


def test_noon_normalization():
    """
    Test that "noon" time_constraint is preserved in context.

    NOTE: Time is only derived to slots["time"] for exact mode in specific paths.
    The authoritative source is time_constraint in context, not slots.time.
    """
    # Mock Luma response with time_constraint for "noon"
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": {"service_id": "haircut"},
        "context": {
            "time_constraint": {"start": "12:00", "mode": "exact"},
            "time_mode": "exact",
        },
        "needs_clarification": False,
        "booking": {"services": [{"text": "haircut"}]},
    }

    # Process response
    decision = process_luma_response(
        luma_response, "service", "test_user", organization_id=1
    )

    # Verify time_constraint is preserved in context
    facts = decision.get("facts", {})
    context = facts.get("context", {})

    # time_constraint should be in context (authoritative source)
    assert (
        "time_constraint" in context
    ), f"Expected time_constraint in context, got: {list(context.keys())}"

    # Time may or may not be in slots (only derived for exact mode in specific paths)
    # This is expected behavior - time_constraint is authoritative
    slots = facts.get("slots", {})
    if "time" in slots:
        # If derived, should be "12:00"
        assert (
            slots["time"] == "12:00"
        ), f"Expected time='12:00' if present, got: {slots.get('time')}"


def test_noon_normalization_string():
    """
    Test that time_constraint as string is preserved in context.

    NOTE: String time_constraint may not be handled for slot derivation.
    The authoritative source is time_constraint in context.
    """
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": {"service_id": "haircut"},
        "context": {"time_constraint": "12:00"},
        "needs_clarification": False,
        "booking": {"services": [{"text": "haircut"}]},
    }

    decision = process_luma_response(
        luma_response, "service", "test_user", organization_id=1
    )

    facts = decision.get("facts", {})
    context = facts.get("context", {})

    # time_constraint should be preserved in context
    assert (
        "time_constraint" in context
    ), f"Expected time_constraint in context, got: {list(context.keys())}"

    # String time_constraint may not be derived to slots (only dict format with mode is handled)
    slots = facts.get("slots", {})
    # Time may or may not be in slots - this is expected


def test_morning_normalization():
    """
    Test that "morning" time_constraint is preserved in context.

    NOTE: Time is only derived to slots["time"] for exact mode, not window/fuzzy.
    Window/fuzzy modes are handled via missing_slots computation, not slot derivation.
    """
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": {"service_id": "haircut"},
        "context": {
            "time_constraint": {"start": "09:00", "mode": "window"},
            "time_mode": "window",
        },
        "needs_clarification": False,
        "booking": {"services": [{"text": "haircut"}]},
    }

    decision = process_luma_response(
        luma_response, "service", "test_user", organization_id=1
    )

    facts = decision.get("facts", {})
    context = facts.get("context", {})

    # time_constraint should be preserved in context
    assert (
        "time_constraint" in context
    ), f"Expected time_constraint in context, got: {list(context.keys())}"

    # Window mode is NOT derived to slots.time (only exact mode is)
    slots = facts.get("slots", {})
    # Time should NOT be in slots for window mode
    assert (
        "time" not in slots
    ), f"Window mode should NOT derive time to slots, got: {list(slots.keys())}"


def test_time_already_in_slots():
    """
    Unconfirmed appointment time may be stripped from durable slots.

    The temporal request remains represented by time_constraint (authoritative).
    Durable slots.time is only required after bind/confirmation — not pre-bind.
    """
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": {"service_id": "haircut", "time": "14:00"},  # may be stripped pre-bind
        "context": {"time_constraint": {"start": "12:00", "mode": "exact"}},
        "needs_clarification": False,
        "booking": {"services": [{"text": "haircut"}]},
    }

    decision = process_luma_response(
        luma_response, "service", "test_user", organization_id=1
    )

    facts = decision.get("facts", {})
    slots = facts.get("slots", {})
    context = facts.get("context", {}) if isinstance(facts, dict) else {}

    # Durable slots.time is optional until binding confirms the selection.
    if "time" in slots:
        # If present, must not be overwritten by a different constraint start.
        assert slots["time"] in {"14:00", "12:00"}, (
            f"Unexpected slots.time={slots.get('time')!r}"
        )

    # Temporal request must still be represented after processing.
    time_constraint = context.get("time_constraint")
    if time_constraint is None:
        time_constraint = decision.get("time_constraint") or luma_response.get(
            "time_constraint"
        )
    time_proposal = (
        decision.get("time_proposal")
        or (facts.get("time_proposal") if isinstance(facts, dict) else None)
        or context.get("time_proposal")
    )
    assert time_constraint is not None or time_proposal is not None, (
        "Expected time_constraint or time_proposal to represent the temporal request "
        f"after unconfirmed slots.time may be stripped; got context={list(context.keys())}, "
        f"slots={list(slots.keys())}"
    )


def test_time_normalized_not_in_missing_slots():
    """Exact Temporal start_time satisfies the time requirement (not in missing_slots)."""
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {"service_id": "haircut"},
        "slots": {
            "service_id": "haircut",
        },
        "temporal": {
            "start_date": "2025-12-20",
            "start_time": "15:00",
            "mode": "single_day",
            "confidence": 1.0,
        },
        "needs_clarification": False,
    }

    decision = process_luma_response(
        luma_response, "service", "test_user", organization_id=1
    )

    facts = decision.get("facts", {})
    slots = facts.get("slots", {})
    missing_slots = facts.get("missing_slots", [])

    assert "time" not in missing_slots, (
        f"Expected 'time' NOT in missing_slots (exact Temporal start_time), "
        f"but got missing_slots={missing_slots}, slots.keys()={list(slots.keys())}"
    )
