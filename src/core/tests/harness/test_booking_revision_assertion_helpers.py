"""Deterministic checks for booking revision E2E assertion helpers."""

from unittest.mock import Mock

import pytest

from core.tests.e2e.booking._helpers import (
    _assert_authoritative_time_absent,
    _assert_authoritative_time_replaced,
    _assert_booking_created_with_exact_payload,
    _assert_revision_searched_once,
    _expected_search_catalog_item_id,
)


class _Conversation:
    turn = 5
    user_id = "chat-user"

    def __init__(self, session):
        self._session = session

    def session(self):
        return self._session


def test_absent_time_assertion_accepts_clean_revision_state():
    conv = _Conversation(
        {
            "confirmation_state": None,
            "planning": {
                "slots": {"service_id": "flexi haircut"},
                "proposals": {"date": {"start": "2026-07-06"}},
            },
            "facts": {},
            "temporal": {
                "start_date": "2026-07-06",
                "start_time": None,
                "start_time_expression": None,
            },
        }
    )

    _assert_authoritative_time_absent(conv, "10:00")


def test_absent_time_assertion_rejects_stale_compatibility_fact():
    conv = _Conversation(
        {
            "confirmation_state": None,
            "slots": {"service_id": "flexi haircut"},
            "facts": {"times": ["10:00"]},
            "temporal": {},
        }
    )

    with pytest.raises(AssertionError, match="facts.times"):
        _assert_authoritative_time_absent(conv, "10:00")


def test_replacement_and_exact_booking_payload_assertions():
    session = {
        "customer_id": 9001,
        "confirmation_state": "pending",
        "planning": {
            "slots": {
                "service_id": "flexi haircut",
                "date": "2026-07-06",
                "time": "11:00",
                "booking_id": "booking-1",
                "booking_code": "CODE-1",
            },
        },
        "facts": {
            "times": ["11:00"],
            "time_proposal": {"mode": "exact", "value": "11:00"},
        },
        "time_proposal": {"mode": "exact", "value": "11:00"},
        "temporal": {"start_time": "11:00", "start_time_expression": "11am"},
        "booking": {"booking_id": "booking-1", "booking_code": "CODE-1"},
    }
    conv = _Conversation(session)
    booking = Mock()
    booking.create_booking(
        customer_id=9001,
        item_id=1002,
        start_time="2026-07-06T11:00:00Z",
        end_time="2026-07-06T11:30:00Z",
    )

    _assert_authoritative_time_replaced(conv, "11:00", "10:00")
    _assert_booking_created_with_exact_payload(
        expected_item_id=1002,
        expected_service_id="flexi haircut",
        expected_date="2026-07-06",
        expected_time="11:00",
        abandoned_values=("2026-07-03", "10:00"),
    )(conv, booking)


def test_revision_search_helper_maps_sku_text_to_catalog_item_id():
    assert _expected_search_catalog_item_id("premium haircut") == 1001
    assert _expected_search_catalog_item_id("flexi haircut + prunning") == 1002
    assert _expected_search_catalog_item_id(1002) == 1002
