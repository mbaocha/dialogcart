import pytest
from unittest.mock import patch, MagicMock
from decimal import Decimal

from db.cart import CartDB

@pytest.fixture
def mock_table():
    with patch("boto3.resource") as mock_resource:
        mock_table = MagicMock()
        mock_resource.return_value.Table.return_value = mock_table
        yield mock_table

def test_add_item(mock_table):
    db = CartDB()
    mock_table.put_item.return_value = {}

    item = db.add_item(
        user_id="user-1",
        product_id="prod-1",
        quantity=3
    )
    assert item == {
        "user_id": "user-1",
        "product_id": "prod-1",
        "quantity": Decimal("3")
    }
    mock_table.put_item.assert_called_once_with(Item=item)

def test_remove_item_success(mock_table):
    db = CartDB()
    mock_table.delete_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}

    success = db.remove_item("user-1", "prod-2")
    assert success is True
    mock_table.delete_item.assert_called_once_with(Key={"user_id": "user-1", "product_id": "prod-2"})

def test_remove_item_failure(mock_table):
    db = CartDB()
    mock_table.delete_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 400}}

    success = db.remove_item("user-1", "prod-3")
    assert success is False

def test_update_quantity_success(mock_table):
    db = CartDB()
    mock_table.update_item.return_value = {"Attributes": {"quantity": Decimal("5")}}

    result = db.update_quantity("user-2", "prod-4", 5)
    assert result is True
    mock_table.update_item.assert_called_once_with(
        Key={"user_id": "user-2", "product_id": "prod-4"},
        UpdateExpression="set quantity = :q",
        ExpressionAttributeValues={":q": Decimal("5")},
        ReturnValues="UPDATED_NEW"
    )

def test_update_quantity_failure(mock_table):
    db = CartDB()
    mock_table.update_item.return_value = {}

    result = db.update_quantity("user-3", "prod-5", 7)
    assert result is False

def test_get_cart(mock_table):
    db = CartDB()
    mock_table.query.return_value = {
        "Items": [
            {"user_id": "user-4", "product_id": "prod-6", "quantity": Decimal("2")},
            {"user_id": "user-4", "product_id": "prod-7", "quantity": Decimal("4")}
        ]
    }
    result = db.get_cart("user-4")
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["product_id"] == "prod-6"
    mock_table.query.assert_called_once_with(
        KeyConditionExpression="user_id = :uid",
        ExpressionAttributeValues={":uid": "user-4"}
    )

def test_get_cart_empty(mock_table):
    db = CartDB()
    mock_table.query.return_value = {"Items": []}
    result = db.get_cart("user-5")
    assert result == []

def test_clear_cart(mock_table):
    db = CartDB()
    # Prepare 3 items in cart
    mock_table.query.return_value = {
        "Items": [
            {"user_id": "user-6", "product_id": "prod-8"},
            {"user_id": "user-6", "product_id": "prod-9"},
            {"user_id": "user-6", "product_id": "prod-10"},
        ]
    }
    mock_table.delete_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}

    removed_count = db.clear_cart("user-6")
    assert removed_count == 3
    # Should call delete_item three times (for three products)
    assert mock_table.delete_item.call_count == 3
