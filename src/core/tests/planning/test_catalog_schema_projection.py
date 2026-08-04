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
    assert "Oil Change" in by_name["service"]["catalog"]
    assert "John" in by_name["staff"]["catalog"]
    assert by_name["engine_type"]["type"] == "enum"
    assert by_name["registration_number"]["type"] == "text"
