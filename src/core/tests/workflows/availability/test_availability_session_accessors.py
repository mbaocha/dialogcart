"""Focused tests for canonical Session V2 availability accessors."""

from __future__ import annotations

from copy import deepcopy

from core.session.session_schema_v2 import (
    hydrate_v1_compat_shims,
    normalize_session_to_v2,
    prepare_session_for_persist,
)
from core.workflows.availability.presentation import (
    apply_availability_artifacts,
    availability_cache_from_session,
    availability_fingerprint_from_session,
    availability_pagination_from_session,
    clear_availability_artifacts,
    clear_availability_presentation,
    presented_availability_from_session,
    set_availability_fingerprint,
    set_availability_pagination,
    set_availability_search_result,
    set_presented_availability,
)

_FLAT_KEYS = (
    "availability_fingerprint",
    "last_execution_result",
    "presented_availability",
    "availability_presentation",
)


def _nested_session() -> dict:
    return {
        "schema_version": 2,
        "availability": {
            "fingerprint": "fp-nested",
            "cache": {
                "search_result": {
                    "type": "availability",
                    "status": "success",
                    "slots": [{"starts_at": "2026-07-10T10:00:00Z"}],
                    "search_date": "2026-07-10",
                    "availability_fingerprint": "fp-nested",
                }
            },
            "presentation": {
                "presented": {
                    "slots": [{"starts_at": "2026-07-10T10:00:00Z"}],
                    "search_date": "2026-07-10",
                },
                "page_index": 1,
                "page_size": 3,
            },
        },
    }


def test_nested_read_all_artifacts():
    session = _nested_session()
    assert availability_fingerprint_from_session(session) == "fp-nested"
    cache = availability_cache_from_session(session)
    assert cache is not None
    assert len(cache["slots"]) == 1
    presented = presented_availability_from_session(session)
    assert presented is not None
    assert presented["search_date"] == "2026-07-10"
    pagination = availability_pagination_from_session(session)
    assert pagination == {"page_index": 1, "page_size": 3}


def test_write_helpers_nested_only_no_flat_keys():
    session: dict = {"schema_version": 2}
    search = {
        "type": "availability",
        "status": "success",
        "slots": [{"starts_at": "2026-07-11T09:00:00Z"}],
        "search_date": "2026-07-11",
    }
    presented = {"slots": [{"starts_at": "2026-07-11T09:00:00Z"}], "search_date": "2026-07-11"}
    set_availability_fingerprint(session, "fp-w")
    set_availability_search_result(session, search)
    set_presented_availability(session, presented)
    set_availability_pagination(session, page_index=2, page_size=6)
    for key in _FLAT_KEYS:
        assert key not in session
    assert session["availability"]["fingerprint"] == "fp-w"
    assert session["availability"]["presentation"]["page_index"] == 2


def test_defensive_copy_presented_and_search_result():
    session = _nested_session()
    presented = presented_availability_from_session(session)
    assert presented is not None
    presented["slots"].append({"starts_at": "x"})
    again = presented_availability_from_session(session)
    assert again is not None
    assert len(again["slots"]) == 1

    set_availability_search_result(
        session,
        {
            "type": "availability",
            "status": "success",
            "slots": [{"starts_at": "a"}],
        },
    )
    written = session["availability"]["cache"]["search_result"]
    mutate = {"type": "availability", "status": "success", "slots": [{"starts_at": "b"}]}
    set_availability_search_result(session, mutate)
    mutate["slots"].append({"starts_at": "c"})
    assert len(session["availability"]["cache"]["search_result"]["slots"]) == 1
    assert written is not session["availability"]["cache"]["search_result"]


def test_clear_all_and_presentation_only():
    session = _nested_session()
    clear_availability_presentation(session)
    assert availability_fingerprint_from_session(session) == "fp-nested"
    assert availability_cache_from_session(session) is not None
    assert presented_availability_from_session(session) is None
    assert (availability_pagination_from_session(session) or {}).get("page_index") == 0

    clear_availability_artifacts(session)
    assert availability_fingerprint_from_session(session) is None
    assert availability_cache_from_session(session) is None
    assert presented_availability_from_session(session) is None


def test_historical_flat_normalizes_then_canonical_access():
    flat = {
        "availability_fingerprint": "fp-flat",
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "slots": [{"starts_at": "2026-07-12T11:00:00Z"}],
            "search_date": "2026-07-12",
        },
        "presented_availability": {
            "slots": [{"starts_at": "2026-07-12T11:00:00Z"}],
            "search_date": "2026-07-12",
        },
        "availability_presentation": {"page_index": 0, "page_size": 6},
    }
    v2 = normalize_session_to_v2(flat)
    assert v2["availability"]["fingerprint"] == "fp-flat"
    assert availability_fingerprint_from_session(v2) == "fp-flat"
    assert presented_availability_from_session(v2)["search_date"] == "2026-07-12"

    # Direct flat read fallback still works before normalize (migration).
    assert availability_fingerprint_from_session(deepcopy(flat)) == "fp-flat"


def test_apply_availability_artifacts_batch():
    session: dict = {}
    apply_availability_artifacts(
        session,
        fingerprint="fp-b",
        search_result={
            "type": "availability",
            "status": "success",
            "slots": [{"starts_at": "2026-07-13T08:00:00Z"}],
        },
        presented={"slots": [{"starts_at": "2026-07-13T08:00:00Z"}]},
        presentation={"page_index": 3, "page_size": 3},
    )
    for key in _FLAT_KEYS:
        assert key not in session
    assert availability_pagination_from_session(session)["page_index"] == 3


def test_times_only_presented_window_is_established_for_recovery():
    """Recovery may persist display times without a slots list."""
    nested = {
        "availability": {
            "presentation": {
                "presented": {
                    "search_date": "2026-07-22",
                    "times": ["09:00", "10:00"],
                }
            }
        }
    }
    presented = presented_availability_from_session(nested)
    assert presented is not None
    assert presented["times"] == ["09:00", "10:00"]

    flat = {
        "presented_availability": {
            "search_date": "2026-07-22",
            "times": ["09:00"],
        }
    }
    assert presented_availability_from_session(flat)["times"] == ["09:00"]


def test_malformed_empty_presented_dict_is_not_established():
    """Empty / times-empty dicts are not treated as a presentation window."""
    assert presented_availability_from_session({"presented_availability": {}}) is None
    assert (
        presented_availability_from_session(
            {"availability": {"presentation": {"presented": {}}}}
        )
        is None
    )
    assert (
        presented_availability_from_session(
            {"presented_availability": {"times": []}}
        )
        is None
    )
    assert (
        presented_availability_from_session(
            {"availability": {"presentation": {"presented": {"times": []}}}}
        )
        is None
    )
