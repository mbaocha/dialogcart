"""
DynamoDB Table: customer_addresses (multi-tenant)

Primary Key (composite):
    - PK (string) → TENANT#{tenant_id}
    - SK (string) → ADDRESS#{address_id}

Attributes:
    - entity (string)             → "ADDRESS"
    - tenant_id (string)
    - address_id (string, UUID)
    - customer_id (string, FK to customers table)

    - label (string)              → "Home", "Office"
    - recipient_name (string)
    - phone (string, optional)

    - address_line1 (string)
    - address_line2 (string, optional)
    - city (string)
    - state (string)
    - country (string)
    - postal_code (string, optional)

    - lat (float, optional)
    - lon (float, optional)

    - is_default (bool)
    - created_at (ISO8601)
    - updated_at (ISO8601)

GSIs:
    - GSI1_CustomerAddresses → list all addresses for a customer
        PK: GSI1PK = TENANT#{tenant_id}#CUSTOMER#{customer_id}
        SK: GSI1SK = ADDRESS#{address_id}
"""

import boto3
import uuid
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def pk_tenant(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"

def sk_address(address_id: str) -> str:
    return f"ADDRESS#{address_id}"

def gsi_customer_pk(tenant_id: str, customer_id: str) -> str:
    return f"TENANT#{tenant_id}#CUSTOMER#{customer_id}"

class CustomerAddressDB:
    def __init__(self, table_name="customer_addresses"):
        self.table = boto3.resource("dynamodb").Table(table_name)

    # ---------------- Create ----------------
    def create_address(
        self,
        tenant_id: str,
        customer_id: str,
        label: str,
        address_line1: str,
        city: str,
        state: str,
        country: str,
        postal_code: Optional[str] = None,
        address_line2: Optional[str] = None,
        recipient_name: Optional[str] = None,
        phone: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        is_default: bool = False,
    ) -> Dict[str, Any]:
        """Create a new address for a customer."""
        address_id = str(uuid.uuid4())
        timestamp = now_iso()

        item = {
            "PK": pk_tenant(tenant_id),
            "SK": sk_address(address_id),
            "entity": "ADDRESS",
            "tenant_id": tenant_id,
            "address_id": address_id,
            "customer_id": customer_id,
            "label": label,
            "address_line1": address_line1,
            "city": city,
            "state": state,
            "country": country,
            "created_at": timestamp,
            "updated_at": timestamp,
            "is_default": bool(is_default),
            "GSI1PK": gsi_customer_pk(tenant_id, customer_id),
            "GSI1SK": sk_address(address_id),
        }
        if address_line2:
            item["address_line2"] = address_line2
        if postal_code:
            item["postal_code"] = postal_code
        if recipient_name:
            item["recipient_name"] = recipient_name
        if phone:
            item["phone"] = phone
        if lat is not None:
            item["lat"] = float(lat)
        if lon is not None:
            item["lon"] = float(lon)

        self.table.put_item(Item=item)
        return item

    # ---------------- Read ----------------
    def get_address(self, tenant_id: str, address_id: str) -> Optional[Dict[str, Any]]:
        resp = self.table.get_item(Key={"PK": pk_tenant(tenant_id), "SK": sk_address(address_id)})
        return resp.get("Item")

    def list_addresses(self, tenant_id: str, customer_id: str) -> List[Dict[str, Any]]:
        """List all addresses for a customer using GSI1."""
        resp = self.table.query(
            IndexName="GSI1_CustomerAddresses",
            KeyConditionExpression=Key("GSI1PK").eq(gsi_customer_pk(tenant_id, customer_id))
        )
        return resp.get("Items", [])

    def get_default_address(self, tenant_id: str, customer_id: str) -> Optional[Dict[str, Any]]:
        addresses = self.list_addresses(tenant_id, customer_id)
        for addr in addresses:
            if addr.get("is_default"):
                return addr
        return None

    # ---------------- Update ----------------
    def update_address(self, tenant_id: str, address_id: str, **kwargs) -> bool:
        """Update address fields."""
        update_fields = []
        values = {}
        for k, v in kwargs.items():
            if v is not None:
                update_fields.append(f"{k} = :{k}")
                values[f":{k}"] = v

        if not update_fields:
            return False

        values[":updated_at"] = now_iso()
        update_fields.append("updated_at = :updated_at")

        update_expr = "SET " + ", ".join(update_fields)
        resp = self.table.update_item(
            Key={"PK": pk_tenant(tenant_id), "SK": sk_address(address_id)},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=values,
            ReturnValues="UPDATED_NEW"
        )
        return "Attributes" in resp

    def set_default_address(self, tenant_id: str, customer_id: str, address_id: str) -> bool:
        """Set an address as default, unset all others."""
        addresses = self.list_addresses(tenant_id, customer_id)
        for addr in addresses:
            if addr["address_id"] != address_id and addr.get("is_default"):
                self.update_address(tenant_id, addr["address_id"], is_default=False)
        return self.update_address(tenant_id, address_id, is_default=True)

    # ---------------- Delete ----------------
    def delete_address(self, tenant_id: str, address_id: str) -> bool:
        resp = self.table.delete_item(Key={"PK": pk_tenant(tenant_id), "SK": sk_address(address_id)})
        return resp.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200
