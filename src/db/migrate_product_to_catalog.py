#!/usr/bin/env python3
"""
One-off DynamoDB migration: product_id -> catalog_id

What it does:
- catalog table:
  - Rename PRODUCT entity to CATALOG (entity: "CATALOG")
  - Change SK from PRODUCT#{product_id} to CATALOG#{catalog_id}
  - Rename attribute product_id -> catalog_id on PRODUCT and VARIANT items
  - For VARIANT items, ensure attribute catalog_id is set (copied from product_id)

- carts table:
  - For cart items (embedded list), change each line's product_id -> catalog_id

- cart_backups table:
  - For backups.snapshot list, change product_id -> catalog_id for each snap item

Safety:
- Supports --dry-run (default) to preview changes only
- Batches writes with retries

Usage:
  AWS_REGION=eu-west-2 python -m db.migrate_product_to_catalog --tenant-id demo-tenant-001 --execute
  python src/db/migrate_product_to_catalog.py --tenant-id demo-tenant-001 --dry-run
"""

import os
import sys
import time
import argparse
from typing import Dict, Any, List, Optional

try:
    import boto3  # type: ignore
    from boto3.dynamodb.conditions import Key
except ImportError:
    print("boto3 is required. Install with: pip install boto3")
    sys.exit(1)


REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "eu-west-2"


def pk_tenant(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"


def sk_product(product_id: str) -> str:
    return f"PRODUCT#{product_id}"


def sk_catalog(catalog_id: str) -> str:
    return f"CATALOG#{catalog_id}"


def batch_write(table, puts: List[Dict[str, Any]], deletes: List[Dict[str, Any]]) -> None:
    if not puts and not deletes:
        return
    with table.batch_writer(overwrite_by_pkeys=["PK", "SK"]) as bw:
        for item in puts:
            bw.put_item(Item=item)
        for key in deletes:
            bw.delete_item(Key=key)


def migrate_catalog_table(dynamodb, tenant_id: str, execute: bool) -> Dict[str, int]:
    table = dynamodb.Table("catalog")
    stats = {"scanned": 0, "updated": 0, "deleted": 0, "errors": 0}

    last_key: Optional[Dict[str, Any]] = None
    while True:
        qkwargs: Dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(pk_tenant(tenant_id)),
            "Limit": 200,
        }
        if last_key:
            qkwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**qkwargs)
        items = resp.get("Items", [])
        stats["scanned"] += len(items)

        puts: List[Dict[str, Any]] = []
        deletes: List[Dict[str, Any]] = []

        for it in items:
            entity = it.get("entity")

            if entity == "PRODUCT":
                product_id = it.get("product_id")
                if not product_id:
                    continue
                new_item = dict(it)
                new_item["SK"] = sk_catalog(product_id)
                new_item["entity"] = "CATALOG"
                new_item["catalog_id"] = product_id
                if "product_id" in new_item:
                    del new_item["product_id"]
                puts.append(new_item)
                deletes.append({"PK": it["PK"], "SK": it["SK"]})
                stats["updated"] += 1

            elif entity == "VARIANT":
                # ensure catalog_id field exists (copy from product_id)
                if "catalog_id" not in it and it.get("product_id"):
                    it["catalog_id"] = it["product_id"]
                    # Keep product_id too for safety; code already prefers catalog_id
                    puts.append(it)
                    stats["updated"] += 1

        if execute:
            batch_write(table, puts, deletes)

        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    return stats


def migrate_carts_table(dynamodb, tenant_id: str, execute: bool) -> Dict[str, int]:
    table = dynamodb.Table("carts")
    stats = {"scanned": 0, "updated": 0, "errors": 0}

    # Query all carts for tenant (PK starts with TENANT#tenant_id#CUSTOMER#)
    pk_prefix = f"TENANT#{tenant_id}#CUSTOMER#"

    # We don't have a GSI; we need to scan and filter PK prefix (small table assumed)
    resp = table.scan()
    while True:
        items = resp.get("Items", [])
        for it in items:
            stats["scanned"] += 1
            if not it.get("PK", "").startswith(pk_prefix):
                continue
            changed = False
            line_items = it.get("items", []) or []
            for li in line_items:
                if "product_id" in li and "catalog_id" not in li:
                    li["catalog_id"] = li["product_id"]
                    del li["product_id"]
                    changed = True

            if changed:
                if execute:
                    table.put_item(Item=it)
                stats["updated"] += 1

        if "LastEvaluatedKey" in resp:
            resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        else:
            break

    return stats


def migrate_cart_backups_table(dynamodb, tenant_id: str, execute: bool) -> Dict[str, int]:
    table = dynamodb.Table("cart_backups")
    stats = {"scanned": 0, "updated": 0, "errors": 0}

    pk_prefix = f"TENANT#{tenant_id}#CUSTOMER#"
    resp = table.scan()
    while True:
        items = resp.get("Items", [])
        for it in items:
            stats["scanned"] += 1
            if not it.get("PK", "").startswith(pk_prefix):
                continue
            snap = it.get("snapshot", []) or []
            changed = False
            for li in snap:
                if "product_id" in li and "catalog_id" not in li:
                    li["catalog_id"] = li["product_id"]
                    del li["product_id"]
                    changed = True
            if changed:
                if execute:
                    table.put_item(Item=it)
                stats["updated"] += 1

        if "LastEvaluatedKey" in resp:
            resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        else:
            break

    return stats


def main():
    parser = argparse.ArgumentParser(description="Migrate product_id -> catalog_id across DynamoDB tables")
    parser.add_argument("--tenant-id", required=True, help="Tenant to migrate")
    parser.add_argument("--execute", action="store_true", help="Write changes (default is dry-run)")
    parser.add_argument("--region", default=REGION, help="AWS region")
    args = parser.parse_args()

    dynamodb = boto3.resource("dynamodb", region_name=args.region)

    start = time.time()
    print(f"Starting migration for tenant={args.tenant_id} region={args.region} mode={'EXECUTE' if args.execute else 'DRY-RUN'}")

    cat_stats = migrate_catalog_table(dynamodb, args.tenant_id, execute=args.execute)
    print(f"catalog: {cat_stats}")

    carts_stats = migrate_carts_table(dynamodb, args.tenant_id, execute=args.execute)
    print(f"carts: {carts_stats}")

    backups_stats = migrate_cart_backups_table(dynamodb, args.tenant_id, execute=args.execute)
    print(f"cart_backups: {backups_stats}")

    dur = time.time() - start
    print(f"Done in {dur:.1f}s")


if __name__ == "__main__":
    main()


