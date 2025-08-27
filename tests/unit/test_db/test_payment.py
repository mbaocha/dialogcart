import pytest
from unittest.mock import patch, MagicMock
from decimal import Decimal

from db.payment import PaymentDB

@pytest.fixture
def mock_table():
    with patch("boto3.resource") as mock_resource:
        mock_table = MagicMock()
        mock_resource.return_value.Table.return_value = mock_table
        yield mock_table

def test_create_payment(mock_table):
    db = PaymentDB()
    mock_table.put_item.return_value = {}

    item = db.create_payment(
        order_id="order-123",
        user_id="user-456",
        amount=25.50,
        currency="GBP",
        method="card",
        provider="Stripe",
        status="pending",
        reference="ref-789"
    )
    assert item["order_id"] == "order-123"
    assert item["user_id"] == "user-456"
    assert item["amount"] == Decimal("25.50")
    assert item["currency"] == "GBP"
    assert item["method"] == "card"
    assert item["provider"] == "Stripe"
    assert item["status"] == "pending"
    assert item["reference"] == "ref-789"
    mock_table.put_item.assert_called_once()

def test_get_payment(mock_table):
    db = PaymentDB()
    payment_id = "payment-123"
    fake_payment = {
        "payment_id": payment_id,
        "order_id": "order-456",
        "amount": Decimal("30.00"),
        "status": "completed"
    }
    mock_table.get_item.return_value = {"Item": fake_payment}

    result = db.get_payment(payment_id)
    assert result == fake_payment
    mock_table.get_item.assert_called_once_with(Key={"payment_id": payment_id})

def test_get_payment_not_found(mock_table):
    db = PaymentDB()
    payment_id = "payment-999"
    mock_table.get_item.return_value = {}

    result = db.get_payment(payment_id)
    assert result is None

def test_list_payments(mock_table):
    db = PaymentDB()
    mock_table.scan.return_value = {
        "Items": [
            {"payment_id": "payment-1", "amount": Decimal("25.00"), "status": "pending"},
            {"payment_id": "payment-2", "amount": Decimal("30.00"), "status": "completed"},
            {"payment_id": "payment-3", "amount": Decimal("15.00"), "status": "pending"}
        ]
    }
    
    results = db.list_payments()
    assert len(results) == 3
    assert all("payment_id" in r for r in results)
    mock_table.scan.assert_called_once()

def test_list_payments_with_user_filter(mock_table):
    db = PaymentDB()
    user_id = "user-123"
    mock_table.scan.return_value = {
        "Items": [
            {"payment_id": "payment-1", "user_id": user_id, "amount": Decimal("25.00")},
            {"payment_id": "payment-2", "user_id": user_id, "amount": Decimal("30.00")}
        ]
    }
    
    results = db.list_payments(user_id=user_id)
    assert len(results) == 2
    assert all(r["user_id"] == user_id for r in results)

def test_update_status(mock_table):
    db = PaymentDB()
    payment_id = "payment-123"
    new_status = "completed"
    mock_table.update_item.return_value = {"Attributes": {"status": new_status}}

    result = db.update_status(
        payment_id=payment_id,
        status=new_status,
        reference="ref-456",
        paid_at="2024-01-01T12:00:00Z"
    )
    assert result is True
    mock_table.update_item.assert_called_once()

def test_update_status_failure(mock_table):
    db = PaymentDB()
    payment_id = "payment-999"
    mock_table.update_item.side_effect = Exception("Item not found")

    result = db.update_status(payment_id=payment_id, status="completed")
    assert result is False 