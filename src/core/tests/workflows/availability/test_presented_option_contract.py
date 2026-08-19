from core.planning.temporal_proposal import try_bind_offered_time_selection
from core.workflows.availability.presentation import (
    compute_presented_options_reference,
    presented_options_for_nlu,
    trusted_presented_option,
)


def _state(starts):
    slots = [
        {"starts_at": start, "ends_at": start.replace(":30:00Z", ":59:00Z")}
        for start in starts
    ]
    fingerprint = "criteria-fingerprint"
    presented = {
        "slots": slots,
        "times": ["1:30 PM", "2:00 PM"][: len(slots)],
        "fingerprint": fingerprint,
    }
    return slots, fingerprint, presented


def test_reference_identifies_ordered_visible_window():
    _slots, fingerprint, presented = _state(
        ["2026-08-11T13:30:00Z", "2026-08-11T14:00:00Z"]
    )
    first = compute_presented_options_reference(presented, fingerprint)
    reversed_window = {**presented, "slots": list(reversed(presented["slots"])), "times": list(reversed(presented["times"]))}
    assert first and first.startswith("avp1_")
    assert compute_presented_options_reference(reversed_window, fingerprint) != first


def test_projection_contains_labels_not_provider_objects():
    _slots, fingerprint, presented = _state(["2026-08-11T13:30:00Z"])
    projected = presented_options_for_nlu(presented, fingerprint)
    assert projected == {
        "reference": compute_presented_options_reference(presented, fingerprint),
        "options": [{"index": 1, "label": "1:30 PM"}],
    }


def test_reference_validation_rejects_stale_and_out_of_range():
    _slots, fingerprint, presented = _state(["2026-08-11T13:30:00Z"])
    reference = compute_presented_options_reference(presented, fingerprint)
    assert trusted_presented_option(presented, fingerprint, presentation_ref=reference, option=1)
    assert trusted_presented_option(presented, fingerprint, presentation_ref="stale", option=1) is None
    assert trusted_presented_option(presented, fingerprint, presentation_ref=reference, option=2) is None


def test_presented_option_binds_trusted_canonical_time():
    slots, fingerprint, presented = _state(["2026-08-11T13:30:00Z"])
    reference = compute_presented_options_reference(presented, fingerprint)
    session = {
        "availability_fingerprint": fingerprint,
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "slots": slots,
            "availability_fingerprint": fingerprint,
        },
        "presented_availability": presented,
    }
    result = try_bind_offered_time_selection(
        {"service_id": "haircut"},
        session,
        temporal={
            "expression": "1:30",
            "start_time": None,
            "resolution": {
                "kind": "presented_option",
                "presentation_ref": reference,
                "option": 1,
            },
        },
    )
    assert result is not None
    assert result["slots"]["time"] == "13:30"


def test_explicit_0130_does_not_bind_presented_1330():
    slots, fingerprint, presented = _state(["2026-08-11T13:30:00Z"])
    session = {
        "availability_fingerprint": fingerprint,
        "last_execution_result": {"type": "availability", "status": "success", "slots": slots},
        "presented_availability": presented,
    }
    assert try_bind_offered_time_selection(
        {"service_id": "haircut"}, session,
        time_proposal={"mode": "exact", "value": "01:30"},
        temporal={"expression": "1:30 am", "start_time": "01:30", "resolution": {"kind": "explicit"}},
        user_facts={"time": "01:30", "time_from_current_turn": True},
    ) is None
