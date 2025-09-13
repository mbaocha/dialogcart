import pytest
from unittest.mock import patch, MagicMock
from decimal import Decimal

from db.order import OrderDB

@pytest.fixture
def mock_table():
    with patch("boto3.resource") as mock_resource:
        mock_table = MagicMock()
        mock_resource.return_value.Table.return_value = mock_table
        yield mock_table

def test_create_order(mock_table):
    db = OrderDB()
    mock_table.put_item.return_value = {}

    items = [
        {"product_id": "prod-1", "quantity": 2, "price": 10.00},
        {"product_id": "prod-2", "quantity": 1, "price": 15.00}
    ]
    
    item = db.create_order(
        user_id="user-123",
        items=items,
        total_amount=35.00,
        status="pending",
        address={"address_line1": "123 Main St", "city": "London"},
        payment_status="pending"
    )
    assert item["user_id"] == "user-123"
    assert item["items"] == items
    assert item["total_amount"] == Decimal("35.00")
    assert item["status"] == "pending"
    assert item["address"]["address_line1"] == "123 Main St"
    assert item["payment_status"] == "pending"
    mock_table.put_item.assert_called_once()

def test_get_order(mock_table):
    db = OrderDB()
    order_id = "order-123"
    fake_order = {
        "order_id": order_id,
        "user_id": "user-456",
        "total_amount": Decimal("50.00"),
        "status": "completed"
    }
    mock_table.get_item.return_value = {"Item": fake_order}

    result = db.get_order(order_id)
    assert result == fake_order
    mock_table.get_item.assert_called_once_with(Key={"order_id": order_id})

def test_get_order_not_found(mock_table):
    db = OrderDB()
    order_id = "order-999"
    mock_table.get_item.return_value = {}

    result = db.get_order(order_id)
    assert result is None

def test_list_orders(mock_table):
    db = OrderDB()
    mock_table.scan.return_value = {
        "Items": [
            {"order_id": "order-1", "user_id": "user-1", "status": "pending"},
            {"order_id": "order-2", "user_id": "user-2", "status": "completed"},
            {"order_id": "order-3", "user_id": "user-1", "status": "pending"}
        ]
    }
    
    results = db.list_orders()
    assert len(results) == 3
    assert all("order_id" in r for r in results)
    mock_table.scan.assert_called_once()

def test_list_orders_with_user_filter(mock_table):
    db = OrderDB()
    user_id = "user-123"
    mock_table.scan.return_value = {
        "Items": [
            {"order_id": "order-1", "user_id": user_id, "status": "pending"},
            {"order_id": "order-2", "user_id": user_id, "status": "completed"}
        ]
    }
    
    results = db.list_orders(user_id=user_id)
    assert len(results) == 2
    assert all(r["user_id"] == user_id for r in results)

def test_update_status(mock_table):
    db = OrderDB()
    order_id = "order-123"
    new_status = "completed"
    mock_table.update_item.return_value = {"Attributes": {"status": new_status}}

    result = db.update_status(order_id, new_status)
    assert result is True
    mock_table.update_item.assert_called_once()

def test_update_status_failure(mock_table):
    db = OrderDB()
    order_id = "order-999"
    mock_table.update_item.side_effect = Exception("Item not found")

    result = db.update_status(order_id, "completed")
    assert result is False 