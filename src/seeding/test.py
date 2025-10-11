#!/usr/bin/env python3
"""
Check synonym uniqueness in global_entities DynamoDB table
and show which canonical entities each duplicate synonym maps to.
"""

import boto3
from collections import defaultdict, Counter

TABLE_NAME = "global_entities"
REGION = "eu-west-2"

def scan_table(table):
    """Scan all items from the table (handles pagination)."""
    items = []
    resp = table.scan()
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items

def analyze_synonyms(items):
    """Collect synonyms and map them to canonical entities."""
    synonym_to_entities = defaultdict(set)
    all_synonyms = []

    for item in items:
        canonical = item.get("canonical")
        pk = item.get("pk")
        sk = item.get("sk")

        if not canonical or "synonyms" not in item:
            continue

        for syn in item["synonyms"]:
            syn_norm = syn.strip().lower()
            all_synonyms.append(syn_norm)
            synonym_to_entities[syn_norm].add(f"{pk}::{sk}")

    # Count occurrences
    counter = Counter(all_synonyms)
    return counter, synonym_to_entities

def main():
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(TABLE_NAME)

    print(f"Scanning DynamoDB table: {TABLE_NAME} ...")
    items = scan_table(table)

    counter, synonym_to_entities = analyze_synonyms(items)

    total = sum(counter.values())
    unique = len(counter)
    dupes = {syn: count for syn, count in counter.items() if count > 1}

    print(f"\nTotal synonym strings: {total}")
    print(f"Unique synonym strings: {unique}")
    print(f"  - Appear only once: {unique - len(dupes)}")
    print(f"  - Appear more than once: {len(dupes)}\n")

    if dupes:
        print("⚠ Duplicate synonyms with canonical mappings:")
        for syn, count in sorted(dupes.items(), key=lambda x: -x[1])[:30]:  # show top 30
            print(f"  '{syn}' appears {count} times")
            for ent in sorted(synonym_to_entities[syn]):
                print(f"     ↳ {ent}")
        if len(dupes) > 30:
            print(f"... and {len(dupes) - 30} more")

if __name__ == "__main__":
    main()
