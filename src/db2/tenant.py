# db/tenant.py

"""
DynamoDB Table: tenants

Partition key:
    - tenant_id (string, PK)  # Unique tenant identifier (UUID or numeric)

Attributes:
    - tenant_id (string)      # Unique ID (internal reference used in other tables)
    - platform (string)       # "shopify" | "woocommerce" | "custom" | etc.
    - store_domain (string)   # e.g. "demo-store.myshopify.com"
    - api_key_ref (string)    # Reference to API credentials (e.g. Secrets Manager ARN)
    - plan (string)           # Subscription plan ("basic", "pro", "enterprise")
    - status (string)         # "active", "suspended", "deleted"
    - limits (map, optional)  # Plan limits, e.g. {"max_products": 1000, "max_users": 10}
    - created_at (string, ISO8601)  # Timestamp of tenant creation
    - updated_at (string, ISO8601)  # Timestamp of last update

Notes:
    - Each tenant has one record in this table.
    - This table is small (1 row per tenant).
    - All product, variant, order, and other data live in shared multi-tenant tables
      (e.g. `agentic_catalog`, `b_orders`), keyed by tenant_id.
    - Use this table to resolve tenant metadata (platform, domain, auth tokens).
"""

import boto3
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class TenantDB:
    def __init__(self, table_name: str = "tenants", region_name: str = "eu-west-2"):
        self.table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)

    # ---------------- Create ----------------
    def create_tenant(
        self,
        platform: str,
        store_domain: str,
        api_key_ref: Optional[str] = None,
        plan: str = "basic",
        status: str = "active",
        limits: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Register a new tenant and return the created record."""
        tenant_id = str(uuid.uuid4())  # or use an int sequence if you prefer
        timestamp = now_iso()

        tenant = {
            "tenant_id": tenant_id,
            "platform": platform,
            "store_domain": store_domain,
            "plan": plan,
            "status": status,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        if api_key_ref:
            tenant["api_key_ref"] = api_key_ref
        if limits:
            tenant["limits"] = limits

        self.table.put_item(Item=tenant)
        return tenant

    # ---------------- Read ----------------
    def get_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a tenant by id."""
        resp = self.table.get_item(Key={"tenant_id": tenant_id})
        return resp.get("Item")

    def list_tenants(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List tenants (basic scan, fine for small tenant counts)."""
        resp = self.table.scan(Limit=limit)
        return resp.get("Items", [])

    # ---------------- Update ----------------
    def update_status(self, tenant_id: str, status: str) -> bool:
        """Update tenant status (active/suspended/deleted)."""
        resp = self.table.update_item(
            Key={"tenant_id": tenant_id},
            UpdateExpression="SET #s = :s, updated_at = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status, ":u": now_iso()},
            ReturnValues="UPDATED_NEW"
        )
        return "Attributes" in resp

    def update_plan(self, tenant_id: str, plan: str, limits: Optional[Dict[str, Any]] = None) -> bool:
        """Upgrade/downgrade tenant plan and limits."""
        expr = "SET plan = :p, updated_at = :u"
        vals = {":p": plan, ":u": now_iso()}
        if limits:
            expr += ", limits = :l"
            vals[":l"] = limits
        resp = self.table.update_item(
            Key={"tenant_id": tenant_id},
            UpdateExpression=expr,
            ExpressionAttributeValues=vals,
            ReturnValues="UPDATED_NEW"
        )
        return "Attributes" in resp

    # ---------------- Delete ----------------
    def delete_tenant(self, tenant_id: str) -> bool:
        """Delete a tenant (soft delete recommended instead)."""
        self.table.delete_item(Key={"tenant_id": tenant_id})
        return True
