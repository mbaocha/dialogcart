"""Tests for resolve_service list-pick behaviour (candidate_keys)."""
from nlu.catalog import infer_service_term_from_utterance, resolve_service

_ALIASES = {
    "premium haircut": "beauty.premium",
    "flexi haircut + prunning": "beauty.flexi",
    "premium spa treatment": "beauty.spa",
}


def test_infer_flexi_from_availability_utterance():
    assert (
        infer_service_term_from_utterance(
            "show availability for flexi",
            _ALIASES,
        )
        == "flexi haircut + prunning"
    )


def test_infer_none_when_no_service_token():
    assert (
        infer_service_term_from_utterance("show availability", _ALIASES) is None
    )


def test_premium_picks_uniquely_from_haircut_candidates():
    result = resolve_service(
        "premium",
        _ALIASES,
        candidate_keys=["premium haircut", "flexi haircut + prunning"],
    )
    assert result["service_id"] == "premium haircut"
    assert result["service_candidates"] == []


def test_flexi_picks_from_candidate_list():
    result = resolve_service(
        "flexi",
        _ALIASES,
        candidate_keys=["premium haircut", "flexi haircut + prunning"],
    )
    assert result["service_id"] == "flexi haircut + prunning"
    assert result["service_candidates"] == []


def test_candidate_keys_skip_prior_text_noise():
    result = resolve_service(
        "premium",
        _ALIASES,
        prior_text="book me for a service flexi book me a haircut",
        awaiting_service_id=True,
        candidate_keys=["premium haircut", "flexi haircut + prunning"],
    )
    assert result["service_id"] == "premium haircut"
    assert result["service_candidates"] == []


def test_no_service_term_with_resolved_session_service():
    """Date-only availability follow-up reuses locked service, no catalog flood."""
    result = resolve_service(
        None,
        _ALIASES,
        resolved_service_id="premium haircut",
    )
    assert result["service_id"] == "premium haircut"
    assert result["service_candidates"] == []


def test_no_service_term_cold_start_still_lists_catalog():
    result = resolve_service(None, _ALIASES)
    assert result["service_id"] is None
    assert len(result["service_candidates"]) == len(_ALIASES)
