"""
Unit tests for session service_id stickiness rules in merge_luma_with_session.

CONTRACT:
- service_id is PRESERVED when the user does not mention a service this turn
  (NLU returns service_id=null, service_candidates=[]).
- service_id is DROPPED when the user mentions an ambiguous service
  (NLU returns service_id=null, service_candidates=[...]).
- service_id is REPLACED when current-turn evidence shows an explicit service
  supply/correction (unambiguous service_id on a service-purpose turn).
- Invented facts.service_id on time-only / date-only / confirm turns must not
  overwrite a durable service.
"""

import pytest

from core.session.merge import merge_luma_with_session


def _session(service_id: str) -> dict:
    return {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "slots": {"service_id": service_id},
        "missing_slots": ["date", "time"],
    }


def _luma(service_id=None, service_candidates=None) -> dict:
    """Minimal Luma response for a CREATE_APPOINTMENT turn."""
    facts: dict = {}
    if service_candidates is not None:
        facts["service_candidates"] = service_candidates

    top_slots: dict = {}
    if service_id is not None:
        top_slots["service_id"] = service_id
        facts["service_id"] = service_id

    return {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "_effective_intent": "CREATE_APPOINTMENT",
        "slots": top_slots,
        "facts": facts,
    }


class TestServiceIdPreserved:
    """service_id is kept when user supplies no service this turn."""

    def test_no_service_mentioned_preserves_session(self):
        """Slot-fill turn with date/time only: existing service_id must survive."""
        session = _session("premium spa treatment")
        luma = _luma(service_id=None, service_candidates=[])

        merged = merge_luma_with_session(luma, session)

        assert merged["slots"].get("service_id") == "premium spa treatment"

    def test_no_service_mentioned_empty_candidates(self):
        """Explicit empty candidates list is treated same as absent candidates."""
        session = _session("haircut")
        luma = _luma(service_id=None, service_candidates=[])

        merged = merge_luma_with_session(luma, session)

        assert merged["slots"].get("service_id") == "haircut"


class TestServiceIdDropped:
    """service_id is cleared when user mentions an ambiguous new service."""

    def test_ambiguous_service_drops_stale_session_value(self):
        """If NLU returns service_candidates (ambiguous match), session service_id is removed."""
        session = _session("premium spa treatment")
        luma = _luma(
            service_id=None,
            service_candidates=["premium haircut", "flexi haircut + pruning"],
        )

        merged = merge_luma_with_session(luma, session)

        assert merged["slots"].get("service_id") is None or "service_id" not in merged["slots"]

    def test_ambiguous_service_service_id_missing_from_slots(self):
        """Dropped service_id must not appear in slots at all (not just null)."""
        session = _session("premium spa treatment")
        luma = _luma(
            service_id=None,
            service_candidates=["option a", "option b"],
        )

        merged = merge_luma_with_session(luma, session)

        slots = merged.get("slots") or {}
        assert "service_id" not in slots or slots["service_id"] is None


class TestServiceIdDroppedTopLevel:
    """service_candidates at the top level (actual NLU API format) must also clear session value.

    The NLU API puts service_candidates at the response root, not inside facts.
    merge.py must check both locations.
    """

    def test_top_level_candidates_drop_stale_service_id(self):
        """NLU API format: service_candidates at root → session service_id must be dropped."""
        session = _session("premium haircut")
        luma = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "_effective_intent": "CREATE_APPOINTMENT",
            "slots": {},
            "facts": {"service_id": None},  # NLU nulled it — ambiguous
            "service_candidates": ["premium haircut", "flexi haircut + pruning"],  # top-level
        }

        merged = merge_luma_with_session(luma, session)

        slots = merged.get("slots") or {}
        assert "service_id" not in slots or slots["service_id"] is None

    def test_top_level_empty_candidates_preserves_session(self):
        """Empty top-level candidates must not drop the session value."""
        session = _session("premium haircut")
        luma = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "_effective_intent": "CREATE_APPOINTMENT",
            "slots": {},
            "facts": {"service_id": None},
            "service_candidates": [],
        }

        merged = merge_luma_with_session(luma, session)

        assert merged["slots"].get("service_id") == "premium haircut"

    def test_raw_luma_response_null_service_id_with_candidates_drops_stale(self):
        """_raw_luma_response injects service_id=None into luma_slots — must not block drop."""
        session = _session("flexi haircut + prunning")
        candidates = ["premium haircut", "flexi haircut + prunning"]
        luma = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "_effective_intent": "CREATE_APPOINTMENT",
            "slots": {},
            "facts": {"service_id": None},
            "service_candidates": candidates,
            "_raw_luma_response": {
                "intent": {"name": "CREATE_APPOINTMENT"},
                "facts": {"service_id": None},
                "service_candidates": candidates,
            },
        }

        merged = merge_luma_with_session(luma, session)

        slots = merged.get("slots") or {}
        assert "service_id" not in slots or slots["service_id"] is None

    def test_raw_luma_response_null_service_id_empty_candidates_preserves(self):
        """Null service_id from _raw_luma_response with no candidates still preserves session."""
        session = _session("flexi haircut + prunning")
        luma = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "_effective_intent": "CREATE_APPOINTMENT",
            "slots": {},
            "facts": {"service_id": None},
            "service_candidates": [],
            "_raw_luma_response": {
                "intent": {"name": "CREATE_APPOINTMENT"},
                "facts": {"service_id": None},
            },
        }

        merged = merge_luma_with_session(luma, session)

        assert merged["slots"].get("service_id") == "flexi haircut + prunning"


class TestServiceIdDroppedFactsSlotsLeak:
    """session.facts.slots.service_id must not block candidate-based drop."""

    def test_facts_slots_stale_service_id_with_candidates_drops(self):
        """Production path: persisted facts.slots leaks service_id into luma_slots."""
        session = {
            "intent_name": "CREATE_APPOINTMENT",
            "status": "READY",
            "slots": {"service_id": "flexi haircut + prunning"},
            "facts": {"slots": {"service_id": "flexi haircut + prunning"}},
            "missing_slots": ["date", "time"],
        }
        candidates = ["premium haircut", "flexi haircut + prunning"]
        luma = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "_effective_intent": "CREATE_APPOINTMENT",
            "slots": {},
            "facts": {
                "service_id": None,
                "slots": {"service_id": "flexi haircut + prunning"},
            },
            "service_candidates": candidates,
            "_raw_luma_response": {
                "intent": {"name": "CREATE_APPOINTMENT"},
                "facts": {"service_id": None},
                "service_candidates": candidates,
            },
        }

        merged = merge_luma_with_session(luma, session)

        slots = merged.get("slots") or {}
        assert "service_id" not in slots or slots["service_id"] is None
        assert "service_id" in (merged.get("_intentionally_dropped_slots") or set())


class TestServiceIdReplaced:
    """service_id is overwritten when NLU resolves a different service unambiguously."""

    def test_unambiguous_new_service_replaces_session(self):
        """Service-purpose turn: non-null service_id replaces durable service."""
        session = _session("premium spa treatment")
        luma = _luma(service_id="haircut", service_candidates=[])

        merged = merge_luma_with_session(luma, session)

        assert merged["slots"].get("service_id") == "haircut"

    def test_invented_service_on_time_only_turn_preserves_durable(self):
        session = {
            "intent_name": "CREATE_APPOINTMENT",
            "status": "READY",
            "slots": {
                "service_id": "flexi haircut + prunning",
                "_catalog_item_id": 1002,
            },
            "missing_slots": ["time"],
        }
        luma = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "_effective_intent": "CREATE_APPOINTMENT",
            "_source_text": "9:30",
            "_current_turn_has_time": True,
            "_current_turn_has_date": False,
            "slots": {
                "service_id": "premium haircut",
                "_catalog_item_id": 1001,
                "time": "09:30",
            },
            "facts": {"service_id": "premium haircut", "times": ["09:30"]},
        }

        merged = merge_luma_with_session(luma, session)

        assert merged["slots"].get("service_id") == "flexi haircut + prunning"
        assert merged["slots"].get("_catalog_item_id") == 1002

    def test_same_service_repeated_is_stable(self):
        """Restating the same service keeps it unchanged."""
        session = _session("haircut")
        luma = _luma(service_id="haircut", service_candidates=[])

        merged = merge_luma_with_session(luma, session)

        assert merged["slots"].get("service_id") == "haircut"
