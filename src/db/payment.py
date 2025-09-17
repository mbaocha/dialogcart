"""
Primary Key (composite):
  - PK = TENANT#{tenant_id}
  - SK = PAYMENT#{payment_id}

Attributes:
  - entity = "PAYMENT"
  - payment_id (string)
  - tenant_id (string)
  - order_id (string)
  - user_id (string, optional)
  - amount (number/decimal)
  - currency (string)
  - method (string)           # e.g. card, bank_transfer
  - provider (string)         # e.g. Stripe, Paystack
  - status (string)           # pending, paid, failed, refunded
  - reference (string, optional)
  - created_at, updated_at
  - paid_at (string, optional)
  - raw_response (map, optional)

GSIs:
  - GSI1_OrderPayments → fetch all payments for an order
      PK = TENANT#{tenant_id}#ORDER#{order_id}
      SK = CREATED_AT#{created_at}#PAYMENT#{payment_id}

  - GSI2_StatusPayments → fetch payments by status
      PK = TENANT#{tenant_id}#STATUS#{status}
      SK = CREATED_AT#{created_at}#PAYMENT#{payment_id}

"""
import boto3
import uuid
from decimal import Decimal
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class PaymentDB:
    def __init__(self, table_name="payments"):
        self.table = boto3.resource("dynamodb").Table(table_name)

    def create_payment(
        self,
        tenant_id: str,
        order_id: str,
        user_id: Optional[str],
        amount: float,
        currency: str = "GBP",
        method: str = "card",
        provider: str = "Stripe",
        status: str = "pending",
        reference: Optional[str] = None,
        paid_at: Optional[str] = None,
        raw_response: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Insert a new payment record."""
        payment_id = str(uuid.uuid4())
        timestamp = now_iso()

        item = {
            "PK": f"TENANT#{tenant_id}",
            "SK": f"PAYMENT#{payment_id}",
            "entity": "PAYMENT",
            "payment_id": payment_id,
            "tenant_id": tenant_id,
            "order_id": order_id,
            "user_id": user_id,
            "amount": Decimal(str(amount)),
            "currency": currency,
            "method": method,
            "provider": provider,
            "status": status,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        if reference:
            item["reference"] = reference
        if paid_at:
            item["paid_at"] = paid_at
        if raw_response:
            item["raw_response"] = raw_response

        # GSI1: payments by order
        item["GSI1PK"] = f"TENANT#{tenant_id}#ORDER#{order_id}"
        item["GSI1SK"] = f"CREATED_AT#{timestamp}#PAYMENT#{payment_id}"

        # GSI2: payments by status
        item["GSI2PK"] = f"TENANT#{tenant_id}#STATUS#{status}"
        item["GSI2SK"] = f"CREATED_AT#{timestamp}#PAYMENT#{payment_id}"

        self.table.put_item(Item=item)
        return item

    def get_payment(self, tenant_id: str, payment_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single payment by tenant + payment_id."""
        resp = self.table.get_item(
            Key={
                "PK": f"TENANT#{tenant_id}",
                "SK": f"PAYMENT#{payment_id}",
            }
        )
        return resp.get("Item")

    def update_status(
        self,
        tenant_id: str,
        payment_id: str,
        status: str,
        reference: Optional[str] = None,
        paid_at: Optional[str] = None,
        raw_response: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Update payment status and metadata."""
        update_expr = ["#s = :s", "updated_at = :u"]
        expr_attr_names = {"#s": "status"}
        expr_attr_vals = {
            ":s": status,
            ":u": now_iso(),
        }

        if reference:
            update_expr.append("reference = :r")
            expr_attr_vals[":r"] = reference
        if paid_at:
            update_expr.append("paid_at = :p")
            expr_attr_vals[":p"] = paid_at
        if raw_response:
            update_expr.append("raw_response = :rr")
            expr_attr_vals[":rr"] = raw_response

        resp = self.table.update_item(
            Key={
                "PK": f"TENANT#{tenant_id}",
                "SK": f"PAYMENT#{payment_id}",
            },
            UpdateExpression="SET " + ", ".join(update_expr),
            ExpressionAttributeValues=expr_attr_vals,
            ExpressionAttributeNames=expr_attr_names,
            ReturnValues="UPDATED_NEW",
        )
        return "Attributes" in resp

    def list_payments_by_order(self, tenant_id: str, order_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Query all payments for a given order (latest first)."""
        resp = self.table.query(
            IndexName="GSI1_OrderPayments",
            KeyConditionExpression="GSI1PK = :pk",
            ExpressionAttributeValues={":pk": f"TENANT#{tenant_id}#ORDER#{order_id}"},
            Limit=limit,
            ScanIndexForward=False,  # latest first
        )
        return resp.get("Items", [])

    def list_payments_by_status(self, tenant_id: str, status: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Query all payments for a given status (e.g. failed, pending)."""
        resp = self.table.query(
            IndexName="GSI2_StatusPayments",
            KeyConditionExpression="GSI2PK = :pk",
            ExpressionAttributeValues={":pk": f"TENANT#{tenant_id}#STATUS#{status}"},
            Limit=limit,
            ScanIndexForward=False,
        )
        return resp.get("Items", [])
