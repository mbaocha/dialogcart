"""
DynamoDB Table: b_payments

Partition key: payment_id (string)

Attributes:
    - payment_id (string, PK)
    - order_id (string)                 # Reference to the order being paid
    - user_id (string)                  # User who made the payment
    - amount (number/decimal)           # Payment amount
    - currency (string)                 # e.g., 'GBP'
    - method (string)                   # e.g., 'card', 'bank_transfer', 'wallet', etc.
    - provider (string)                 # e.g., 'Stripe', 'Paystack', etc.
    - status (string)                   # 'pending', 'paid', 'failed', 'refunded'
    - reference (string)                # Payment gateway transaction ID or reference
    - created_at (string, ISO8601)
    - updated_at (string, ISO8601)
    - paid_at (string, ISO8601, optional)      # When payment was successful
    - raw_response (map, optional)              # Raw payment gateway response for audit/debug
    # Add more fields as needed (e.g. error messages)
"""

import boto3
import uuid
from decimal import Decimal
from typing import Optional, Dict, Any
from datetime import datetime, timezone

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class PaymentDB:
    def __init__(self, table_name="b_payments"):
        self.table = boto3.resource('dynamodb').Table(table_name)

    def create_payment(
        self,
        order_id: str,
        user_id: str,
        amount: float,
        currency: str = "GBP",
        method: str = "card",
        provider: str = "Stripe",
        status: str = "pending",
        reference: Optional[str] = None,
        paid_at: Optional[str] = None,
        raw_response: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payment_id = str(uuid.uuid4())
        timestamp = now_iso()
        item = {
            "payment_id": payment_id,
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

        self.table.put_item(Item=item)
        return item

    def get_payment(self, payment_id: str) -> Optional[Dict[str, Any]]:
        response = self.table.get_item(Key={"payment_id": payment_id})
        return response.get("Item")

    def update_status(
        self,
        payment_id: str,
        status: str,
        reference: Optional[str] = None,
        paid_at: Optional[str] = None,
        raw_response: Optional[Dict[str, Any]] = None,
    ) -> bool:
        update_expr = "set status = :s, updated_at = :u"
        expr_attr_vals = {":s": status, ":u": now_iso()}

        if reference:
            update_expr += ", reference = :r"
            expr_attr_vals[":r"] = reference
        if paid_at:
            update_expr += ", paid_at = :p"
            expr_attr_vals[":p"] = paid_at
        if raw_response:
            update_expr += ", raw_response = :rr"
            expr_attr_vals[":rr"] = raw_response

        try:
            response = self.table.update_item(
                Key={"payment_id": payment_id},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_attr_vals,
                ReturnValues="UPDATED_NEW"
            )
            return "Attributes" in response
        except Exception:
            return False

    def list_payments(self, user_id: Optional[str] = None, limit: int = 100) -> Any:
        if user_id:
            response = self.table.scan(
                FilterExpression="user_id = :uid",
                ExpressionAttributeValues={":uid": user_id},
                Limit=limit
            )
        else:
            response = self.table.scan(Limit=limit)
        return response.get("Items", [])
