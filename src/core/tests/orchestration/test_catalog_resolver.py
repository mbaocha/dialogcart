"""Tests for SKU → catalog item id resolution at execution boundary."""

from core.orchestration.catalog_resolver import (
    build_sku_to_catalog_id,
    resolve_catalog_item_id,
)


class TestBuildSkuToCatalogId:
    def test_service_domain_maps_name_to_id(self):
        catalog = {
            "services": [
                {"name": "Premium Haircut", "id": 1001, "is_active": True},
                {"name": "Flexi Haircut", "id": 1002, "is_active": True},
                {"name": "Inactive", "id": 999, "is_active": False},
            ]
        }
        assert build_sku_to_catalog_id(catalog, "service") == {
            "premium haircut": 1001,
            "flexi haircut": 1002,
        }

    def test_reservation_rooms(self):
        catalog = {
            "room_types": [
                {"name": "Deluxe Suite", "id": 55, "is_active": True},
            ]
        }
        assert build_sku_to_catalog_id(catalog, "reservation") == {
            "deluxe suite": 55,
        }


class TestResolveCatalogItemId:
    def test_resolves_sku_string(self):
        sku_map = {"premium haircut": 1001}
        assert resolve_catalog_item_id("premium haircut", sku_map) == 1001
        assert resolve_catalog_item_id("Premium Haircut", sku_map) == 1001

    def test_passthrough_int(self):
        assert resolve_catalog_item_id(42, {}) == 42
        assert resolve_catalog_item_id("42", {}) == 42

    def test_unmapped_returns_none(self):
        assert resolve_catalog_item_id("unknown service", {"haircut": 1}) is None
