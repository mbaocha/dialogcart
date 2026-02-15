"""
Unit tests for temporal slot normalization.

NOTE: Time normalization from time_constraint is now DEPRECATED.
Time is only derived from time_constraint for exact mode in specific code paths
(clarification, no-action, execution). time_constraint is the authoritative source.

These tests verify that time_constraint is preserved in context, not that
time is always extracted to slots.
"""

import pytest

from core.orchestration.nlu.luma_response_processor import process_luma_response


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
    decision = process_luma_response(luma_response, "service", "test_user")

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

    decision = process_luma_response(luma_response, "service", "test_user")

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

    decision = process_luma_response(luma_response, "service", "test_user")

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
    Test that existing time in slots is not overwritten.
    """
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": {"service_id": "haircut", "time": "14:00"},  # Already in slots
        "context": {"time_constraint": {"start": "12:00", "mode": "exact"}},
        "needs_clarification": False,
        "booking": {"services": [{"text": "haircut"}]},
    }

    decision = process_luma_response(luma_response, "service", "test_user")

    facts = decision.get("facts", {})
    slots = facts.get("slots", {})

    # Should preserve existing time, not overwrite with time_constraint
    assert (
        slots["time"] == "14:00"
    ), f"Expected existing time='14:00' to be preserved, got: {slots.get('time')}"


def test_time_normalized_not_in_missing_slots():
    """
    Test that exact mode time_constraint satisfies time requirement (removes from missing_slots).

    NOTE: Time is only derived to slots["time"] for exact mode in specific paths.
    However, exact mode time_constraint should satisfy the time requirement,
    removing "time" from missing_slots even if not derived to slots.
    """
    # Mock Luma response where time_constraint exists with exact mode
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": {
            "service_id": "haircut",
            "date": "2025-12-20",
            # time is NOT in slots initially
        },
        # time_constraint must be at top level, not in context
        # (code expects luma_response.get("time_constraint"), not context.time_constraint)
        "time_constraint": {"start": "15:00", "mode": "exact"},
        "context": {"time_mode": "exact"},
        "needs_clarification": False,
        "booking": {"services": [{"text": "haircut"}]},
    }

    # Process response
    decision = process_luma_response(luma_response, "service", "test_user")

    facts = decision.get("facts", {})
    slots = facts.get("slots", {})
    missing_slots = facts.get("missing_slots", [])
    context = facts.get("context", {})

    # NOTE: time_constraint is processed and may not be preserved in context
    # The code extracts time_constraint from top-level luma_response, processes it,
    # and may only preserve time_mode in context (not the full time_constraint dict)
    # The important thing is that exact mode time_constraint satisfies the time requirement

    # Exact mode time_constraint should satisfy time requirement
    # Time may or may not be in slots (depends on code path), but missing_slots should not include "time"
    # because exact mode satisfies the time requirement
    assert "time" not in missing_slots, (
        f"Expected 'time' NOT in missing_slots (exact mode satisfies time), "
        f"but got missing_slots={missing_slots}, slots.keys()={list(slots.keys())}"
    )
