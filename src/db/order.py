"""
DynamoDB Table: orders (multi-tenant SaaS)

Primary Key (composite):
    - PK = TENANT#{tenant_id}
    - SK = ORDER#{order_id}

Attributes:
    - order_id, tenant_id, user_id
    - items (snapshot of order items)
    - total_amount, status, payment_status, last_payment_id
    - address (snapshot), delivery_method, tracking_info, notes
    - created_at, updated_at

GSIs:
    - GSI1_UserOrders:
        PK = TENANT#{tenant_id}#USER#{user_id}
        SK = CREATED_AT#{created_at}#ORDER#{order_id}

    - GSI2_StatusOrders:
        PK = TENANT#{tenant_id}#STATUS#{status}
        SK = CREATED_AT#{created_at}#ORDER#{order_id}
"""

import boto3
import uuid
from typing import List, Dict, Any, Optional, Tuple
from decimal import Decimal
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrderDB:
    def __init__(self, table_name="orders", region_name="eu-west-2"):
        self.table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)

    # -------------------- Create --------------------

    def create_order(
        self,
        tenant_id: str,
        user_id: str,
        items: List[Dict[str, Any]],
        total_amount: float,
        status: str = "pending",
        address: Optional[Dict[str, Any]] = None,
        payment_status: Optional[str] = None,
        last_payment_id: Optional[str] = None,
        notes: Optional[str] = None,
        delivery_method: Optional[str] = None,
        tracking_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        order_id = str(uuid.uuid4())
        timestamp = now_iso()

        # Convert prices to Decimal
        for item in items:
            if "unit_price" in item:
                item["unit_price"] = Decimal(str(item["unit_price"]))
            if "total_price" in item:
                item["total_price"] = Decimal(str(item["total_price"]))

        order = {
            "PK": f"TENANT#{tenant_id}",
            "SK": f"ORDER#{order_id}",

            "entity": "ORDER",
            "order_id": order_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "items": items,
            "total_amount": Decimal(str(total_amount)),
            "status": status,
            "created_at": timestamp,
            "updated_at": timestamp,

            # GSI1: User orders
            "GSI1PK": f"TENANT#{tenant_id}#USER#{user_id}",
            "GSI1SK": f"CREATED_AT#{timestamp}#ORDER#{order_id}",

            # GSI2: Orders by status
            "GSI2PK": f"TENANT#{tenant_id}#STATUS#{status}",
            "GSI2SK": f"CREATED_AT#{timestamp}#ORDER#{order_id}",
        }

        if address is not None:
            order["address"] = address
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

    # -------------------- Get --------------------

    def get_order(self, tenant_id: str, order_id: str) -> Optional[Dict[str, Any]]:
        resp = self.table.get_item(
            Key={"PK": f"TENANT#{tenant_id}", "SK": f"ORDER#{order_id}"}
        )
        return resp.get("Item")

    # -------------------- Update --------------------

    def update_status(self, tenant_id: str, order_id: str, new_status: str) -> bool:
        timestamp = now_iso()
        resp = self.table.update_item(
            Key={"PK": f"TENANT#{tenant_id}", "SK": f"ORDER#{order_id}"},
            UpdateExpression="SET #s = :s, updated_at = :u, "
                             "GSI2PK = :g2pk, GSI2SK = :g2sk",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": new_status,
                ":u": timestamp,
                ":g2pk": f"TENANT#{tenant_id}#STATUS#{new_status}",
                ":g2sk": f"CREATED_AT#{timestamp}#ORDER#{order_id}",
            },
            ReturnValues="UPDATED_NEW",
        )
        return "Attributes" in resp

    def update_payment_summary(
        self,
        tenant_id: str,
        order_id: str,
        payment_status: str,
        last_payment_id: Optional[str] = None,
    ) -> bool:
        update_expr = "SET payment_status = :ps, updated_at = :u"
        expr_vals = {":ps": payment_status, ":u": now_iso()}

        if last_payment_id:
            update_expr += ", last_payment_id = :lp"
            expr_vals[":lp"] = last_payment_id

        resp = self.table.update_item(
            Key={"PK": f"TENANT#{tenant_id}", "SK": f"ORDER#{order_id}"},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_vals,
            ReturnValues="UPDATED_NEW",
        )
        return "Attributes" in resp

    # -------------------- Queries --------------------

    def list_orders_for_user(
        self, tenant_id: str, user_id: str, limit: int = 50, last_key: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        resp = self.table.query(
            IndexName="GSI1_UserOrders",
            KeyConditionExpression=Key("GSI1PK").eq(f"TENANT#{tenant_id}#USER#{user_id}"),
            Limit=limit,
            ScanIndexForward=False,  # latest first
            ExclusiveStartKey=last_key if last_key else None,
        )
        return resp.get("Items", []), resp.get("LastEvaluatedKey")

    def list_orders_by_status(
        self, tenant_id: str, status: str, limit: int = 50, last_key: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        resp = self.table.query(
            IndexName="GSI2_StatusOrders",
            KeyConditionExpression=Key("GSI2PK").eq(f"TENANT#{tenant_id}#STATUS#{status}"),
            Limit=limit,
            ScanIndexForward=False,
            ExclusiveStartKey=last_key if last_key else None,
        )
        return resp.get("Items", []), resp.get("LastEvaluatedKey")
