"""Schema-driven catalog projection (no hardcoded collection names)."""

from __future__ import annotations

from core.adapters.cache.catalog_cache import CatalogCache
from core.adapters.nlu.catalog_projection import project_catalog_collections
from core.adapters.nlu.entity_schema_builder import build_entity_schema
from core.config.business_category_loader import (
    clear_business_category_cache,
    get_catalog_collection_keys,
)
from core.tests.harness.car_service_catalog import (
    CAR_SERVICE_COLLECTIONS,
    CAR_SERVICE_SERVICES,
)
from core.tests.harness.clients import TestCatalogClient
from core.planning.planner.turn_planner import build_nlu_request_context


def setup_function(_fn=None):
    clear_business_category_cache()


def test_catalog_keys_from_business_schema_not_hardcoded():
    assert get_catalog_collection_keys("beauty_salon") == ["services"]
    assert get_catalog_collection_keys("car_service") == ["services", "staff"]
    assert get_catalog_collection_keys("hotel") == ["room_types"]


def test_project_only_schema_referenced_collections():
    catalog_data = {
        "services": [
            {"name": "Oil Change", "id": 101, "is_active": True},
        ],
        "staff": [
            {"name": "John", "id": 201, "is_active": True},
        ],
        "ignored_extra": [
            {"name": "X", "id": 9, "is_active": True},
        ],
    }
    projected = project_catalog_collections(
        catalog_data,
        collection_keys=get_catalog_collection_keys("car_service"),
    )
    assert set(projected.keys()) == {"services", "staff"}
    assert "Oil Change" in projected["services"]
    assert "John" in projected["staff"]
    assert "ignored_extra" not in projected


def test_beauty_salon_projection_excludes_staff_even_if_present():
    catalog_data = {
        "services": [{"name": "Cut", "id": 1, "is_active": True}],
        "staff": [{"name": "Alex", "id": 2, "is_active": True}],
    }
    projected = project_catalog_collections(
        catalog_data,
        collection_keys=get_catalog_collection_keys("beauty_salon"),
    )
    assert set(projected.keys()) == {"services"}
    assert "staff" not in projected


def test_catalog_cache_passes_through_staff_collection():
    client = TestCatalogClient(
        test_aliases=CAR_SERVICE_SERVICES,
        collections=CAR_SERVICE_COLLECTIONS,
        business_category_id=3,
    )
    cache = CatalogCache()
    data = cache.get_catalog(42, client, domain="service", force_refresh=True)
    assert isinstance(data.get("services"), list) and data["services"]
    assert isinstance(data.get("staff"), list) and data["staff"]
    names = {item["name"] for item in data["staff"]}
    assert names == {"John", "Mike"}


def test_entity_schema_builds_staff_from_projected_catalog():
    client = TestCatalogClient(
        test_aliases=CAR_SERVICE_SERVICES,
        collections=CAR_SERVICE_COLLECTIONS,
        business_category_id=3,
    )
    cache = CatalogCache()
    data = cache.get_catalog(43, client, domain="service", force_refresh=True)
    projected = project_catalog_collections(
        data, collection_keys=get_catalog_collection_keys("car_service")
    )
    schema = build_entity_schema("car_service", projected_collections=projected)
    assert schema is not None
    by_name = {f["name"]: f for f in schema["fields"]}
    assert "executive oil change" in by_name["service"]["catalog"]
    assert "John" in by_name["staff"]["catalog"]
    assert by_name["engine_type"]["type"] == "enum"
    assert by_name["registration_number"]["type"] == "text"


def test_flat_catalogue_without_optional_metadata_preserves_request_shape():
    client = TestCatalogClient(service_records=[{"id": "1001", "name": "Cut"}])
    tenant_context, schema = build_nlu_request_context(
        9101, "beauty_salon", "service", client
    )
    assert tenant_context == {"booking_mode": "service", "aliases": {"cut": 1001}}
    assert "catalog" not in tenant_context
    service_field = next(field for field in schema["fields"] if field["name"] == "service")
    assert "items" not in service_field


def test_optional_description_and_category_are_additive_semantic_context():
    records = [
        {"id": "1001", "name": "Cut", "description": "Wash and styling", "category": "Hair"},
        {"id": "2001", "name": "Manicure", "category": "Nails"},
    ]
    client = TestCatalogClient(service_records=records)
    tenant_context, schema = build_nlu_request_context(
        9102, "beauty_salon", "service", client
    )
    assert tenant_context["catalog"]["services"] == records
    service_field = next(field for field in schema["fields"] if field["name"] == "service")
    assert service_field["items"] == [
        {"id": 1001, "name": "Cut", "description": "Wash and styling", "category": "Hair"},
        {"id": 2001, "name": "Manicure", "category": "Nails"},
    ]
    assert set(service_field["catalog"].values()) == {1001, 2001}


def test_pending_confirmation_schema_allows_authoritative_contact_name_revision():
    client = TestCatalogClient(service_records=[{"id": "1001", "name": "Cut"}])
    session = {
        "confirmation_state": "pending",
        "planning": {"pending_profile_request": None},
        "customer_id": 91,
        "customer_contact": {
            "customer_id": 91,
            "authoritative_name": "Godswill Mbaocha",
            "name_status": "authoritative",
        },
    }

    _, schema = build_nlu_request_context(
        9103, "beauty_salon", "service", client, session_state=session
    )

    contact_field = next(
        field for field in schema["fields"]
        if field["name"] == "customer_contact_name"
    )
    assert contact_field["type"] == "text"
