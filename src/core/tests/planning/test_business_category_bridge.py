"""Focused tests: business category owns schema; booking_domain owns workflow."""

from core.adapters.cache.org_domain_cache import (
    BUSINESS_CATEGORY_IDS,
    OrgDomainCache,
)
from core.adapters.nlu.entity_schema_builder import (
    build_entity_schema,
    required_slot_keys_from_entity_schema,
    search_criteria_slot_keys_from_entity_schema,
)
from core.config.business_category_loader import (
    clear_business_category_cache,
    get_booking_domain,
    get_category_entities,
    get_configured_categories,
    is_configured_category,
)
from core.planning.planner.missing_slots import compose_planning_required_slots
from core.planning.booking_revision import detect_booking_revision
from core.policy.intent_policy import select_next_execution_step


def setup_function(_fn=None):
    clear_business_category_cache()


def test_configured_categories_include_salon_car_hotel():
    cats = get_configured_categories()
    assert "beauty_salon" in cats
    assert "car_service" in cats
    assert "hotel" in cats


def test_booking_domain_metadata_on_categories():
    assert get_booking_domain("beauty_salon") == "service"
    assert get_booking_domain("car_service") == "service"
    assert get_booking_domain("hotel") == "reservation"


def test_beauty_salon_entities_match_legacy_service_shape():
    entities = get_category_entities("beauty_salon")
    names = [e.get("name") for e in entities]
    assert names == ["service"]
    assert entities[0].get("catalog") == "services"


def test_car_service_has_required_business_entities():
    entities = get_category_entities("car_service")
    by_name = {e["name"]: e for e in entities}
    assert by_name["engine_type"].get("required") is True
    assert by_name["registration_number"].get("required") is True
    assert by_name["staff"].get("role") == "staff"
    assert by_name["staff"].get("required") is not True


def test_org_resolves_category_then_booking_domain():
    cache = OrgDomainCache()

    class _OrgClient:
        def get_details(self, _org_id):
            return {"organization": {"businessCategoryId": 1}}

    category, booking_domain, cat_id = cache.resolve(99, _OrgClient())
    assert category == "beauty_salon"
    assert booking_domain == "service"
    assert cat_id == 1

    # get_domain remains booking_domain for workflow/catalog callers
    domain, returned_id = cache.get_domain(99, _OrgClient())
    assert domain == "service"
    assert returned_id == 1


def test_org_resolves_car_service_category():
    cache = OrgDomainCache()

    class _OrgClient:
        def get_details(self, _org_id):
            return {"organization": {"businessCategoryId": 3}}

    category, booking_domain, cat_id = cache.resolve(77, _OrgClient())
    assert category == "car_service"
    assert booking_domain == "service"
    assert cat_id == 3
    assert BUSINESS_CATEGORY_IDS[3] == "car_service"


def test_org_resolves_hotel_category():
    cache = OrgDomainCache()

    class _OrgClient:
        def get_details(self, _org_id):
            return {"organization": {"businessCategoryId": 2}}

    category, booking_domain, _ = cache.resolve(55, _OrgClient())
    assert category == "hotel"
    assert booking_domain == "reservation"


def test_entity_schema_from_business_category():
    projected = {
        "services": {"Oil Change": "oil-change", "Full Service": "full-service"},
        "staff": {"John": "staff-1", "Mike": "staff-2"},
    }
    schema = build_entity_schema("car_service", projected_collections=projected)
    assert schema is not None
    names = [f["name"] for f in schema["fields"]]
    assert "service" in names
    assert "engine_type" in names
    assert "registration_number" in names
    assert "staff" in names
    # Catalog phrase maps projected
    service_field = next(f for f in schema["fields"] if f["name"] == "service")
    assert "Oil Change" in service_field["catalog"]


def test_beauty_salon_entity_schema_unchanged_shape():
    projected = {"services": {"Premium Haircut": "premium haircut"}}
    schema = build_entity_schema("beauty_salon", projected_collections=projected)
    assert schema is not None
    assert [f["name"] for f in schema["fields"]] == ["service"]
    assert required_slot_keys_from_entity_schema(schema) == []


def test_car_planning_requiredness_composed():
    projected = {
        "services": {"Oil Change": "oil-change"},
        "staff": {"John": "staff-1"},
    }
    schema = build_entity_schema("car_service", projected_collections=projected)
    required = compose_planning_required_slots(
        "CREATE_APPOINTMENT", entity_schema=schema
    )
    assert required[:3] == ["service_id", "date", "time"]
    assert "engine_type" in required
    assert "registration_number" in required
    assert "staff_id" not in required  # optional


def test_car_committing_execution_uses_composed_requiredness():
    projected = {
        "services": {"Oil Change": "oil-change"},
        "staff": {"John": "staff-1"},
    }
    schema = build_entity_schema("car_service", projected_collections=projected)
    flags = {
        "availability_ready": True,
        "time_selection_ready": True,
        "user_confirmation_satisfied": True,
    }
    incomplete = select_next_execution_step(
        "CREATE_APPOINTMENT",
        {"service_id": "oil-change", "date": "2026-07-02", "time": "10:00"},
        flags,
        entity_schema=schema,
    )
    assert incomplete is None

    complete = select_next_execution_step(
        "CREATE_APPOINTMENT",
        {
            "service_id": "oil-change",
            "date": "2026-07-02",
            "time": "10:00",
            "engine_type": "diesel",
            "registration_number": "AB12CDE",
        },
        flags,
        entity_schema=schema,
    )
    assert complete is not None
    assert complete["action"] == "CONFIRM_APPOINTMENT"


def test_car_staff_revision_invalidates_availability_not_attributes():
    projected = {
        "services": {"Oil Change": "oil-change"},
        "staff": {"John": "staff-1", "Mike": "staff-2"},
    }
    schema = build_entity_schema("car_service", projected_collections=projected)
    assert "staff_id" in search_criteria_slot_keys_from_entity_schema(schema)
    # car_service YAML marks engine_type as availability_criteria
    assert "engine_type" in search_criteria_slot_keys_from_entity_schema(schema)
    assert "registration_number" not in search_criteria_slot_keys_from_entity_schema(
        schema
    )

    session = {
        "slots": {
            "service_id": "oil-change",
            "staff_id": "staff-1",
            "engine_type": "diesel",
        }
    }
    staff_rev = detect_booking_revision(
        {
            "facts": {"staff_id": "staff-2"},
            "slots": {"staff_id": "staff-2"},
            "_entity_schema": schema,
        },
        session,
        entity_schema=schema,
    )
    assert staff_rev.criteria is True
    assert staff_rev.invalidates_availability is True

    attr_rev = detect_booking_revision(
        {
            "facts": {"registration_number": "ZZ99ZZZ"},
            "slots": {"registration_number": "ZZ99ZZZ"},
            "_entity_schema": schema,
        },
        session,
        entity_schema=schema,
    )
    assert attr_rev.any is False

    engine_rev = detect_booking_revision(
        {
            "facts": {"engine_type": "petrol"},
            "slots": {"engine_type": "petrol"},
            "_entity_schema": schema,
        },
        {**session, "slots": {**session["slots"], "engine_type": "diesel"}},
        entity_schema=schema,
    )
    assert engine_rev.criteria is True
    assert engine_rev.invalidates_availability is True


def test_is_configured_category_rejects_booking_domain_keys():
    """Booking domains are no longer schema lookup keys."""
    assert is_configured_category("beauty_salon")
    assert not is_configured_category("service")
    assert not is_configured_category("reservation")
