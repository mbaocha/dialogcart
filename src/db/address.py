"""
DynamoDB Table: user_addresses

Partition key: address_id (string)
Sort key: (none)

Attributes:
    - address_id (string, PK)
    - user_id (string, FK to users)
    - label (string)          # "Home", "Office", etc.
    - address_line1 (string)
    - address_line2 (string, optional)
    - city (string)
    - state (string)
    - country (string)
    - postal_code (string)
    - lat (float, optional)
    - lon (float, optional)
    - is_default (bool, optional)
    - created_at (string, ISO8601)
    - updated_at (string, ISO8601)
"""

import boto3
import uuid
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class AddressDB:
    def __init__(self, table_name="user_addresses"):
        self.table = boto3.resource('dynamodb').Table(table_name)

    def create_address(
        self,
        user_id: str,
        label: str,
        address_line1: str,
        city: str,
        state: str,
        country: str,
        postal_code: Optional[str] = None,
        address_line2: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        is_default: Optional[bool] = False,
    ) -> Dict[str, Any]:
        address_id = str(uuid.uuid4())
        timestamp = now_iso()
        item = {
            "address_id": address_id,
            "user_id": user_id,
            "label": label,
            "address_line1": address_line1,
            "city": city,
            "state": state,
            "country": country,
            "created_at": timestamp,
            "updated_at": timestamp,
            "is_default": bool(is_default),
        }
        if address_line2:
            item["address_line2"] = address_line2
        if postal_code:
            item["postal_code"] = postal_code
        if lat is not None:
            item["lat"] = float(lat)
        if lon is not None:
            item["lon"] = float(lon)

        self.table.put_item(Item=item)
        return item

    def get_address(self, address_id: str) -> Optional[Dict[str, Any]]:
        response = self.table.get_item(Key={"address_id": address_id})
        return response.get("Item")

    def list_addresses(self, user_id: str) -> List[Dict[str, Any]]:
        # DynamoDB scan for all addresses belonging to a user (if no GSI on user_id)
        response = self.table.scan(
            FilterExpression="user_id = :uid",
            ExpressionAttributeValues={":uid": user_id}
        )
        return response.get("Items", [])

    def update_address(
        self,
        address_id: str,
        **kwargs,
    ) -> bool:
        """
        Update any address fields (address_line1, label, city, etc).
        """
        update_fields = []
        values = {}
        for k, v in kwargs.items():
            if v is not None:
                update_fields.append(f"{k} = :{k}")
                values[f":{k}"] = v
        values[":updated_at"] = now_iso()
        update_fields.append("updated_at = :updated_at")

        if not update_fields:
            return False  # nothing to update

        try:
            response = self.table.update_item(
                Key={"address_id": address_id},
                UpdateExpression="set " + ", ".join(update_fields),
                ExpressionAttributeValues=values,
                ReturnValues="UPDATED_NEW"
            )
            return "Attributes" in response
        except Exception:
            return False

    def delete_address(self, address_id: str) -> bool:
        response = self.table.delete_item(Key={"address_id": address_id})
        return response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200

    def set_default_address(self, user_id: str, address_id: str) -> bool:
        """
        Sets the specified address as default for the user, unsetting others.
        """
        # 1. Unset all others
        addresses = self.list_addresses(user_id)
        for addr in addresses:
            if addr["address_id"] != address_id and addr.get("is_default"):
                self.update_address(addr["address_id"], is_default=False)
        # 2. Set this one
        return self.update_address(address_id, is_default=True)

    def set_default(self, user_id: str, address_id: str) -> bool:
        """
        Alias for set_default_address for backward compatibility.
        """
        return self.set_default_address(user_id, address_id)

    def get_default_address(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Gets the default address for a user.
        Returns the first address with is_default=True, or None if no default exists.
        """
        addresses = self.list_addresses(user_id)
        for addr in addresses:
            if addr.get("is_default"):
                return addr
        return None
