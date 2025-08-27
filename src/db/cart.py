"""
DynamoDB Table: b_carts

Partition key: user_id (string)
Sort key:     product_id (string)

Attributes:
    - user_id (string, PK)
    - product_id (string, SK)
    - quantity (number/decimal)
"""

import boto3
from decimal import Decimal
from typing import Optional, List, Dict, Any
import uuid
import time
from boto3.dynamodb.conditions import Key

class CartDB:
    def __init__(self, table_name="b_carts", backups_table_name="b_cart_backups", backup_ttl_seconds: int = 1800):
        self.table = boto3.resource('dynamodb').Table(table_name)
        self.backups = boto3.resource('dynamodb').Table(backups_table_name)
        self.backup_ttl = backup_ttl_seconds

    def add_item(
        self,
        user_id: str,
        product_id: str,
        quantity: float,
    ) -> Dict[str, Any]:
        """
        Add or update an item in the user's cart.
        If the item exists, quantity is increased; otherwise, a new item is created.
        """
        try:
            # Check if item already exists in cart
            existing_item = self.table.get_item(
                Key={
                    "user_id": user_id,
                    "product_id": product_id,
                }
            )
            
            if "Item" in existing_item:
                # Item exists, increase quantity
                existing_quantity = float(existing_item["Item"]["quantity"])
                new_quantity = existing_quantity + quantity
                
                # Update the item with new quantity
                response = self.table.update_item(
                    Key={
                        "user_id": user_id,
                        "product_id": product_id,
                    },
                    UpdateExpression="set quantity = :q",
                    ExpressionAttributeValues={":q": Decimal(str(new_quantity))},
                    ReturnValues="UPDATED_NEW"
                )
                return response.get("Attributes", {})
            else:
                # Item doesn't exist, create new item
                item = {
                    "user_id": user_id,
                    "product_id": product_id,
                    "quantity": Decimal(str(quantity)),
                }
                self.table.put_item(Item=item)
                return item
                
        except Exception:
            # Fallback to original behavior if there's an error
            item = {
                "user_id": user_id,
                "product_id": product_id,
                "quantity": Decimal(str(quantity)),
            }
            self.table.put_item(Item=item)
            return item

    def remove_item(self, user_id: str, product_id: str) -> bool:
        response = self.table.delete_item(
            Key={
                "user_id": user_id,
                "product_id": product_id,
            }
        )
        return response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200

    def update_quantity(self, user_id: str, product_id: str, quantity: float) -> bool:
        response = self.table.update_item(
            Key={
                "user_id": user_id,
                "product_id": product_id,
            },
            UpdateExpression="set quantity = :q",
            ExpressionAttributeValues={":q": Decimal(str(quantity))},
            ReturnValues="UPDATED_NEW"
        )
        return "Attributes" in response

    def get_cart(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get all items in a user's cart (latest quantities).
        """
        response = self.table.query(
            KeyConditionExpression="user_id = :uid",
            ExpressionAttributeValues={":uid": user_id},
        )
        return response.get("Items", [])

    def clear_cartx(self, user_id: str) -> int:
        """
        Remove all items in the user's cart.
        """
        items = self.get_cart(user_id)
        count = 0
        for item in items:
            self.remove_item(user_id, item["product_id"])
            count += 1
        return count




    def backup_cart(self, user_id: str) -> Optional[str]:
            """
            Take a snapshot of the user's current cart into b_cart_backups with a TTL.
            Returns backup_id if a backup was written, or None if cart was empty.
            """
            items = self.get_cart(user_id)
            if not items:
                return None

            backup_id = str(uuid.uuid4())
            now = int(time.time())
            expires_at = now + self.backup_ttl

            # Snapshot only what you need; keep it lightweight
            snapshot = [
                {"product_id": i["product_id"], "quantity": str(i["quantity"])}  # store as str to preserve Decimal
                for i in items
            ]

            self.backups.put_item(
                Item={
                    "user_id": user_id,
                    "backup_id": backup_id,
                    "snapshot": snapshot,
                    "created_at": now,
                    "expires_at": expires_at,  # TTL attribute
                }
            )
            return backup_id

    def clear_cart(self, user_id: str) -> Dict[str, Any]:
        """
        Back up the user's cart and then clear it.
        Returns metadata with how many items were cleared and the backup_id (if any).
        """
        backup_id = self.backup_cart(user_id)
        items = self.get_cart(user_id)
        if not items:
            return {"cleared": 0, "backup_id": backup_id}

        cleared = 0
        # Batch delete (efficient and idempotent)
        with self.table.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"user_id": user_id, "product_id": item["product_id"]})
                cleared += 1

        return {"cleared": cleared, "backup_id": backup_id}

    # ---------- NEW: undo / restore (merge) ----------

    def _get_latest_backup(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch the most recent backup for the user.
        (Assumes SK is backup_id; to sort by time, you may add a GSI or store created_at in SK.)
        For simplicity, this does a Query + scan in code; adjust to your data volume.
        """
        # If you expect many backups per user, create a GSI on (user_id, created_at DESC).
        # Here we just Query and pick the newest by created_at client-side.
        resp = self.backups.query(
            KeyConditionExpression=Key("user_id").eq(user_id)
        )
        backups = resp.get("Items", [])
        if not backups:
            return None
        return max(backups, key=lambda b: b.get("created_at", 0))

    def restore_cart(self, user_id: str) -> Dict[str, Any]:
        """
        Restore from the latest valid backup by MERGING with the current cart:
        - Same product_id: sum quantities
        - Different product_id: keep both
        Re-pricing/stock checks should happen in your pricing/inventory layer (not shown here).
        """
        backup = self._get_latest_backup(user_id)
        if not backup:
            return {"restored": False, "reason": "no_backup"}

        # TTL might not have deleted yet, so check expiry manually
        if backup.get("expires_at", 0) < int(time.time()):
            return {"restored": False, "reason": "backup_expired"}

        # Build dicts for easy merging
        current_items = self.get_cart(user_id)
        current_by_pid: Dict[str, Decimal] = {}
        for item in current_items:
            current_by_pid[item["product_id"]] = current_by_pid.get(item["product_id"], Decimal("0")) + Decimal(str(item["quantity"]))

        backup_by_pid: Dict[str, Decimal] = {}
        for line in backup["snapshot"]:
            # snapshot saved quantity as string; coerce to Decimal
            backup_by_pid[line["product_id"]] = backup_by_pid.get(line["product_id"], Decimal("0")) + Decimal(str(line["quantity"]))

        # Merge quantities
        merged: Dict[str, Decimal] = dict(current_by_pid)
        for pid, qty in backup_by_pid.items():
            merged[pid] = merged.get(pid, Decimal("0")) + qty

        # Write merged cart (overwrite existing items for those keys; add new ones)
        # We "PUT" all merged lines; this will upsert each item. We don't need to delete
        # anything because merged contains the union of PIDs.
        with self.table.batch_writer() as batch:
            for pid, qty in merged.items():
                batch.put_item(Item={
                    "user_id": user_id,
                    "product_id": pid,
                    "quantity": qty
                })

        # (Optional) mark backup as used (soft) — keeps audit but prevents repeated undo semantics
        self.backups.update_item(
            Key={"user_id": user_id, "backup_id": backup["backup_id"]},
            UpdateExpression="SET restored_at = :t",
            ExpressionAttributeValues={":t": int(time.time())},
        )

        return {
            "restored": True,
            "merge": True,
            "added_from_backup": len([pid for pid in backup_by_pid if pid not in current_by_pid]),
            "increased_existing": len([pid for pid in backup_by_pid if pid in current_by_pid]),
            "backup_id": backup["backup_id"],
        }