"""Tests for resolve_service list-pick behaviour (candidate_keys)."""
from nlu.catalog import (
    infer_service_term_from_utterance,
    resolve_service,
    spoken_unique_catalog_mention,
    unique_prefix_catalog_pick,
)

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


_HAIRCUTS = {
    "premium haircut": 1001,
    "flexi haircut + prunning": 1002,
}


def test_premium_trim_resolves_to_unique_premium_haircut():
    result = resolve_service("premium trim", _HAIRCUTS)
    assert result["service_id"] == "premium haircut"
    assert result["service_candidates"] == []


def test_flexi_haircut_phrase_resolves_without_candidate_list():
    result = resolve_service("flexi haircut", _HAIRCUTS)
    assert result["service_id"] == "flexi haircut + prunning"
    assert result["service_candidates"] == []


def test_spaceship_room_has_no_unique_prefix_pick():
    rooms = {"King Room": "room_king", "King Suite": "room_suite"}
    assert unique_prefix_catalog_pick("spaceship room", list(rooms)) is None


def test_one_character_and_noise_only_prefixes_cannot_pick_catalogue_items():
    aliases = ["integration spa treatment", "premium haircut"]

    assert unique_prefix_catalog_pick("i", aliases) is None
    assert unique_prefix_catalog_pick("me", aliases) is None
    assert infer_service_term_from_utterance(
        "yes i want to book another appointment", dict.fromkeys(aliases)
    ) is None
    assert resolve_service("i", dict.fromkeys(aliases)) == {
        "service_id": None,
        "service_candidates": [],
    }


def test_exact_short_alias_remains_valid_but_non_exact_short_prefix_does_not():
    aliases = ["it", "integration spa treatment"]

    assert unique_prefix_catalog_pick("it", aliases) == "it"
    assert unique_prefix_catalog_pick("in", aliases) is None


def test_meaningful_multi_character_prefix_retains_existing_behavior():
    aliases = ["integration spa treatment", "premium haircut"]

    assert (
        unique_prefix_catalog_pick("integration spa", aliases)
        == "integration spa treatment"
    )
    assert unique_prefix_catalog_pick("premium", aliases) == "premium haircut"


def test_ambiguous_meaningful_prefix_does_not_select_first_item():
    aliases = ["premium haircut", "premium spa treatment"]

    assert unique_prefix_catalog_pick("premium", aliases) is None
    resolved = resolve_service("premium", dict.fromkeys(aliases))
    assert resolved["service_id"] is None
    assert resolved["service_candidates"] == aliases


def test_spoken_unique_mention_returns_spoken_subset_not_label():
    phrases = list(_HAIRCUTS)
    assert spoken_unique_catalog_mention(
        "rather book flexi haircut", phrases
    ) == "flexi haircut"
    assert spoken_unique_catalog_mention("premium", phrases) == "premium"


def test_spoken_unique_mention_rejects_negation_and_ambiguity():
    phrases = list(_HAIRCUTS)
    assert spoken_unique_catalog_mention("not premium", phrases) is None
    assert spoken_unique_catalog_mention("haircut", phrases) is None
    assert spoken_unique_catalog_mention(
        "which is better premium or flexi?", phrases
    ) is None
