"""Tests for NLU turn understanding outcome."""

from nlu.stages.shared.turn_understanding import (
    UNDERSTOOD,
    UNRECOGNIZED_INPUT,
    derive_turn_understanding,
)


def test_premium_with_service_term_is_understood():
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "service_term": "premium",
        "temporal": {"mode": "none"},
    }
    ctx = {"last_intent": "CREATE_APPOINTMENT"}
    assert derive_turn_understanding(slm, ctx) == UNDERSTOOD


def test_time_only_is_understood():
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "facts": {"dates": [], "times": ["09:00"], "date_time_pairs": []},
        "temporal": {"mode": "none", "start_time": "09:00"},
    }
    ctx = {"last_intent": "CREATE_APPOINTMENT"}
    assert derive_turn_understanding(slm, ctx) == UNDERSTOOD


def test_in_flow_gibberish_is_unrecognized():
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "service_term": None,
        "temporal": {"mode": "none"},
    }
    ctx = {"last_intent": "CREATE_APPOINTMENT"}
    assert derive_turn_understanding(slm, ctx) == UNRECOGNIZED_INPUT


def test_entity_extraction_failure_overrides_independent_booking_id_evidence():
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "facts": {"booking_id": "aa1239", "registration_number": None},
        "_entity_extraction_failed": True,
    }
    ctx = {
        "last_intent": "CREATE_APPOINTMENT",
        "missing_slots": ["registration_number"],
    }

    assert derive_turn_understanding(slm, ctx) == UNRECOGNIZED_INPUT


def test_sticky_resolved_service_id_alone_is_not_understood():
    """Session reuse of resolved_service_id must not imply the utterance was understood."""
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": "premium-haircut",
            "booking_id": None,
        },
        "service_term": None,
        "temporal": {"mode": "none"},
    }
    ctx = {
        "last_intent": "CREATE_APPOINTMENT",
        "resolved_service_id": "premium-haircut",
    }
    assert derive_turn_understanding(slm, ctx) == UNRECOGNIZED_INPUT


def test_new_service_id_without_term_is_understood():
    """AVAILABILITY-style extraction into facts.service_id (no sticky match) is evidence."""
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": "flexi-haircut",
            "booking_id": None,
        },
        "service_term": None,
        "temporal": {"mode": "none"},
    }
    ctx = {
        "last_intent": "CREATE_APPOINTMENT",
        "resolved_service_id": "premium-haircut",
    }
    assert derive_turn_understanding(slm, ctx) == UNDERSTOOD


def test_cold_start_booking_verb_empty_slots_is_understood():
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "temporal": {"mode": "none"},
    }
    assert derive_turn_understanding(slm, None) == UNDERSTOOD


def test_unknown_without_evidence_is_unrecognized():
    slm = {
        "intent": "UNKNOWN",
        "facts": {"dates": [], "times": [], "date_time_pairs": []},
        "temporal": {"mode": "none"},
    }
    assert derive_turn_understanding(slm, None) == UNRECOGNIZED_INPUT


def test_off_topic_is_understood():
    """OFF_TOPIC is a coherent act — UNDERSTOOD, not UNRECOGNIZED_INPUT."""
    slm = {
        "intent": "OFF_TOPIC",
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "search_query": None,
        "temporal": {"mode": "none"},
    }
    assert derive_turn_understanding(slm, None) == UNDERSTOOD


def test_off_topic_mid_booking_is_understood():
    slm = {
        "intent": "OFF_TOPIC",
        "facts": {"dates": [], "times": [], "date_time_pairs": []},
        "temporal": {"mode": "none"},
    }
    ctx = {"last_intent": "CREATE_APPOINTMENT", "active_booking_intent": "CREATE_APPOINTMENT"}
    assert derive_turn_understanding(slm, ctx) == UNDERSTOOD


def test_general_inquiry_is_understood():
    slm = {
        "intent": "GENERAL_INQUIRY",
        "facts": {"dates": [], "times": [], "date_time_pairs": []},
        "search_query": "available services",
        "temporal": {"mode": "none"},
    }
    assert derive_turn_understanding(slm, None) == UNDERSTOOD


def test_catalog_candidate_dump_without_term_is_not_evidence():
    """Null service_term + full-catalog candidates must stay UNRECOGNIZED."""
    slm = {
        "intent": "UNKNOWN",
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "service_term": None,
        "service_candidates": ["premium haircut", "flexi haircut + prunning"],
        "temporal": {"mode": "none", "confidence": 0.0},
    }
    assert derive_turn_understanding(slm, None) == UNRECOGNIZED_INPUT


def test_confirm_action_is_understood():
    slm = {
        "intent": "CONFIRM_ACTION",
        "facts": {"dates": [], "times": [], "date_time_pairs": []},
    }
    ctx = {"last_intent": "CREATE_APPOINTMENT"}
    assert derive_turn_understanding(slm, ctx) == UNDERSTOOD


def test_cancel_during_booking_is_understood():
    slm = {
        "intent": "CANCEL_BOOKING",
        "facts": {"dates": [], "times": [], "date_time_pairs": []},
        "temporal": {"mode": "none"},
    }
    ctx = {"last_intent": "CREATE_APPOINTMENT"}
    assert derive_turn_understanding(slm, ctx) == UNDERSTOOD
