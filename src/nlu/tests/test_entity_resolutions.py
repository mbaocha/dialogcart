"""Contract and grounding tests for authoritative entity_resolutions."""
import pytest
from unittest.mock import patch

from nlu.api import app
from nlu.entity_resolution import (
    AmbiguousEntity,
    EntityExtractionValidationError,
    EntityResolutionValidationError,
    MentionState,
    ResolvedEntity,
    UnresolvedEntity,
    serialize_entity_resolutions,
    usable_customer_contact_name,
    validate_generated_entity_evidence,
    validate_generated_entity_results,
    validate_entity_resolutions,
)
from nlu.pipeline import (
    NLUPipeline,
    PipelineResult,
    _project_offered_candidate_aliases,
)
from nlu.stages.stage2.entity_schema import compile_business_entities
from nlu.stages.stage2.dispatcher import supports_schema_entity_extraction
from nlu.stages.stage2.groups.availability import _merge as merge_availability
from nlu.stages.stage2.groups.create import (
    _malformed_entity_results_fallback,
    _merge,
)


SCHEMA = {
    "version": 1,
    "fields": [
        {"name": "room_type", "type": "catalog", "role": "bookable_item",
         "catalog": {"King Room": "room_king", "King Suite": "room_suite"}},
        {"name": "staff", "type": "catalog", "role": "staff",
         "catalog": {"Alex": 12, "Alexa": 19}},
        {"name": "engine_type", "type": "enum", "values": ["petrol", "diesel"]},
        {"name": "vehicle_details", "type": "text"},
    ],
}


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "Guest", "gUeSt", "anonymous", "I don't know", "not sure"],
)
def test_customer_contact_name_rejects_non_name_placeholders(value):
    assert usable_customer_contact_name(value) is None


def test_customer_contact_name_normalizes_usable_semantic_value():
    assert usable_customer_contact_name("  Godswill Mbaocha  ") == "Godswill Mbaocha"


def test_pending_contact_name_evidence_cannot_authorize_confirmation():
    compiled = compile_business_entities({
        "version": 1,
        "fields": [{
            "name": "customer_contact_name",
            "type": "text",
            "description": "The customer's name for booking contact details.",
        }],
    })
    merged = _merge(
        {
            "validated_intent": "CONFIRM_ACTION",
            "confidence": 0.72,
            "entity_results": {
                "customer_contact_name": {
                    "status": "MENTIONED_VALUE",
                    "value": "aSEW NATHAN",
                },
            },
        },
        "CREATE_APPOINTMENT",
        text="aSEW NATHAN",
        conversation_context={
            "last_intent": "CREATE_APPOINTMENT",
            "active_booking_intent": "CREATE_APPOINTMENT",
            "pending_profile_request": "CUSTOMER_CONTACT_NAME",
        },
        compiled=compiled,
    )

    assert merged["intent"] == "CREATE_APPOINTMENT"
    evidence = merged["_entity_mentions"]["customer_contact_name"]
    assert evidence.state == MentionState.MENTIONED_VALUE
    assert evidence.raw_value == "aSEW NATHAN"


def test_explicit_contact_name_correction_recovers_missed_generated_mention():
    compiled = compile_business_entities({
        "version": 1,
        "fields": [{
            "name": "customer_contact_name",
            "type": "text",
            "description": "The customer's name for booking contact details.",
        }],
    })
    merged = _merge(
        {
            "validated_intent": "CORRECTION",
            "confidence": 0.95,
            "entity_results": {
                "customer_contact_name": {"status": "NOT_MENTIONED"},
            },
        },
        "CORRECTION",
        text="Sorry, the contact name is Godin Nnem",
        compiled=compiled,
    )

    evidence = merged["_entity_mentions"]["customer_contact_name"]
    assert evidence.state == MentionState.MENTIONED_VALUE
    assert evidence.raw_value == "Godin Nnem"

SERVICE_SCHEMA = {
    "version": 1,
    "fields": [{
        "name": "service",
        "type": "catalog",
        "role": "bookable_item",
        "catalog": {"premium haircut": 1001, "flexi haircut + prunning": 1002},
    }],
}


def compiled():
    return compile_business_entities(SCHEMA)


def test_generated_unresolved_entity_cannot_carry_value():
    raw = {
        field.name: {"status": "NOT_MENTIONED"}
        for field in compiled().fields
    }
    raw["engine_type"] = {
        "status": "MENTIONED_UNRESOLVED",
        "value": "diesel",
    }

    with pytest.raises(
        EntityExtractionValidationError,
        match="MENTIONED_UNRESOLVED must not carry a value",
    ):
        validate_generated_entity_results(raw, compiled())


def test_malformed_registration_result_returns_public_unresolved_evidence():
    schema = {
        "version": 1,
        "fields": [
            {
                "name": "registration_number",
                "type": "text",
                "description": "Vehicle registration number.",
                "required": True,
            }
        ],
    }
    compiled_schema = compile_business_entities(schema)
    malformed = {
        "entity_results": {
            "registration_number": {
                "status": "MENTIONED_UNRESOLVED",
                "value": "aa1239",
            }
        }
    }
    fallback = _malformed_entity_results_fallback(
        malformed,
        "CREATE_APPOINTMENT",
        text="aa1239",
        conversation_context={
            "last_intent": "CREATE_APPOINTMENT",
            "missing_slots": ["registration_number"],
        },
        compiled=compiled_schema,
    )
    pipeline = NLUPipeline()

    with patch.object(pipeline, "_slm_extract", return_value=fallback):
        result = pipeline.run(
            "aa1239",
            {"aliases": {}, "booking_mode": "service"},
            now="2026-08-14T09:45:00Z",
            timezone="UTC",
            conversation_context={
                "last_intent": "CREATE_APPOINTMENT",
                "active_booking_intent": "CREATE_APPOINTMENT",
                "missing_slots": ["registration_number"],
            },
            entity_schema=schema,
        )

    assert result.facts["registration_number"] is None
    assert result.facts["booking_id"] == "aa1239"
    assert serialize_entity_resolutions(result.entity_resolutions) == {
        "registration_number": {"resolution": "UNRESOLVED"}
    }
    assert result.understanding == "UNRECOGNIZED_INPUT"


def _availability_raw(*, service=None, mentioned=False):
    return {
        "validated_intent": "AVAILABILITY",
        "confidence": 0.95,
        "facts": {
            "room_type": service,
            "staff": None,
            "engine_type": None,
            "vehicle_details": None,
        },
        "entity_mentions": {
            "room_type": mentioned,
            "staff": False,
            "engine_type": False,
            "vehicle_details": False,
        },
        "temporal": {
            "expression": "july 24",
            "start_date_expression": "july 24",
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": "2026-07-24",
            "start_time": None,
            "end_date": None,
            "end_time": None,
            "mode": "single_day",
            "confidence": 0.95,
        },
        "operation": None,
        "service_candidates": [],
    }


def test_empty_and_all_valid_variants():
    assert validate_entity_resolutions({}, compiled()) == {}
    out = validate_entity_resolutions({
        "room_type": {"resolution": "RESOLVED", "value": "room_king"},
        "staff": {"resolution": "AMBIGUOUS", "candidate_values": [12, 19]},
        "engine_type": {"resolution": "UNRESOLVED"},
    }, compiled())
    assert isinstance(out["room_type"], ResolvedEntity)
    assert isinstance(out["staff"], AmbiguousEntity)
    assert isinstance(out["engine_type"], UnresolvedEntity)


@pytest.mark.parametrize("payload", [
    {"resolution": "BOGUS"},
    {"resolution": "RESOLVED"},
    {"resolution": "RESOLVED", "value": "room_king", "candidate_values": ["room_suite"]},
    {"resolution": "AMBIGUOUS", "value": "room_king", "candidate_values": ["room_king", "room_suite"]},
    {"resolution": "AMBIGUOUS", "candidate_values": []},
    {"resolution": "AMBIGUOUS", "candidate_values": ["room_king"]},
    {"resolution": "AMBIGUOUS", "candidate_values": ["room_king", "room_king"]},
    {"resolution": "UNRESOLVED", "value": "room_king"},
    {"resolution": "UNRESOLVED", "candidate_values": ["room_king", "room_suite"]},
    {"resolution": "UNRESOLVED", "extra": True},
])
def test_invalid_combinations_remain_contract_failures(payload):
    with pytest.raises(EntityResolutionValidationError):
        validate_entity_resolutions({"room_type": payload}, compiled())


def test_malformed_unknown_name_type_and_catalog_value_fail():
    for raw in (None, [], "bad"):
        with pytest.raises(EntityResolutionValidationError):
            validate_entity_resolutions(raw, compiled())
    with pytest.raises(EntityResolutionValidationError, match="unknown entity"):
        validate_entity_resolutions({"service": {"resolution": "UNRESOLVED"}}, compiled())
    with pytest.raises(EntityResolutionValidationError, match="outside its catalog"):
        validate_entity_resolutions({"staff": {"resolution": "RESOLVED", "value": "12"}}, compiled())


def _slm(**facts):
    raw_facts = {field.name: facts.get(field.name) for field in compiled().fields}
    mentions = {name: value is not None for name, value in raw_facts.items()}
    return {"facts": {**raw_facts, "service_id": None, "booking_id": None},
            "_entity_mentions": validate_generated_entity_evidence(
                raw_facts, mentions, compiled()),
            "service_candidates": [], "service_term": facts.get("room_type")}


def _production(raw_facts, mentions, schema=None):
    schema = schema or compiled()
    raw = {
        "validated_intent": "CREATE_APPOINTMENT", "confidence": 0.9,
        "facts": raw_facts, "entity_mentions": mentions,
        "temporal": {}, "operation": None, "declined_entities": [],
    }
    merged = _merge(raw, "CREATE_APPOINTMENT", compiled=schema)
    return NLUPipeline()._resolve_service_ambiguity(
        merged, {}, compiled=schema, text="test utterance"
    )


def _candidate_pick(schema, phrase, candidates):
    schema = compile_business_entities(schema)
    field = schema.bookable_item_field
    raw_facts = {item.name: None for item in schema.fields}
    raw_mentions = {item.name: False for item in schema.fields}
    raw_facts[field.name] = phrase
    raw_mentions[field.name] = True
    slm = {
        "facts": {**raw_facts, "service_id": None, "booking_id": None},
        "_entity_mentions": validate_generated_entity_evidence(
            raw_facts, raw_mentions, schema
        ),
        "service_term": phrase,
        "service_candidates": [],
    }
    return NLUPipeline()._resolve_service_ambiguity(
        slm,
        {},
        conversation_context={
            "missing_slots": ["service_id"],
            "service_candidates": candidates,
        },
        compiled=schema,
    )


def test_generic_room_overlap_stays_unresolved_without_unique_prefix():
    compiled_schema = compiled()
    slm = {
        "facts": {
            "room_type": "spaceship room",
            "staff": None,
            "engine_type": None,
            "vehicle_details": None,
            "service_id": None,
            "booking_id": None,
        },
        "_entity_mentions": validate_generated_entity_evidence(
            {
                "room_type": "spaceship room",
                "staff": None,
                "engine_type": None,
                "vehicle_details": None,
            },
            {
                "room_type": True,
                "staff": False,
                "engine_type": False,
                "vehicle_details": False,
            },
            compiled_schema,
        ),
        "service_term": "spaceship room",
        "service_candidates": [],
    }
    out = NLUPipeline()._resolve_schema_entities(
        slm, {}, compiled_schema, text="spaceship room"
    )
    assert serialize_entity_resolutions(out["entity_resolutions"])["room_type"] == {
        "resolution": "UNRESOLVED",
    }


def test_informal_premium_trim_resolves_to_typed_premium_id():
    compiled = compile_business_entities(SERVICE_SCHEMA)
    slm = {
        "facts": {"service": "premium trim", "service_id": None, "booking_id": None},
        "_entity_mentions": validate_generated_entity_evidence(
            {"service": "premium trim"}, {"service": True}, compiled
        ),
        "service_term": "premium trim",
        "service_candidates": [],
    }
    out = NLUPipeline()._resolve_schema_entities(
        slm,
        {},
        compiled,
        text="Can you fit me in for a premium trim tomorrow?",
    )
    assert serialize_entity_resolutions(out["entity_resolutions"])["service"] == {
        "resolution": "RESOLVED",
        "value": 1001,
    }


def test_switch_to_flexi_catalog_label_grounds_from_utterance():
    compiled = compile_business_entities(SERVICE_SCHEMA)
    merged = _merge(
        {
            "validated_intent": "CORRECTION",
            "confidence": 0.95,
            "entity_results": {
                "service": {
                    "status": "MENTIONED_VALUE",
                    "value": "flexi haircut + prunning",
                },
            },
            "temporal": {
                "expression": None,
                "start_date_expression": None,
                "start_time_expression": None,
                "end_date_expression": None,
                "end_time_expression": None,
                "start_date": None,
                "start_time": None,
                "end_date": None,
                "end_time": None,
                "mode": "none",
                "confidence": 0.95,
            },
            "operation": None,
            "declined_entities": [],
        },
        "CORRECTION",
        text="switch to flexi haircut",
        compiled=compiled,
    )
    from nlu.pipeline import _strip_ungrounded_schema_entity_values

    stripped = _strip_ungrounded_schema_entity_values(
        "switch to flexi haircut", merged
    )
    assert stripped["_entity_mentions"]["service"].raw_value == "flexi haircut"
    out = NLUPipeline()._resolve_schema_entities(
        stripped, {}, compiled, text="switch to flexi haircut"
    )
    assert serialize_entity_resolutions(out["entity_resolutions"])["service"] == {
        "resolution": "RESOLVED",
        "value": 1002,
    }


def test_schema_entity_grounding_does_not_use_substring_matches():
    compiled = compile_business_entities(SERVICE_SCHEMA)
    merged = _merge(
        {
            "validated_intent": "CREATE_APPOINTMENT",
            "confidence": 0.95,
            "entity_results": {
                "service": {"status": "MENTIONED_VALUE", "value": "oil"},
            },
            "temporal": {},
            "operation": None,
            "declined_entities": [],
        },
        "CREATE_APPOINTMENT",
        text="spoiler package",
        compiled=compiled,
    )
    from nlu.pipeline import _strip_ungrounded_schema_entity_values

    stripped = _strip_ungrounded_schema_entity_values("spoiler package", merged)

    assert stripped["_entity_mentions"]["service"].state.value == "NOT_MENTIONED"
    assert stripped["facts"]["service"] is None


def test_pipeline_multiple_entities_and_canonical_legacy_projections():
    out = NLUPipeline()._resolve_service_ambiguity(
        _slm(room_type="King Room", engine_type="diesel", vehicle_details="BMW 320d"),
        {}, compiled=compiled(),
    )
    serialized = serialize_entity_resolutions(out["entity_resolutions"])
    assert serialized == {
        "room_type": {"resolution": "RESOLVED", "value": "room_king"},
        "engine_type": {"resolution": "RESOLVED", "value": "diesel"},
        "vehicle_details": {"resolution": "RESOLVED", "value": "BMW 320d"},
    }
    assert out["facts"]["room_type"] == "King Room"
    assert out["facts"]["service_id"] == "king room"
    assert out["service_candidates"] == []


def test_pipeline_ambiguity_does_not_erase_resolved_sibling():
    out = NLUPipeline()._resolve_service_ambiguity(
        _slm(room_type="king", engine_type="petrol"), {}, compiled=compiled()
    )
    serialized = serialize_entity_resolutions(out["entity_resolutions"])
    assert serialized["room_type"] == {
        "resolution": "AMBIGUOUS", "candidate_values": ["room_king", "room_suite"]}
    assert serialized["engine_type"]["value"] == "petrol"
    assert set(out["service_candidates"]) == {"king room", "king suite"}


@pytest.mark.parametrize("phrase", ["premium haircut", "Premium"])
def test_integer_canonical_candidates_match_exact_and_shortened_labels(phrase):
    out = _candidate_pick(SERVICE_SCHEMA, phrase, [1001, 1002])

    resolution = serialize_entity_resolutions(out["entity_resolutions"])["service"]
    assert resolution == {"resolution": "RESOLVED", "value": 1001}
    assert type(resolution["value"]) is int
    assert out["facts"]["service_id"] == "premium haircut"


def test_string_canonical_candidate_preserves_string_type():
    out = _candidate_pick(SCHEMA, "King Room", ["room_king", "room_suite"])

    resolution = serialize_entity_resolutions(out["entity_resolutions"])["room_type"]
    assert resolution == {"resolution": "RESOLVED", "value": "room_king"}
    assert type(resolution["value"]) is str


def test_multiple_aliases_for_offered_canonical_value_are_searchable():
    schema = {
        "fields": [{
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "catalog": {"premium haircut": 7, "premium trim": 7, "basic trim": 8},
        }],
    }

    out = _candidate_pick(schema, "premium trim", [7])

    assert serialize_entity_resolutions(out["entity_resolutions"])["service"] == {
        "resolution": "RESOLVED",
        "value": 7,
    }


def test_mixed_legacy_alias_and_canonical_candidates_remain_restricted():
    out = _candidate_pick(
        SERVICE_SCHEMA,
        "Flexi",
        ["premium haircut", 1002],
    )

    assert serialize_entity_resolutions(out["entity_resolutions"])["service"] == {
        "resolution": "RESOLVED",
        "value": 1002,
    }


def test_candidate_alias_projection_is_deterministic_and_deduplicated():
    aliases = {
        "premium haircut": 1001,
        "premium trim": 1001,
        "flexi haircut": 1002,
    }

    assert _project_offered_candidate_aliases(
        [1001, "premium haircut", 1001], aliases
    ) == ["premium haircut", "premium trim"]


def test_candidate_alias_projection_uses_strict_bool_integer_identity():
    aliases = {"boolean service": True, "integer service": 1}

    assert _project_offered_candidate_aliases([1], aliases) == ["integer service"]
    assert _project_offered_candidate_aliases([True], aliases) == ["boolean service"]


def test_string_candidate_can_be_both_alias_key_and_canonical_value():
    aliases = {
        "room_king": "other_room",
        "king room": "room_king",
        "king suite": "room_suite",
    }

    assert _project_offered_candidate_aliases(["room_king"], aliases) == [
        "room_king",
        "king room",
    ]


@pytest.mark.parametrize(
    ("phrase", "candidates"),
    [
        ("Premium", [9999]),
        ("spaceship", [1001, 1002]),
        ("Premium", [1002]),
    ],
)
def test_unknown_no_match_and_outside_offer_are_unresolved_without_crashing(
    phrase, candidates
):
    out = _candidate_pick(SERVICE_SCHEMA, phrase, candidates)

    assert serialize_entity_resolutions(out["entity_resolutions"])["service"] == {
        "resolution": "UNRESOLVED",
    }
    assert out["facts"]["service_id"] is None
    assert out["service_candidates"] == []


def test_full_catalog_prefix_cannot_bypass_offered_candidate_restriction():
    out = _candidate_pick(SERVICE_SCHEMA, "Premium", [1002])

    assert serialize_entity_resolutions(out["entity_resolutions"])["service"] == {
        "resolution": "UNRESOLVED",
    }
    assert out["facts"]["service_id"] is None


def test_pipeline_unresolved_and_unmentioned_are_distinct():
    out = NLUPipeline()._resolve_service_ambiguity(
        _slm(room_type="spaceship", engine_type="steam"), {}, compiled=compiled()
    )
    serialized = serialize_entity_resolutions(out["entity_resolutions"])
    assert serialized == {
        "room_type": {"resolution": "UNRESOLVED"},
        "engine_type": {"resolution": "UNRESOLVED"},
    }
    assert "staff" not in serialized
    assert out["facts"]["service_id"] is None


def test_availability_date_only_with_schema_emits_empty_entity_resolutions():
    pipeline = NLUPipeline()
    service_compiled = compile_business_entities(SERVICE_SCHEMA)
    raw = _availability_raw()
    raw["facts"] = {"service": None}
    raw["entity_mentions"] = {"service": False}
    stage2 = merge_availability(
        raw,
        "AVAILABILITY",
        text="show slots for july 24",
        compiled=service_compiled,
    )

    with patch.object(pipeline, "_slm_extract", return_value=stage2):
        result = pipeline.run(
            "show slots for july 24",
            {},
            now="2026-07-01T10:00:00Z",
            timezone="UTC",
            entity_schema=SERVICE_SCHEMA,
        )

    assert result.intent["name"] == "AVAILABILITY"
    assert result.facts["dates"] == ["2026-07-24"]
    assert serialize_entity_resolutions(result.entity_resolutions) == {}


def test_availability_explicitly_unmentioned_schema_entities_are_skipped():
    merged = merge_availability(
        _availability_raw(), "AVAILABILITY", compiled=compiled()
    )
    out = NLUPipeline()._resolve_service_ambiguity(
        merged, {}, compiled=compiled()
    )

    assert serialize_entity_resolutions(out["entity_resolutions"]) == {}


def test_availability_discards_contextual_value_marked_unmentioned():
    raw = _availability_raw(service="King Room", mentioned=False)

    merged = merge_availability(
        raw,
        "AVAILABILITY",
        text="show dates for july 21",
        compiled=compiled(),
    )

    assert merged["facts"]["room_type"] is None
    assert merged["temporal"]["start_date"] == "2026-07-24"


def test_availability_claimed_mention_requires_complete_typed_evidence():
    raw = _availability_raw(service="King Room", mentioned=True)
    del raw["entity_mentions"]["room_type"]

    with pytest.raises(EntityExtractionValidationError, match="entity_mentions keys"):
        merge_availability(raw, "AVAILABILITY", compiled=compiled())


@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ("King Room", {"resolution": "RESOLVED", "value": "room_king"}),
        (None, {"resolution": "UNRESOLVED"}),
        (
            "king",
            {
                "resolution": "AMBIGUOUS",
                "candidate_values": ["room_king", "room_suite"],
            },
        ),
    ],
)
def test_availability_mentioned_entity_resolution_variants_are_unchanged(
    service, expected
):
    merged = merge_availability(
        _availability_raw(service=service, mentioned=True),
        "AVAILABILITY",
        compiled=compiled(),
    )
    out = NLUPipeline()._resolve_service_ambiguity(
        merged, {}, compiled=compiled()
    )

    assert (
        serialize_entity_resolutions(out["entity_resolutions"])["room_type"]
        == expected
    )


def test_legacy_service_fields_cannot_replace_typed_mention_evidence():
    with pytest.raises(
        EntityExtractionValidationError,
        match="typed entity mention evidence is missing",
    ):
        NLUPipeline()._resolve_service_ambiguity(
            {
                "facts": {"service_id": "king room"},
                "service_term": "King Room",
                "service_candidates": ["king room"],
            },
            {},
            compiled=compiled(),
        )


def test_pipeline_result_requires_empty_map_by_default():
    assert PipelineResult().entity_resolutions == {}


def test_successful_api_response_always_has_entity_resolutions():
    app.config["TESTING"] = True
    with patch("nlu.api._pipeline.run", return_value=PipelineResult()), app.test_client() as client:
        response = client.post("/resolve", json={"text": "hello"})
    assert response.status_code == 200
    assert response.get_json()["entity_resolutions"] == {}


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        ("CREATE_APPOINTMENT", True),
        ("CREATE_RESERVATION", True),
        ("CORRECTION", True),
        ("AVAILABILITY", True),
        ("OFF_TOPIC", False),
        ("GENERAL_INQUIRY", False),
        ("MODIFY_BOOKING", False),
        ("CANCEL_BOOKING", False),
        ("BOOKING_INQUIRY", False),
        ("CONFIRM_ACTION", False),
    ],
)
def test_stage2_schema_entity_capability_follows_group_contract(intent, expected):
    assert supports_schema_entity_extraction(intent) is expected


def _non_schema_stage2(intent, **extra):
    return {
        "intent": intent,
        "confidence": 0.99,
        "facts": {
            "dates": [], "times": [], "date_time_pairs": [],
            "service_id": None, "booking_id": None,
        },
        "time_constraint": None,
        "search_query": None,
        "service_term": None,
        "service_candidates": [],
        "temporal": {},
        **extra,
    }


def test_off_topic_with_schema_bypasses_entity_resolution_and_prior_booking_state():
    pipeline = NLUPipeline()
    stage2 = _non_schema_stage2(
        "OFF_TOPIC",
        off_topic_query="Who is the president of Nigeria?",
        answerable=True,
        answer="A brief factual answer.",
    )
    conversation_context = {
        "last_intent": "CREATE_APPOINTMENT",
        "active_booking_intent": "CREATE_APPOINTMENT",
        "missing_slots": ["date", "time"],
        "resolved_service_id": 1001,
        "service_candidates": [1001, 1002],
        "pending_assistant_proposals": [{
            "proposal_type": "entity_selection",
            "status": "PENDING",
            "slot_key": "service_id",
            "canonical_id": 1001,
        }],
    }

    with patch.object(pipeline, "_slm_extract", return_value=stage2):
        result = pipeline.run(
            "Who is the president of Nigeria?",
            {},
            now="2026-07-01T10:00:00Z",
            timezone="UTC",
            conversation_context=conversation_context,
            entity_schema=SERVICE_SCHEMA,
        )

    assert result.intent["name"] == "OFF_TOPIC"
    assert result.off_topic_query == "Who is the president of Nigeria?"
    assert result.facts["service_id"] is None
    assert serialize_entity_resolutions(result.entity_resolutions) == {}


def test_other_non_schema_stage2_group_bypasses_entity_resolution():
    pipeline = NLUPipeline()
    stage2 = _non_schema_stage2(
        "GENERAL_INQUIRY", search_query="opening hours"
    )

    with patch.object(pipeline, "_slm_extract", return_value=stage2):
        result = pipeline.run(
            "When are you open?",
            {},
            now="2026-07-01T10:00:00Z",
            timezone="UTC",
            entity_schema=SERVICE_SCHEMA,
        )

    assert result.intent["name"] == "GENERAL_INQUIRY"
    assert result.search_query == "opening hours"
    assert result.facts["service_id"] is None
    assert serialize_entity_resolutions(result.entity_resolutions) == {}


def test_schema_aware_pipeline_output_without_mentions_remains_invalid():
    pipeline = NLUPipeline()
    malformed = _non_schema_stage2("CREATE_APPOINTMENT")

    with patch.object(pipeline, "_slm_extract", return_value=malformed):
        with pytest.raises(
            EntityExtractionValidationError,
            match="typed entity mention evidence is missing",
        ):
            pipeline.run(
                "Book a haircut",
                {},
                now="2026-07-01T10:00:00Z",
                timezone="UTC",
                entity_schema=SERVICE_SCHEMA,
            )


def test_api_serializes_typed_authoritative_resolution():
    result = PipelineResult(entity_resolutions=validate_entity_resolutions({
        "room_type": {"resolution": "RESOLVED", "value": "room_king"},
    }, compiled()))
    app.config["TESTING"] = True
    with patch("nlu.api._pipeline.run", return_value=result), app.test_client() as client:
        body = client.post("/resolve", json={"text": "king room"}).get_json()
    assert body["entity_resolutions"] == {
        "room_type": {"resolution": "RESOLVED", "value": "room_king"}}


def test_production_absent_entity_is_omitted_without_candidates():
    out = _production(
        {field.name: None for field in compiled().fields},
        {field.name: False for field in compiled().fields},
    )
    assert serialize_entity_resolutions(out["entity_resolutions"]) == {}
    assert out["facts"]["service_id"] is None
    assert out["service_candidates"] == []


def test_production_explicit_unknown_mention_is_unresolved():
    facts = {field.name: None for field in compiled().fields}
    mentions = {field.name: False for field in compiled().fields}
    facts["room_type"] = "spaceship room"
    mentions["room_type"] = True
    out = _production(facts, mentions)
    assert serialize_entity_resolutions(out["entity_resolutions"])["room_type"] == {
        "resolution": "UNRESOLVED"}
    assert out["facts"]["service_id"] is None
    assert out["service_candidates"] == []


def test_production_mentioned_without_safe_value_is_unresolved():
    facts = {field.name: None for field in compiled().fields}
    mentions = {field.name: False for field in compiled().fields}
    mentions["room_type"] = True
    out = _production(facts, mentions)
    assert serialize_entity_resolutions(out["entity_resolutions"])["room_type"] == {
        "resolution": "UNRESOLVED"}


def test_production_unknown_text_does_not_identify_an_entity():
    facts = {field.name: None for field in compiled().fields}
    mentions = {field.name: False for field in compiled().fields}
    out = _production(facts, mentions)
    assert serialize_entity_resolutions(out["entity_resolutions"]) == {}


@pytest.mark.parametrize("bad_facts", [[], "bad", True])
def test_production_malformed_facts_container_is_contract_failure(bad_facts):
    with pytest.raises(EntityExtractionValidationError):
        _production(bad_facts, {field.name: False for field in compiled().fields})


@pytest.mark.parametrize("bad_value", [["diesel"], {"value": "diesel"}, True])
def test_production_malformed_entity_value_is_contract_failure(bad_value):
    facts = {field.name: None for field in compiled().fields}
    mentions = {field.name: False for field in compiled().fields}
    facts["engine_type"] = bad_value
    mentions["engine_type"] = True
    with pytest.raises(EntityExtractionValidationError):
        _production(facts, mentions)


def test_contradictory_generated_fact_and_mention_remains_contract_failure():
    facts = {field.name: None for field in compiled().fields}
    mentions = {field.name: False for field in compiled().fields}
    facts["room_type"] = "King Room"

    with pytest.raises(
        EntityExtractionValidationError,
        match="facts.room_type has a value while entity_mentions is false",
    ):
        _production(facts, mentions)


def test_production_many_aliases_one_canonical_has_consistent_projection():
    schema = compile_business_entities({"fields": [{
        "name": "room_type", "type": "catalog", "role": "bookable_item",
        "catalog": {"King Room": "same_room", "King Suite": "same_room"},
    }]})
    out = _production({"room_type": "king"}, {"room_type": True}, schema)
    assert serialize_entity_resolutions(out["entity_resolutions"]) == {
        "room_type": {"resolution": "RESOLVED", "value": "same_room"}}
    assert out["facts"]["service_id"] == "king room"
    assert out["service_candidates"] == []


def test_production_distinct_canonicals_are_ambiguous_and_project_candidates():
    out = _production(
        {"room_type": "king", "staff": None, "engine_type": None,
         "vehicle_details": None},
        {"room_type": True, "staff": False, "engine_type": False,
         "vehicle_details": False},
    )
    resolutions = serialize_entity_resolutions(out["entity_resolutions"])
    assert resolutions["room_type"]["resolution"] == "AMBIGUOUS"
    assert resolutions["room_type"]["candidate_values"] == ["room_king", "room_suite"]
    assert out["facts"]["service_id"] is None
    assert out["service_candidates"] == ["king room", "king suite"]


def test_production_mixed_siblings_and_projection_invariants():
    out = _production(
        {"room_type": "King Room", "staff": "nobody", "engine_type": "diesel",
         "vehicle_details": None},
        {"room_type": True, "staff": True, "engine_type": True,
         "vehicle_details": False},
    )
    resolutions = serialize_entity_resolutions(out["entity_resolutions"])
    assert resolutions["room_type"] == {"resolution": "RESOLVED", "value": "room_king"}
    assert resolutions["staff"] == {"resolution": "UNRESOLVED"}
    assert resolutions["engine_type"] == {"resolution": "RESOLVED", "value": "diesel"}
    assert out["facts"]["room_type"] == "King Room"
    assert out["facts"]["service_id"] == "king room"
    assert out["facts"]["staff_id"] is None
    assert out["facts"]["engine_type"] == "diesel"
    assert out["service_candidates"] == []
