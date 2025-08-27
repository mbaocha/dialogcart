"""
DynamoDB Table: b_orders

Partition key: order_id (string)

Attributes:
    - order_id (string, PK)
    - user_id (string)
    - items (list of maps)         # [{product_id, name, quantity, unit_price, total_price}, ...]
    - total_amount (number)
    - status (string)
        # Common statuses: 'pending', 'paid', 'failed', 'processing', 'shipped', 'delivered', 'cancelled', 'refunded'
        # 'pending' means order exists but payment not yet confirmed.
    - created_at (string, ISO8601)
    - updated_at (string, ISO8601)
    - address_id (string, optional)
    - address (map, optional)      # Snapshot of delivery address at order time
    - delivery_method (string, optional)
    - tracking_info (map, optional)
    - notes (string, optional)
    - payment_status (string, optional)  # e.g., 'pending', 'paid', 'failed' (summary only)
    - last_payment_id (string, optional) # Reference to latest payment record if needed
"""

import boto3
import uuid
from typing import List, Dict, Any, Optional
from decimal import Decimal
from datetime import datetime, timezone

try:
    from db.address import AddressDB
    address_db = AddressDB()
except ImportError:
    address_db = None

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class OrderDB:
    def __init__(self, table_name="b_orders"):
        self.table = boto3.resource('dynamodb').Table(table_name)

    def create_order(
        self,
        user_id: str,
        items: List[Dict[str, Any]],
        total_amount: float,
        status: str = "pending",
        address_id: Optional[str] = None,
        address: Optional[Any] = None,
        payment_status: Optional[str] = None,     # summary payment status only
        last_payment_id: Optional[str] = None,    # reference to payment record
        notes: Optional[str] = None,
        delivery_method: Optional[str] = None,
        tracking_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        order_id = str(uuid.uuid4())
        timestamp = now_iso()

        for item in items:
            if "unit_price" in item:
                item["unit_price"] = Decimal(str(item["unit_price"]))
            if "total_price" in item:
                item["total_price"] = Decimal(str(item["total_price"]))

        order_address = None
        if address_id:
            if not address_db:
                raise Exception("AddressDB not available. Install or import db.address.AddressDB")
            addr = address_db.get_address(address_id)
            if not addr:
                raise ValueError("Address not found")
            order_address = {k: v for k, v in addr.items() if k not in ("user_id", "address_id")}
        elif address:
            order_address = address

        order = {
            "order_id": order_id,
            "user_id": user_id,
            "items": items,
            "total_amount": Decimal(str(total_amount)),
            "status": status,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        if order_address is not None:
            order["address"] = order_address
        if payment_status is not None:
            order["payment_status"] = payment_status
        if last_payment_id is not None:
            order["last_payment_id"] = last_payment_id
        if notes is not None:
            order["notes"] = notes
        if delivery_method is not None:
            order["delivery_method"] = delivery_method
        if tracking_info is not None:
            order["tracking_info"] = tracking_info

        self.table.put_item(Item=order)
        return order

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        response = self.table.get_item(Key={"order_id": order_id})
        return response.get("Item")

    def update_status(self, order_id: str, status: str) -> bool:
        try:
            response = self.table.update_item(
                Key={"order_id": order_id},
                UpdateExpression="set status = :s, updated_at = :u",
                ExpressionAttributeValues={
                    ":s": status,
                    ":u": now_iso(),
                },
                ReturnValues="UPDATED_NEW"
            )
            return "Attributes" in response
        except Exception:
            return False

    def update_payment_summary(
        self,
        order_id: str,
        payment_status: str,
        last_payment_id: Optional[str] = None,
    ) -> bool:
        update_expr = "set payment_status = :ps, updated_at = :u"
        expr_attr_vals = {
            ":ps": payment_status,
            ":u": now_iso(),
        }
        if last_payment_id:
            update_expr += ", last_payment_id = :lp"
            expr_attr_vals[":lp"] = last_payment_id

        response = self.table.update_item(
            Key={"order_id": order_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_attr_vals,
            ReturnValues="UPDATED_NEW"
        )
        return "Attributes" in response

    def list_orders(self, user_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if user_id:
            response = self.table.scan(
                FilterExpression="user_id = :uid",
                ExpressionAttributeValues={":uid": user_id},
                Limit=limit
            )
        else:
            response = self.table.scan(Limit=limit)
        return response.get("Items", [])
