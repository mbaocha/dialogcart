#!/usr/bin/env python3
"""
Minimal loader for global_entities.json → DynamoDB.
- Creates global_entities table if it does not exist
- Validates each record
- Loads data into DynamoDB
"""

import json
from datetime import datetime, timezone
import boto3
from botocore.exceptions import ClientError

TABLE_NAME = "global_entities"
REGION = "eu-west-2"  # change if needed
JSON_PATH ="merged_v2.json" # "global_entities.json"  # assumes file is in same dir as script

# --- Validation schema ---
REQUIRED_FIELDS = {"pk", "sk", "type", "canonical"}
VALID_TYPES = {"product", "brand", "variant", "category", "synonym"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_item(item: dict) -> bool:
    """Check required fields and type."""
    missing = REQUIRED_FIELDS - item.keys()
    if missing:
        print(f"✗ Skipping, missing fields {missing}: {item}")
        return False
    if item["type"] not in VALID_TYPES:
        print(f"✗ Skipping, invalid type {item['type']}: {item}")
        return False
    return True

def find_duplicates(data):
    seen = {}
    duplicates = []
    for item in data:
        key = (item.get("pk"), item.get("sk"))
        if key in seen:
            duplicates.append((key, seen[key], item))
        else:
            seen[key] = item
    return duplicates


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def table_exists(client, table_name: str) -> bool:
    try:
        client.describe_table(TableName=table_name)
        return True
    except client.exceptions.ResourceNotFoundException:
        return False


def create_table(client):
    print(f"Creating DynamoDB table: {TABLE_NAME}")
    client.create_table(
        TableName=TABLE_NAME,
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
            {"AttributeName": "GSI2PK", "AttributeType": "S"},
            {"AttributeName": "GSI2SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSI1_CategoryEntity",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "GSI2_SynonymLookup",
                "KeySchema": [
                    {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    waiter = client.get_waiter("table_exists")
    waiter.wait(TableName=TABLE_NAME)
    print(f"✓ Table {TABLE_NAME} created and active.")


def main():
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    client = boto3.client("dynamodb", region_name=REGION)

    # Ensure table exists
    if not table_exists(client, TABLE_NAME):
        create_table(client)
    else:
        print(f"✓ Table {TABLE_NAME} already exists")

    table = dynamodb.Table(TABLE_NAME)

    # Load JSON
    # Load JSON
    data = load_json(JSON_PATH)
    print(f"Loaded {len(data)} records from {JSON_PATH}")

        # Detect duplicates
    dupes = find_duplicates(data)
    if dupes:
        print(f"⚠ Found {len(dupes)} duplicate keys (pk, sk):")
        for key, first, dup in dupes[:20]:  # print first 20 for inspection
            print(f"  Key {key} appears more than once")
            print(f"    First: {first}")
            print(f"    Dup:   {dup}")
        if len(dupes) > 20:
            print(f"... and {len(dupes) - 20} more")
        # ⚠ Don't return here — continue after logging

    # Deduplicate before write
    seen = {}
    for item in data:
        key = (item["pk"], item["sk"])
        if key not in seen:
            seen[key] = item
    unique_data = list(seen.values())
    print(f"✓ Proceeding with {len(unique_data)} unique records")

    # Write to DynamoDB
    with table.batch_writer() as batch:
        count = 0
        for item in unique_data:
            if not validate_item(item):
                continue
            item["updated_at"] = item.get("updated_at") or now_iso()
            batch.put_item(Item=item)
            count += 1
        print(f"✓ Inserted {count} valid items into {TABLE_NAME}")


if __name__ == "__main__":
    main()
