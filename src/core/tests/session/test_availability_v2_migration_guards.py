"""Guards: availability flat mirrors are no longer hydrated or dual-written."""

from __future__ import annotations

from core.session.invalidation import InvalidationTrigger, apply_invalidation
from core.planning.booking_revision import BookingRevision
from core.session.session_projector import SessionProjectorV2
from core.session.session_schema_v2 import (
    hydrate_v1_compat_shims,
    normalize_session_to_v2,
    prepare_session_for_persist,
)
from core.workflows.availability.presentation import (
    availability_fingerprint_from_session,
    availability_pagination_from_session,
    clear_availability_artifacts,
    presented_availability_from_session,
)

_FLAT_KEYS = (
    "availability_fingerprint",
    "last_execution_result",
    "presented_availability",
    "availability_presentation",
)


def test_hydrate_v1_compat_shims_does_not_create_availability_quartet():
    v2 = normalize_session_to_v2(
        {
            "availability": {
                "fingerprint": "fp-1",
                "cache": {
                    "search_result": {
                        "type": "availability",
                        "status": "success",
                        "slots": [{"starts_at": "2026-07-10T10:00:00Z"}],
                    }
                },
                "presentation": {
                    "presented": {"slots": [{"starts_at": "2026-07-10T10:00:00Z"}]},
                    "page_index": 1,
                    "page_size": 3,
                },
            }
        }
    )
    working = hydrate_v1_compat_shims(v2)
    for key in _FLAT_KEYS:
        assert key not in working
    assert availability_fingerprint_from_session(working) == "fp-1"
    assert presented_availability_from_session(working) is not None


def test_projector_does_not_emit_availability_quartet():
    outcome = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {"service_id": "premium haircut", "date": "2026-07-10"},
        "missing_slots": ["time"],
        "facts": {},
    }
    workflow = {
        "availability_fingerprint": "fp-proj",
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "slots": [{"starts_at": "2026-07-10T10:00:00Z"}],
            "search_date": "2026-07-10",
        },
        "presented_availability": {
            "slots": [{"starts_at": "2026-07-10T10:00:00Z"}],
            "search_date": "2026-07-10",
        },
        "availability_presentation": {"page_index": 1, "page_size": 3},
    }
    projected = SessionProjectorV2().project(
        outcome=outcome,
        outcome_status="NEEDS_CLARIFICATION",
        organization_id=1,
        merged_luma_response={"slots": outcome["slots"]},
        workflow_result=workflow,
        user_id="u-avail-mig",
    )
    assert projected is not None
    for key in _FLAT_KEYS:
        assert key not in projected
    assert availability_fingerprint_from_session(projected) == "fp-proj"
    assert (availability_pagination_from_session(projected) or {}).get("page_index") == 1

    pure = prepare_session_for_persist(projected)
    for key in _FLAT_KEYS:
        assert key not in pure
    assert pure["availability"]["fingerprint"] == "fp-proj"
    assert pure["availability"]["presentation"]["page_index"] == 1


def test_invalidation_clears_nested_availability_artifacts():
    session = {
        "slots": {"service_id": "haircut", "date": "2026-07-10", "time": "09:00"},
        "availability": {
            "fingerprint": "fp-old",
            "cache": {"search_result": {"type": "availability", "status": "success", "slots": []}},
            "presentation": {
                "presented": {"slots": []},
                "page_index": 2,
                "page_size": 3,
            },
        },
        "presented_availability": {"slots": []},
        "availability_fingerprint": "fp-old",
    }
    apply_invalidation(
        session,
        InvalidationTrigger.BOOKING_REVISION,
        revision=BookingRevision(date=True),
        reason="test_avail_clear",
    )
    assert availability_fingerprint_from_session(session) is None
    assert presented_availability_from_session(session) is None
    for key in _FLAT_KEYS:
        assert key not in session


def test_clear_availability_artifacts_removes_flat_and_nested():
    session = {
        "availability_fingerprint": "fp",
        "last_execution_result": {"type": "availability", "status": "success", "slots": []},
        "presented_availability": {"slots": []},
        "availability_presentation": {"page_index": 1},
        "availability": {
            "fingerprint": "fp",
            "cache": {"search_result": {"type": "availability", "status": "success", "slots": []}},
            "presentation": {"presented": {"slots": []}, "page_index": 1},
        },
    }
    clear_availability_artifacts(session)
    assert availability_fingerprint_from_session(session) is None
    for key in _FLAT_KEYS:
        assert key not in session