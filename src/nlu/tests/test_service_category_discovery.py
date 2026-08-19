from nlu.entity_resolution import EntityMentionEvidence, MentionState, serialize_entity_resolutions
from nlu.pipeline import NLUPipeline, _apply_service_catalogue_evidence
from nlu.stages.stage2.entity_schema import compile_business_entities


TENANT_CONTEXT = {
    "catalog": {
        "services": [
            {"id": "1001", "name": "Premium Haircut", "category": "Hair"},
            {"id": "1002", "name": "Hair Colouring", "category": " hair "},
            {"id": "2001", "name": "Manicure", "category": "Nails"},
        ]
    }
}


def test_exact_category_name_emits_semantic_evidence_without_service_id():
    result = _apply_service_catalogue_evidence(
        "Show Hair availability tomorrow",
        {"intent": "AVAILABILITY", "facts": {"service_id": None}},
        TENANT_CONTEXT,
        None,
    )
    assert result["service_category"] == {
        "name": "Hair", "resolution": "RESOLVED"
    }
    assert result["facts"]["service_id"] is None


def test_exact_category_name_removes_conflicting_model_service_evidence():
    result = _apply_service_catalogue_evidence(
        "Hair",
        {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service": "Hair", "service_id": "hair colouring"},
            "entity_resolutions": {
                "service": {"resolution": "RESOLVED", "value": "1002"}
            },
        },
        TENANT_CONTEXT,
        None,
    )
    assert result["service_category"] == {
        "name": "Hair", "resolution": "RESOLVED"
    }
    assert result["facts"]["service"] is None
    assert result["facts"]["service_id"] is None
    assert "service" not in result["entity_resolutions"]


def test_explicit_category_listing_is_an_operation_not_availability_reclassification():
    for text in (
        "What service categories do you have?",
        "What types of services do you offer?",
        "What services do you offer?",
    ):
        result = _apply_service_catalogue_evidence(
            text,
            {"intent": "GENERAL_INQUIRY", "facts": {"service_id": None}},
            TENANT_CONTEXT,
            None,
        )
        assert result["operation"] == "list_service_categories"
        assert result["intent"] == "GENERAL_INQUIRY"


def test_generic_offer_question_does_not_fabricate_catalogue_semantics():
    result = _apply_service_catalogue_evidence(
        "What do you offer?",
        {"intent": "GENERAL_INQUIRY", "facts": {"service_id": None}},
        TENANT_CONTEXT,
        None,
    )
    assert "operation" not in result
    assert "service_category" not in result


def test_ordinal_requires_current_structured_presentation_context():
    presentation = {
        "reference": "cp_123",
        "kind": "category",
        "options": [{"index": 1, "id": "hair", "label": "Hair"}],
    }
    selected = _apply_service_catalogue_evidence(
        "first",
        {"intent": "CREATE_APPOINTMENT", "facts": {"service_id": None}},
        TENANT_CONTEXT,
        {"catalogue_presentation": presentation},
    )
    assert selected["catalog_selection"] == {
        "presentation_ref": "cp_123", "kind": "category", "option": 1
    }
    absent = _apply_service_catalogue_evidence(
        "first",
        {"intent": "CREATE_APPOINTMENT", "facts": {"service_id": None}},
        TENANT_CONTEXT,
        None,
    )
    assert "catalog_selection" not in absent


def test_natural_ordinal_prefers_catalogue_presentation_over_temporal_option():
    presentation = {
        "reference": "cp_123",
        "kind": "category",
        "options": [{"index": 1, "id": "hair", "label": "Hair"}],
    }
    selected = _apply_service_catalogue_evidence(
        "the first one",
        {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service": None, "service_id": None},
            "temporal": {
                "mode": "none",
                "start_time": None,
                "end_time": None,
                "resolution": {"kind": "invalid_option_reference"},
            },
        },
        TENANT_CONTEXT,
        {"catalogue_presentation": presentation},
    )
    assert selected["catalog_selection"] == {
        "presentation_ref": "cp_123", "kind": "category", "option": 1
    }
    assert selected["temporal"]["resolution"] is None


def test_service_ordinal_projects_authoritative_canonical_resolution():
    presentation = {
        "reference": "cp_services",
        "kind": "service",
        "options": [
            {"index": 1, "id": "1001", "label": "Premium Haircut"},
            {"index": 2, "id": "1002", "label": "Hair Colouring"},
        ],
    }
    selected = _apply_service_catalogue_evidence(
        "the second one",
        {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service": None, "service_id": None},
            "entity_resolutions": {},
        },
        {
            **TENANT_CONTEXT,
            "aliases": {
                "premium haircut": 1001,
                "hair colouring": 1002,
                "manicure": 2001,
            },
        },
        {"catalogue_presentation": presentation},
    )
    assert selected["catalog_selection"] == {
        "presentation_ref": "cp_services", "kind": "service", "option": 2
    }
    assert selected["facts"]["service"] == "Hair Colouring"
    assert selected["facts"]["service_id"] == 1002
    assert selected["entity_resolutions"]["service"].model_dump(mode="json") == {
        "resolution": "RESOLVED", "value": 1002
    }


def test_explicit_service_outside_presented_category_uses_full_catalogue():
    schema = {
        "version": 1,
        "fields": [{
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "catalog": {
                "Premium Haircut": "1001",
                "Hair Colouring": "1002",
                "Manicure": "2001",
            },
        }],
    }
    compiled = compile_business_entities(schema)
    slm = {
        "intent": "CORRECTION",
        "facts": {"service": "Manicure", "service_id": None},
        "_entity_mentions": {
            "service": EntityMentionEvidence(
                entity_name="service",
                state=MentionState.MENTIONED_VALUE,
                raw_value="Manicure",
            )
        },
    }
    result = NLUPipeline()._resolve_schema_entities(
        slm,
        {
            "missing_slots": ["service_id"],
            "service_candidates": ["Premium Haircut", "Hair Colouring"],
        },
        compiled,
        text="Actually, manicure",
    )
    assert serialize_entity_resolutions(result["entity_resolutions"])["service"] == {
        "resolution": "RESOLVED", "value": "2001"
    }
