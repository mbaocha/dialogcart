from nlu.pipeline import _apply_presented_temporal_resolution


def _slm(start_time="01:30"):
    return {
        "intent": "CREATE_APPOINTMENT",
        "confidence": 1.0,
        "facts": {"times": [start_time] if start_time else []},
        "temporal": {"expression": "1:30", "start_time": start_time, "mode": "none"},
    }


def _context(*labels):
    return {
        "temporal_context_version": 1,
        "presented_options": {
            "reference": "avp1_test",
            "options": [
                {"index": index, "label": label}
                for index, label in enumerate(labels, start=1)
            ],
        },
    }


def test_bare_clock_without_options_is_ambiguous():
    result = _apply_presented_temporal_resolution("1:30", _slm(), None)
    assert result["temporal"]["start_time"] is None
    assert result["temporal"]["resolution"] == {"kind": "ambiguous_meridiem"}
    assert result["facts"]["times"] == []


def test_bare_clock_selects_unique_displayed_pm_option():
    result = _apply_presented_temporal_resolution("1:30", _slm(), _context("1:30 PM", "2:00 PM"))
    assert result["temporal"]["start_time"] is None
    assert result["temporal"]["resolution"] == {
        "kind": "presented_option", "presentation_ref": "avp1_test", "option": 1
    }


def test_bare_hour_selects_unique_displayed_option():
    result = _apply_presented_temporal_resolution(
        "9", _slm(None), _context("9:00 AM", "10:00 AM")
    )
    assert result["temporal"]["start_time"] is None
    assert result["temporal"]["resolution"] == {
        "kind": "presented_option", "presentation_ref": "avp1_test", "option": 1
    }
    assert result["facts"]["times"] == []


def test_bare_clock_matching_am_and_pm_is_invalid_reference():
    result = _apply_presented_temporal_resolution("1:30", _slm(), _context("1:30 AM", "1:30 PM"))
    assert result["temporal"]["resolution"] == {"kind": "invalid_option_reference"}


def test_first_one_selects_first_option_and_invalid_ordinal_clarifies():
    selected = _apply_presented_temporal_resolution("the first one", _slm(None), _context("1:30 PM", "2:00 PM"))
    assert selected["temporal"]["resolution"]["option"] == 1
    invalid = _apply_presented_temporal_resolution("the third one", _slm(None), _context("1:30 PM", "2:00 PM"))
    assert invalid["temporal"]["resolution"] == {"kind": "invalid_option_reference"}


def test_explicit_meridiem_and_24_hour_remain_explicit():
    pm = _apply_presented_temporal_resolution("1:30 pm", _slm("13:30"), _context("1:30 PM"))
    military = _apply_presented_temporal_resolution("13:30", _slm("13:30"), _context("1:30 PM"))
    assert pm["temporal"]["start_time"] == "13:30"
    assert pm["temporal"]["resolution"] == {"kind": "explicit"}
    assert military["temporal"]["resolution"] == {"kind": "explicit"}


def test_date_only_output_cannot_carry_explicit_time_resolution():
    slm = _slm(None)
    slm["temporal"].update(
        {
            "expression": "July 11",
            "start_date": "2026-07-11",
            "resolution": {"kind": "explicit"},
        }
    )

    result = _apply_presented_temporal_resolution(
        "actually July 11", slm, _context("10:00 AM", "11:00 AM")
    )

    assert result["temporal"]["start_date"] == "2026-07-11"
    assert result["temporal"]["resolution"] is None


def test_unrelated_turn_cannot_carry_incomplete_presented_option_resolution():
    slm = _slm(None)
    slm["facts"]["customer_contact_name"] = "Godswill Mbaocha"
    slm["temporal"]["resolution"] = {
        "kind": "presented_option",
        "presentation_ref": "avp1_test",
        "option": None,
    }

    result = _apply_presented_temporal_resolution(
        "Godswill Mbaocha", slm, _context("9:00 AM", "9:30 AM")
    )

    assert result["facts"]["customer_contact_name"] == "Godswill Mbaocha"
    assert result["temporal"]["resolution"] is None
