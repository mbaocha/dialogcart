from core.catalogue import (
    apply_catalogue_turn,
    build_presentation,
    derive_service_catalogue,
    is_valid_presentation,
    resolve_presented_selection,
)


SERVICES = [
    {"id": "1001", "name": "Premium Haircut", "description": "Wash and styling", "category": "Hair"},
    {"id": "1002", "name": "Hair Colouring", "category": " hair "},
    {"id": "2001", "name": "Manicure", "category": "Nails"},
]


def test_categories_are_derived_case_insensitively_with_first_label():
    catalogue = derive_service_catalogue(SERVICES)
    assert catalogue.category_first is True
    assert [(group.key, group.label) for group in catalogue.categories] == [
        ("hair", "Hair"), ("nails", "Nails")
    ]
    assert [service.id for service in catalogue.category("HAIR").services] == ["1001", "1002"]


def test_flat_and_mixed_catalogues_keep_flat_discovery():
    flat = derive_service_catalogue([{"id": 1, "name": "Cut"}])
    mixed = derive_service_catalogue([*SERVICES, {"id": 4, "name": "Consultation"}])
    assert flat.category_first is False
    assert mixed.category_first is False
    assert flat.categories == mixed.categories == ()


def test_inactive_blank_and_all_inactive_services_do_not_enable_categories():
    catalogue = derive_service_catalogue([
        {"id": 1, "name": "Cut", "category": "Hair"},
        {"id": 2, "name": "Retired", "category": "Nails", "is_active": False},
    ])
    assert catalogue.category_first is True
    assert [service.id for service in catalogue.services] == ["1"]
    assert derive_service_catalogue([
        {"id": 1, "name": "Cut", "category": "   "}
    ]).category_first is False
    assert derive_service_catalogue([
        {"id": 1, "name": "Cut", "category": "Hair", "is_active": False}
    ]).category_first is False


def test_fingerprint_tracks_trusted_content_and_semantic_option_order():
    original = derive_service_catalogue(SERVICES)
    assert derive_service_catalogue(list(SERVICES)).fingerprint == original.fingerprint
    assert derive_service_catalogue(list(reversed(SERVICES))).fingerprint != original.fingerprint
    changed_description = [dict(item) for item in SERVICES]
    changed_description[0]["description"] = "Changed evidence"
    assert derive_service_catalogue(changed_description).fingerprint != original.fingerprint


def test_category_evidence_filters_services_without_filling_service_id():
    catalogue = derive_service_catalogue(SERVICES)
    resolved = apply_catalogue_turn(
        {"intent": {"name": "AVAILABILITY"}, "facts": {"service_id": None},
         "service_category": {"name": "Hair", "resolution": "RESOLVED"}},
        catalogue=catalogue, session=None,
    )
    assert resolved["facts"]["service_id"] is None
    assert resolved["service_candidates"] == ["Premium Haircut", "Hair Colouring"]
    assert resolved["_catalogue_presentation"]["kind"] == "service"


def test_no_service_presents_categories_but_flat_catalogue_does_not():
    response = {"intent": {"name": "CREATE_APPOINTMENT"}, "facts": {"service_id": None}}
    categorized = apply_catalogue_turn(response, catalogue=derive_service_catalogue(SERVICES), session=None)
    flat = apply_catalogue_turn(response, catalogue=derive_service_catalogue([{"id": 1, "name": "Cut"}]), session=None)
    assert categorized["service_candidates"] == ["Hair", "Nails"]
    assert "_catalogue_presentation" not in flat


def test_explicit_list_operation_presents_categories_without_service_or_availability():
    catalogue = derive_service_catalogue(SERVICES)
    resolved = apply_catalogue_turn(
        {
            "intent": {"name": "GENERAL_INQUIRY"},
            "operation": "list_service_categories",
            "facts": {"service_id": None, "dates": []},
        },
        catalogue=catalogue,
        session=None,
    )
    assert resolved["service_candidates"] == ["Hair", "Nails"]
    assert resolved["facts"]["service_id"] is None
    assert resolved["_catalogue_presentation"]["kind"] == "category"


def test_presented_ordinal_requires_exact_ref_kind_range_and_catalogue():
    catalogue = derive_service_catalogue(SERVICES)
    presentation = build_presentation(catalogue, kind="category")
    valid = {"presentation_ref": presentation["reference"], "kind": "category", "option": 2}
    assert resolve_presented_selection(valid, presentation=presentation, catalogue=catalogue) == {"kind": "category", "id": "nails"}
    for invalid in (
        {**valid, "presentation_ref": "cp_stale"},
        {**valid, "kind": "service"},
        {**valid, "option": 0},
        {**valid, "option": 99},
        {**valid, "option": "2"},
        {**valid, "option": True},
        {**valid, "option": -1},
        {"kind": "category", "option": 1},
        {"presentation_ref": presentation["reference"], "option": 1},
    ):
        assert resolve_presented_selection(invalid, presentation=presentation, catalogue=catalogue) is None


def test_changed_catalogue_invalidates_presentation():
    catalogue = derive_service_catalogue(SERVICES)
    changed = derive_service_catalogue(SERVICES[:-1])
    presentation = build_presentation(catalogue, kind="category")
    selection = {"presentation_ref": presentation["reference"], "kind": "category", "option": 1}
    assert resolve_presented_selection(selection, presentation=presentation, catalogue=changed) is None


def test_malformed_reordered_and_duplicate_presentations_fail_closed():
    catalogue = derive_service_catalogue(SERVICES)
    presentation = build_presentation(catalogue, kind="service")
    selection = {"presentation_ref": presentation["reference"], "kind": "service", "option": 1}
    assert is_valid_presentation(presentation, catalogue=catalogue)
    malformed_states = [
        None,
        {**presentation, "kind": "unknown"},
        {**presentation, "options": list(reversed(presentation["options"]))},
        {**presentation, "options": [presentation["options"][0], presentation["options"][0]]},
        {**presentation, "options": [{**presentation["options"][0], "label": "Renamed"}]},
    ]
    for malformed in malformed_states:
        assert not is_valid_presentation(malformed, catalogue=catalogue)
        assert resolve_presented_selection(
            selection, presentation=malformed, catalogue=catalogue
        ) is None


def test_valid_service_ordinal_promotes_only_canonical_service_id():
    catalogue = derive_service_catalogue(SERVICES)
    presentation = build_presentation(
        catalogue, kind="service", services=catalogue.category("hair").services
    )
    resolved = apply_catalogue_turn(
        {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {"service_id": None},
            "catalog_selection": {
                "presentation_ref": presentation["reference"],
                "kind": "service",
                "option": 2,
            },
        },
        catalogue=catalogue,
        session={"catalogue_presentation": presentation},
    )
    assert resolved["facts"] == {"service_id": "1002"}
    assert resolved["_catalogue_presentation"] is None


def test_direct_service_clears_presentation_and_category_is_not_a_slot():
    catalogue = derive_service_catalogue(SERVICES)
    resolved = apply_catalogue_turn(
        {"intent": {"name": "CREATE_APPOINTMENT"}, "facts": {"service_id": 2001}},
        catalogue=catalogue,
        session={"catalogue_presentation": build_presentation(catalogue, kind="category")},
    )
    assert resolved["facts"] == {"service_id": 2001}
    assert resolved["_catalogue_presentation"] is None


def test_duplicate_normalized_service_name_fails_closed():
    catalogue = derive_service_catalogue([
        {"id": 1, "name": "Cut", "category": "Hair"},
        {"id": 2, "name": " cut ", "category": "Nails"},
    ])
    resolved = apply_catalogue_turn(
        {"intent": {"name": "CREATE_APPOINTMENT"}, "facts": {"service_id": 1}},
        catalogue=catalogue, session=None,
    )
    assert resolved["facts"]["service_id"] is None
    assert resolved["service_candidates"] == ["Cut", "cut"]
