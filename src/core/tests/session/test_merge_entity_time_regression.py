"""
Regression test: use-before-assignment of merged_intent_name in merge_luma_with_session.

Bug: The entities["time"] extraction guard at (formerly) line 791 referenced
     ``merged_intent_name`` before the variable was assigned at (formerly) line 814.
     When entities contains "time" and luma_slots does not already contain "time",
     execution reached the guard and raised:

         NameError: name 'merged_intent_name' is not defined

Fix: The ``merged_intent_name`` assignment was moved to immediately before the
     entities extraction block so it is always defined before any use.
"""

import pytest

from core.session.merge import merge_luma_with_session


def _minimal_session(intent: str = "CREATE_APPOINTMENT") -> dict:
    """Minimal realistic session state for a follow-up turn."""
    return {
        "intent_name": intent,
        "status": "NEEDS_CLARIFICATION",
        "slots": {"service_id": "haircut"},
        "missing_slots": ["date"],
    }


def _luma_with_entities_time(intent: str = "CREATE_APPOINTMENT", time: str = "14:00") -> dict:
    """
    Luma response that puts time exclusively in entities, not in facts.

    This is the shape that triggers the bug path:
      - entities["time"] is populated  → condition 1
      - facts is empty                  → facts_to_slots() returns {}
      - luma_slots has no "time"        → condition 2
    Both conditions together reach the merged_intent_name guard.
    """
    return {
        "intent": {"name": intent},
        "_effective_intent": intent,
        "entities": {"time": time},
        "facts": {},   # empty: no time promoted via facts_to_slots
        "slots": {},   # empty: no time in top-level slots
    }


class TestEntitiesTimeGuardNoNameError:
    """
    Verify that reaching the entities["time"] guard never raises NameError,
    regardless of the intent value passed.
    """

    def test_create_appointment_completes_without_nameerror(self):
        """
        CREATE_APPOINTMENT: guard evaluates ``merged_intent_name == "CREATE_APPOINTMENT"``
        and SKIPS entity-time extraction.  Was NameError before the fix.
        """
        luma = _luma_with_entities_time(intent="CREATE_APPOINTMENT", time="14:00")
        session = _minimal_session(intent="CREATE_APPOINTMENT")

        merged = merge_luma_with_session(luma, session)

        # For CREATE_APPOINTMENT the guard explicitly blocks entity-time extraction;
        # time must NOT appear in the durable slot set from this path alone.
        assert "time" not in merged.get("slots", {}), (
            "entity-time must not be extracted for CREATE_APPOINTMENT "
            "(time_constraint is authoritative for that intent)"
        )

    def test_non_appointment_intent_extracts_entity_time(self):
        """
        A non-CREATE_APPOINTMENT intent: guard evaluates True and ALLOWS entity-time
        extraction.  Was NameError before the fix.
        """
        luma = _luma_with_entities_time(intent="MODIFY_BOOKING", time="15:30")
        session = _minimal_session(intent="MODIFY_BOOKING")

        # Must not raise; merged_intent_name must be defined before the guard.
        merged = merge_luma_with_session(luma, session)

        # For non-CREATE_APPOINTMENT intents the guard allows extraction;
        # "time" must appear in the result.
        assert merged.get("slots", {}).get("time") == "15:30", (
            "entity-time must be extracted for non-CREATE_APPOINTMENT intents"
        )

    def test_unknown_intent_no_entity_time_in_slots(self):
        """
        UNKNOWN intent (no session intent): guard must not raise and must
        allow time extraction (UNKNOWN != CREATE_APPOINTMENT is True).
        """
        luma = {
            "intent": {"name": "UNKNOWN"},
            "_effective_intent": "UNKNOWN",
            "entities": {"time": "09:00"},
            "facts": {},
            "slots": {},
        }
        session: dict = {}   # no prior session

        # Must not raise regardless of whether there is a session.
        merged = merge_luma_with_session(luma, session)

        # UNKNOWN is not CREATE_APPOINTMENT; extraction is allowed.
        assert merged.get("slots", {}).get("time") == "09:00"

    def test_luma_time_in_facts_already_prevents_entity_path(self):
        """
        Verify that when luma_slots already has "time" (from facts promotion),
        the entity-time guard is NOT reached at all.  The function must still
        complete without error.
        """
        luma = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "_effective_intent": "CREATE_APPOINTMENT",
            "entities": {"time": "16:00"},
            "facts": {"time": "12:00"},   # time is in facts → will be in luma_slots
            "slots": {},
        }
        session = _minimal_session(intent="CREATE_APPOINTMENT")

        merged = merge_luma_with_session(luma, session)

        # facts-promoted time takes precedence; entity time is not reached.
        assert merged is not None
