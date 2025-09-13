import pytest
from unittest.mock import patch, MagicMock

from db.address import AddressDB

@pytest.fixture
def mock_table():
    with patch("boto3.resource") as mock_resource:
        mock_table = MagicMock()
        mock_resource.return_value.Table.return_value = mock_table
        yield mock_table

def test_create_address(mock_table):
    db = AddressDB()
    mock_table.put_item.return_value = {}

    item = db.create_address(
        user_id="user-123",
        label="Home",
        address_line1="123 Main Street",
        city="London",
        state="England",
        country="UK",
        postal_code="SW1A 1AA",
        address_line2="Apt 4B",
        lat=51.5074,
        lon=-0.1278,
        is_default=True
    )
    assert item["user_id"] == "user-123"
    assert item["label"] == "Home"
    assert item["address_line1"] == "123 Main Street"
    assert item["city"] == "London"
    assert item["state"] == "England"
    assert item["country"] == "UK"
    assert item["postal_code"] == "SW1A 1AA"
    assert item["address_line2"] == "Apt 4B"
    assert item["lat"] == 51.5074
    assert item["lon"] == -0.1278
    assert item["is_default"] is True
    mock_table.put_item.assert_called_once()

def test_create_address_without_optional_fields(mock_table):
    db = AddressDB()
    mock_table.put_item.return_value = {}

    item = db.create_address(
        user_id="user-456",
        label="Work",
        address_line1="456 Business Ave",
        city="Manchester",
        state="England",
        country="UK"
    )
    assert item["user_id"] == "user-456"
    assert item["label"] == "Work"
    assert "postal_code" not in item  # Should not be included if None
    assert "address_line2" not in item
    assert "lat" not in item
    assert "lon" not in item
    assert item["is_default"] is False  # Default value

def test_get_address(mock_table):
    db = AddressDB()
    address_id = "addr-123"
    fake_address = {
        "address_id": address_id,
        "user_id": "user-123",
        "label": "Home",
        "address_line1": "123 Main Street",
        "city": "London"
    }
    mock_table.get_item.return_value = {"Item": fake_address}

    result = db.get_address(address_id)
    assert result == fake_address
    mock_table.get_item.assert_called_once_with(Key={"address_id": address_id})

def test_get_address_not_found(mock_table):
    db = AddressDB()
    address_id = "addr-999"
    mock_table.get_item.return_value = {}

    result = db.get_address(address_id)
    assert result is None

def test_list_addresses(mock_table):
    db = AddressDB()
    user_id = "user-123"
    mock_table.scan.return_value = {
        "Items": [
            {"address_id": "addr-1", "user_id": user_id, "label": "Home", "is_default": True},
            {"address_id": "addr-2", "user_id": user_id, "label": "Work", "is_default": False}
        ]
    }
    
    results = db.list_addresses(user_id)
    assert len(results) == 2
    assert all(r["user_id"] == user_id for r in results)
    mock_table.scan.assert_called_once()

def test_update_address(mock_table):
    db = AddressDB()
    address_id = "addr-123"
    mock_table.update_item.return_value = {"Attributes": {"label": "Updated Home"}}

    result = db.update_address(address_id, label="Updated Home", city="Birmingham")
    assert result is True
    mock_table.update_item.assert_called_once()

def test_update_address_failure(mock_table):
    db = AddressDB()
    address_id = "addr-999"
    mock_table.update_item.side_effect = Exception("Item not found")

    result = db.update_address(address_id, label="Updated")
    assert result is False

def test_delete_address(mock_table):
    db = AddressDB()
    address_id = "addr-123"
    mock_table.delete_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}

    result = db.delete_address(address_id)
    assert result is True
    mock_table.delete_item.assert_called_once_with(Key={"address_id": address_id})

def test_delete_address_failure(mock_table):
    db = AddressDB()
    address_id = "addr-999"
    mock_table.delete_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 400}}

    result = db.delete_address(address_id)
    assert result is False

def test_set_default_address(mock_table):
    db = AddressDB()
    user_id = "user-123"
    address_id = "addr-456"
    
    # Mock the scan to return existing addresses
    mock_table.scan.return_value = {
        "Items": [
            {"address_id": "addr-1", "user_id": user_id, "is_default": True},
            {"address_id": "addr-456", "user_id": user_id, "is_default": False}
        ]
    }
    mock_table.update_item.return_value = {"Attributes": {"is_default": True}}

    result = db.set_default(user_id, address_id)
    assert result is True
    # Should call update_item multiple times (to unset old default and set new default)
    assert mock_table.update_item.call_count >= 2

def test_get_default_address(mock_table):
    db = AddressDB()
    user_id = "user-123"
    mock_table.scan.return_value = {
        "Items": [
            {"address_id": "addr-1", "user_id": user_id, "label": "Home", "is_default": False},
            {"address_id": "addr-2", "user_id": user_id, "label": "Work", "is_default": True}
        ]
    }
    
    result = db.get_default_address(user_id)
    assert result["address_id"] == "addr-2"
    assert result["is_default"] is True 