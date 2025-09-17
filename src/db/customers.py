"""
DynamoDB Table: customers (multi-tenant)

Primary Key (composite):
    - PK (string) → TENANT#{tenant_id}
    - SK (string) → CUSTOMER#{customer_id}

Attributes:
    - entity (string)             → "CUSTOMER"
    - tenant_id (string)
    - customer_id (string)
    - source (string)             → "whatsapp" | "telegram" | "web" | "shopify" | "woocommerce"
    - source_customer_id (string) → optional, upstream ID if synced from Shopify/Woo

    - first_name (string)
    - last_name (string)
    - full_name (string)
    - email (string, optional)
    - phone (string, optional)

    - consent_time (string, ISO8601)
    - status (string)             → "active" | "inprogress" | "disabled"

    - tags (list<string>, optional)
    - notes (string, optional)

    - last_seen (string, ISO8601)
    - state_data (map, optional)
    - chat_summary (string, optional)

    - created_at (string, ISO8601)
    - updated_at (string, ISO8601)

GSIs:
    - GSI1_EmailLookup
        PK: GSI1PK = TENANT#{tenant_id}#EMAIL
        SK: GSI1SK = <normalized_lower_email>
    - GSI2_PhoneLookup
        PK: GSI2PK = TENANT#{tenant_id}#PHONE
        SK: GSI2SK = <normalized_phone>
"""

import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from utils.coreutil import convert_floats_for_dynamodb

try:
    import boto3
    from boto3.dynamodb.conditions import Key, Attr
    BOTO3_AVAILABLE = True
except ImportError:
    print("Warning: boto3 not available, using mock implementation")
    BOTO3_AVAILABLE = False
    # (Mock implementation can be reused from your b_users code if needed)

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def pk_tenant(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"

def sk_customer(customer_id: str) -> str:
    return f"CUSTOMER#{customer_id}"

def normalize_email(email: Optional[str]) -> Optional[str]:
    return email.strip().lower() if email else None

def normalize_phone(phone: Optional[str]) -> Optional[str]:
    return phone.strip().replace(" ", "") if phone else None

class CustomerDB:
    def __init__(self, table_name="customers"):
        self.table = boto3.resource("dynamodb").Table(table_name)

    def save_customer(
        self,
        tenant_id: str,
        first_name: str,
        last_name: str,
        source: str,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        consent_time: Optional[str] = None,
        status: str = "inprogress",
        state_data: Optional[Dict[str, Any]] = None,
        chat_summary: Optional[str] = None,
        source_customer_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update a customer (upsert)."""
        timestamp = now_iso()
        customer_id = str(uuid.uuid4())

        full_name = f"{first_name} {last_name}".strip()
        normalized_email = normalize_email(email)
        normalized_phone = normalize_phone(phone)

        item = {
            "PK": pk_tenant(tenant_id),
            "SK": sk_customer(customer_id),
            "entity": "CUSTOMER",
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "source": source,
            "status": status,
            "created_at": timestamp,
            "updated_at": timestamp,
            "last_seen": timestamp,
        }
        if consent_time:
            item["consent_time"] = consent_time
        if email:
            item["email"] = email
            item["GSI1PK"] = f"TENANT#{tenant_id}#EMAIL"
            item["GSI1SK"] = normalized_email
        if phone:
            item["phone"] = phone
            item["GSI2PK"] = f"TENANT#{tenant_id}#PHONE"
            item["GSI2SK"] = normalized_phone
        if state_data:
            item["state_data"] = convert_floats_for_dynamodb(state_data)
        if chat_summary:
            item["chat_summary"] = chat_summary
        if source_customer_id:
            item["source_customer_id"] = source_customer_id
        if tags:
            item["tags"] = tags
        if notes:
            item["notes"] = notes

        self.table.put_item(Item=item)
        return item

    def get_customer(self, tenant_id: str, customer_id: str) -> Optional[Dict[str, Any]]:
        resp = self.table.get_item(Key={"PK": pk_tenant(tenant_id), "SK": sk_customer(customer_id)})
        return resp.get("Item")

    def lookup_by_email(self, tenant_id: str, email: str) -> Optional[Dict[str, Any]]:
        normalized = normalize_email(email)
        resp = self.table.query(
            IndexName="GSI1_EmailLookup",
            KeyConditionExpression=Key("GSI1PK").eq(f"TENANT#{tenant_id}#EMAIL") &
                                   Key("GSI1SK").eq(normalized),
            Limit=1,
        )
        items = resp.get("Items", [])
        return items[0] if items else None

    def lookup_by_phone(self, tenant_id: str, phone: str) -> Optional[Dict[str, Any]]:
        normalized = normalize_phone(phone)
        resp = self.table.query(
            IndexName="GSI2_PhoneLookup",
            KeyConditionExpression=Key("GSI2PK").eq(f"TENANT#{tenant_id}#PHONE") &
                                   Key("GSI2SK").eq(normalized),
            Limit=1,
        )
        items = resp.get("Items", [])
        return items[0] if items else None

    def list_customers(self, tenant_id: str, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        kwargs = {
            "KeyConditionExpression": Key("PK").eq(pk_tenant(tenant_id)),
            "Limit": limit,
        }
        if status:
            kwargs["FilterExpression"] = Attr("status").eq(status)
        resp = self.table.query(**kwargs)
        return resp.get("Items", [])

    def update_state_data(self, tenant_id: str, customer_id: str, state_data: Dict[str, Any]) -> bool:
        timestamp = now_iso()
        resp = self.table.update_item(
            Key={"PK": pk_tenant(tenant_id), "SK": sk_customer(customer_id)},
            UpdateExpression="SET state_data = :state, updated_at = :u, last_seen = :ls",
            ExpressionAttributeValues={
                ":state": convert_floats_for_dynamodb(state_data),
                ":u": timestamp,
                ":ls": timestamp,
            },
            ReturnValues="UPDATED_NEW",
        )
        return "Attributes" in resp
