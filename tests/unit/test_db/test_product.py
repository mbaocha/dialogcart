import pytest
from unittest.mock import patch, MagicMock
from decimal import Decimal

from db.catalog import ProductDB
from db.enums import ProductUnit

@pytest.fixture
def mock_table():
    with patch("boto3.resource") as mock_resource:
        mock_table = MagicMock()
        mock_resource.return_value.Table.return_value = mock_table
        yield mock_table

def test_create_product_allowed_quantities_dict(mock_table):
    db = ProductDB()
    mock_table.put_item.return_value = {}

    item = db.create_product(
        name="Yam",
        unit=ProductUnit.KG.value,
        price=10.0,
        allowed_quantities={"min": 2},
        available_quantity=100,
        description="Fresh yam"
    )
    assert item["name"] == "Yam"
    assert item["unit"] == "kg"
    assert item["price"] == Decimal("10.0")
    assert item["allowed_quantities"] == {"min": 2}
    assert item["available_quantity"] == Decimal("100")
    assert item["description"] == "Fresh yam"
    mock_table.put_item.assert_called_once()

def test_create_product_allowed_quantities_list(mock_table):
    db = ProductDB()
    mock_table.put_item.return_value = {}

    item = db.create_product(
        name="Caribbean rice",
        unit=ProductUnit.KG.value,
        price=8.5,
        allowed_quantities=[25, 50, 75],
        description="Big bags"
    )
    assert item["name"] == "Caribbean rice"
    assert item["unit"] == "kg"
    assert item["price"] == Decimal("8.5")
    assert item["allowed_quantities"] == [25, 50, 75]
    assert item["description"] == "Big bags"
    mock_table.put_item.assert_called_once()

def test_create_product_default_allowed_quantities(mock_table):
    db = ProductDB()
    mock_table.put_item.return_value = {}

    # If allowed_quantities not provided, should default to {"min": 1}
    item = db.create_product(
        name="Plantain",
        unit=ProductUnit.BUNCH.value,
        price=3.0
    )
    assert item["allowed_quantities"] == {"min": 1}
    assert item["price"] == Decimal("3.0")
    mock_table.put_item.assert_called_once()

def test_create_product_invalid_unit(mock_table):
    db = ProductDB()
    with pytest.raises(ValueError):
        db.create_product(
            name="Tomato",
            unit="bucket",  # not in ProductUnit
            price=5.0
        )

def test_get_product(mock_table):
    db = ProductDB()
    product_id = "prod-123"
    fake_product = {"id": product_id, "name": "Eggs", "unit": "dozen"}
    mock_table.get_item.return_value = {"Item": fake_product}

    result = db.get_product(product_id)
    assert result == fake_product
    mock_table.get_item.assert_called_once_with(Key={"id": product_id})

def test_search_products_found(mock_table):
    db = ProductDB()
    # Table scan returns 3 items, 2 match
    mock_table.scan.return_value = {
        "Items": [
            {"name": "Red Onions"},
            {"name": "White Onions"},
            {"name": "Potatoes"}
        ]
    }
    results = db.search_products("onion")
    assert len(results) == 2
    assert all("Onions" in r["name"] for r in results)
