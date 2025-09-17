"""
DynamoDB Table: carts (multi-tenant SaaS)

Primary Key (composite):
  - PK = TENANT#{tenant_id}#CUSTOMER#{customer_id}
  - SK = CART#{cart_id}   # usually 1 active cart per customer, but allows history

Attributes:
  - entity = "CART"
  - tenant_id (string)
  - customer_id (string)
  - cart_id (string, UUID)
  - status (string)          # "active" | "converted" | "abandoned"
  - currency (string)
  - last_channel (string)    # "whatsapp" | "web" | "telegram"
  - items (list<map>)        # [{variant_id, catalog_id, title, qty, unit_price, total_price}]
  - subtotal (Decimal)
  - total_items (int)
  - created_at (ISO8601)
  - updated_at (ISO8601)


"""

import boto3
import uuid
import time
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from boto3.dynamodb.conditions import Key

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def pk_cart(tenant_id: str, customer_id: str) -> str:
    return f"TENANT#{tenant_id}#CUSTOMER#{customer_id}"

def sk_cart(cart_id: str) -> str:
    return f"CART#{cart_id}"

class CartDB:
    def __init__(self, table_name="carts", backups_table_name="cart_backups", 
                 backup_ttl_seconds: int = 1800, region_name="eu-west-2"):
        self.table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)
        self.backups = boto3.resource("dynamodb", region_name=region_name).Table(backups_table_name)
        self.backup_ttl = backup_ttl_seconds

    # ---------- Create / Fetch ----------
    def create_cart(self, tenant_id: str, customer_id: str, currency="GBP", channel="whatsapp") -> Dict[str, Any]:
        cart_id = str(uuid.uuid4())
        timestamp = now_iso()
        cart = {
            "PK": pk_cart(tenant_id, customer_id),
            "SK": sk_cart(cart_id),
            "entity": "CART",
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "cart_id": cart_id,
            "status": "active",
            "currency": currency,
            "last_channel": channel,
            "items": [],
            "subtotal": Decimal("0.00"),
            "total_items": 0,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        self.table.put_item(Item=cart)
        return cart

    def get_active_cart(self, tenant_id: str, customer_id: str) -> Optional[Dict[str, Any]]:
        resp = self.table.query(
            KeyConditionExpression=Key("PK").eq(pk_cart(tenant_id, customer_id)),
            Limit=1,
            ScanIndexForward=False
        )
        items = resp.get("Items", [])
        if not items:
            return None
        cart = items[0]
        return cart if cart.get("status") == "active" else None

    def list_carts(self, tenant_id: str, customer_id: str, limit=20) -> List[Dict[str, Any]]:
        resp = self.table.query(
            KeyConditionExpression=Key("PK").eq(pk_cart(tenant_id, customer_id)),
            Limit=limit,
            ScanIndexForward=False
        )
        return resp.get("Items", [])

    # ---------- Item operations ----------
    def get_cart(self, tenant_id: str, customer_id: str) -> List[Dict[str, Any]]:
        cart = self.get_active_cart(tenant_id, customer_id)
        return cart.get("items", []) if cart else []

    def add_item(self, tenant_id: str, customer_id: str, variant_id: str,
                 catalog_id: str, title: str, qty: int, unit_price: float) -> Dict[str, Any]:
        cart = self.get_active_cart(tenant_id, customer_id)
        if not cart:
            cart = self.create_cart(tenant_id, customer_id)

        items = cart.get("items", [])
        found = False
        for item in items:
            if item["variant_id"] == variant_id:
                item["qty"] += qty
                item["total_price"] = str(Decimal(item["qty"]) * Decimal(item["unit_price"]))
                found = True
                break
        if not found:
            items.append({
                "variant_id": variant_id,
                "catalog_id": catalog_id,
                "title": title,
                "qty": qty,
                "unit_price": str(Decimal(str(unit_price))),
                "total_price": str(Decimal(qty) * Decimal(str(unit_price))),
            })

        return self._save_cart(cart, items)

    def update_quantity(self, tenant_id: str, customer_id: str, variant_id: str, qty: int) -> Dict[str, Any]:
        cart = self.get_active_cart(tenant_id, customer_id)
        if not cart:
            return {}
        items = []
        for item in cart.get("items", []):
            if item["variant_id"] == variant_id:
                if qty > 0:
                    item["qty"] = qty
                    item["total_price"] = str(Decimal(qty) * Decimal(item["unit_price"]))
                    items.append(item)
            else:
                items.append(item)
        return self._save_cart(cart, items)

    def remove_item(self, tenant_id: str, customer_id: str, variant_id: str) -> Dict[str, Any]:
        cart = self.get_active_cart(tenant_id, customer_id)
        if not cart:
            return {}
        items = [i for i in cart.get("items", []) if i["variant_id"] != variant_id]
        return self._save_cart(cart, items)

    def reduce_quantity(self, tenant_id: str, customer_id: str, variant_id: str, reduce_by: int) -> Dict[str, Any]:
        """Reduce the quantity of an item in the cart by the specified amount.
        If the resulting quantity is <= 0, the item is removed entirely."""
        cart = self.get_active_cart(tenant_id, customer_id)
        if not cart:
            return {}
        
        items = []
        for item in cart.get("items", []):
            if item["variant_id"] == variant_id:
                new_qty = item["qty"] - reduce_by
                if new_qty > 0:
                    # Keep the item with reduced quantity
                    item["qty"] = new_qty
                    item["total_price"] = str(Decimal(new_qty) * Decimal(item["unit_price"]))
                    items.append(item)
                # If new_qty <= 0, don't add the item (effectively removing it)
            else:
                items.append(item)
        
        return self._save_cart(cart, items)

    def clear_cart(self, tenant_id: str, customer_id: str) -> Dict[str, Any]:
        cart = self.get_active_cart(tenant_id, customer_id)
        if not cart:
            return {}
        return self._save_cart(cart, [])

    # ---------- Lifecycle ----------
    def mark_converted(self, tenant_id: str, customer_id: str) -> bool:
        cart = self.get_active_cart(tenant_id, customer_id)
        if not cart:
            return False
        cart["status"] = "converted"
        cart["updated_at"] = now_iso()
        self.table.put_item(Item=cart)
        return True

    def abandon_cart(self, tenant_id: str, customer_id: str) -> bool:
        cart = self.get_active_cart(tenant_id, customer_id)
        if not cart:
            return False
        cart["status"] = "abandoned"
        cart["updated_at"] = now_iso()
        self.table.put_item(Item=cart)
        return True

    # ---------- Backup / Restore ----------
    def backup_cart(self, tenant_id: str, customer_id: str) -> Optional[str]:
        cart = self.get_active_cart(tenant_id, customer_id)
        if not cart or not cart.get("items"):
            return None

        backup_id = str(uuid.uuid4())
        now = int(time.time())
        expires_at = now + self.backup_ttl

        snapshot = cart.get("items", [])
        self.backups.put_item(
            Item={
                "PK": pk_cart(tenant_id, customer_id),
                "SK": f"BACKUP#{backup_id}",
                "tenant_id": tenant_id,
                "customer_id": customer_id,
                "backup_id": backup_id,
                "snapshot": snapshot,
                "created_at": now,
                "expires_at": expires_at,
            }
        )
        return backup_id

    def restore_cart(self, tenant_id: str, customer_id: str) -> Dict[str, Any]:
        resp = self.backups.query(
            KeyConditionExpression=Key("PK").eq(pk_cart(tenant_id, customer_id)),
            Limit=1,
            ScanIndexForward=False
        )
        backups = resp.get("Items", [])
        if not backups:
            return {"restored": False, "reason": "no_backup"}

        backup = backups[0]
        if backup.get("expires_at", 0) < int(time.time()):
            return {"restored": False, "reason": "backup_expired"}

        cart = self.get_active_cart(tenant_id, customer_id)
        if not cart:
            cart = self.create_cart(tenant_id, customer_id)

        # merge quantities
        current_items = {i["variant_id"]: Decimal(i["qty"]) for i in cart.get("items", [])}
        for i in backup["snapshot"]:
            vid = i["variant_id"]
            current_items[vid] = current_items.get(vid, Decimal(0)) + Decimal(str(i["qty"]))

        items = []
        for i in backup["snapshot"]:
            vid = i["variant_id"]
            qty = int(current_items[vid])
            items.append({
                "variant_id": vid,
                "catalog_id": i["catalog_id"],
                "title": i["title"],
                "qty": qty,
                "unit_price": i["unit_price"],
                "total_price": str(Decimal(qty) * Decimal(i["unit_price"])),
            })

        return self._save_cart(cart, items)

    # ---------- Internal helper ----------
    def _save_cart(self, cart: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
        subtotal = sum(Decimal(i["total_price"]) for i in items) if items else Decimal("0.00")
        total_items = sum(i["qty"] for i in items) if items else 0
        cart["items"] = items
        cart["subtotal"] = subtotal
        cart["total_items"] = total_items
        cart["updated_at"] = now_iso()
        self.table.put_item(Item=cart)
        return cart
