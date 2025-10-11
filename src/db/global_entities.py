"""
DynamoDB Table: global_entities (cross-domain entity dictionary)

Primary Key (composite):
    - pk (string) → <ENTITY_TYPE>#<canonical_value>
    - sk (string) → <ENTITY_TYPE> (or namespaced variant, e.g. VARIANT#color)

Entities:
    - PRODUCT   (canonical product, e.g. "jeans")
    - BRAND     (canonical brand, e.g. "Nike", "Chanel")
    - VARIANT   (attribute variant, e.g. "slim-fit", "red", "XL")
    - CATEGORY  (high-level grouping, e.g. "fashion", "beauty", "groceries")
    - SYNONYM   (reverse lookup entry that maps a synonym to a canonical entity)

Common Attributes:
    - type (string)               → "product" | "brand" | "variant" | "category" | "synonym"
    - canonical (string)          → normalized canonical value ("jeans", "nike", "slim-fit")
    - synonyms (list<string>)     → list of synonyms/aliases ("denims", "denim pants")
    - category (string | list)    → single domain ("fashion") or multiple domains ["fashion","beauty"]
    - updated_at (string, ISO8601) → auto-computed by Python at write time

PRODUCT-only Attributes:
    - pk = PRODUCT#<product_name>
    - sk = PRODUCT
    - category = domain category (e.g. "fashion")

BRAND-only Attributes:
    - pk = BRAND#<brand_name>
    - sk = BRAND
    - category = one or more domains (e.g. ["fashion","beauty"])

VARIANT-only Attributes:
    - pk = VARIANT#<value>
    - sk = VARIANT#<group> (e.g. VARIANT#color, VARIANT#fit, VARIANT#size)
    - category = domain category (e.g. "fashion")

CATEGORY-only Attributes:
    - pk = CATEGORY#<category_name>
    - sk = CATEGORY
    - category = self-reference to domain name (e.g. "fashion")

SYNONYM-only Attributes:
    - pk = SYNONYM#<normalized_synonym>
    - sk = <ENTITY_TYPE>#<canonical>
    - type = "synonym"
    - canonical = canonical entity string
    - entity_type = original entity type ("product", "brand", "variant", "category")
    - category = single domain or multiple domains

Global Secondary Indexes (GSIs):
    - GSI1_CategoryEntity   → Browse entities by category
        pk: GSI1PK = CATEGORY#<category>
        sk: GSI1SK = <ENTITY_TYPE>#<canonical>

    - GSI2_SynonymLookup    → Synonym to Canonical resolution
        pk: GSI2PK = SYNONYM#<normalized_synonym>
        sk: GSI2SK = <ENTITY_TYPE>#<canonical>
"""

import boto3
from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key


def now_iso() -> str:
    """Return current UTC time in ISO8601 format."""
    return datetime.now(timezone.utc).isoformat()


def pk_entity(entity_type: str, value: str) -> str:
    """Build partition key: e.g., PRODUCT#jeans."""
    return f"{entity_type.upper()}#{value.lower()}"


def sk_entity(entity_type: str, group: Optional[str] = None) -> str:
    """Build sort key: e.g., VARIANT#color or just BRAND/PRODUCT."""
    if entity_type.upper() == "VARIANT" and group:
        return f"VARIANT#{group.lower()}"
    return entity_type.upper()


def normalize_category(category: Optional[Union[str, List[str]]]) -> Optional[Union[str, List[str]]]:
    """
    Normalize category to lowercase.
    - Accepts str ("fashion"), list of str (["fashion","beauty"]), or None.
    - Returns same type, lowercased.
    """
    if category is None:
        return None
    if isinstance(category, str):
        return category.lower()
    if isinstance(category, list):
        return [c.lower() for c in category]
    raise ValueError("category must be str, list, or None")


class GlobalEntitiesDB:
    """
    Wrapper around DynamoDB for managing the global_entities table.
    Stores products, brands, variants, categories, and synonym entries.
    Ensures updated_at is always auto-computed in Python.
    """

    def __init__(self, table_name: str = "global_entities", region_name: str = "eu-west-2"):
        self.table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)

    # ---------- Upsert ----------

    def put_entity(
        self,
        entity_type: str,
        canonical: str,
        category: Optional[Union[str, List[str]]] = None,
        synonyms: Optional[List[str]] = None,
        group: Optional[str] = None
    ) -> None:
        """
        Insert or update an entity (product, brand, variant, category).
        Also creates synonym records for reverse lookup.
        - entity_type: product, brand, variant, category
        - canonical: canonical string (normalized lowercase)
        - category: str or list[str] (domains), or None
        - synonyms: optional list of alternative spellings
        - group: for variants (color, size, fit, etc.)
        """
        timestamp = now_iso()

        item = {
            "pk": pk_entity(entity_type, canonical),
            "sk": sk_entity(entity_type, group),
            "type": entity_type.lower(),
            "canonical": canonical.lower(),
            "category": normalize_category(category),
            "synonyms": [s.lower() for s in (synonyms or [])],
            "updated_at": timestamp,
        }
        self.table.put_item(Item=item)

        # synonym reverse index
        if synonyms:
            for syn in synonyms:
                syn_item = {
                    "pk": f"SYNONYM#{syn.lower()}",
                    "sk": pk_entity(entity_type, canonical),
                    "type": "synonym",
                    "canonical": canonical.lower(),
                    "entity_type": entity_type.lower(),
                    "category": normalize_category(category),
                    "updated_at": timestamp,
                }
                self.table.put_item(Item=syn_item)

    # ---------- Getters ----------

    def get_entity(self, entity_type: str, canonical: str, group: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch a single entity by type and canonical value."""
        resp = self.table.get_item(Key={
            "pk": pk_entity(entity_type, canonical),
            "sk": sk_entity(entity_type, group),
        })
        return resp.get("Item")

    def resolve_synonym(self, synonym: str) -> List[Dict[str, Any]]:
        """
        Given a synonym, return all canonical mappings.
        Example: "denim pants" → [ { pk: "PRODUCT#jeans", ... } ]
        """
        resp = self.table.query(
            KeyConditionExpression=Key("pk").eq(f"SYNONYM#{synonym.lower()}")
        )
        return resp.get("Items", [])

    def list_by_category(self, category: str, limit: int = 50, last_key: Optional[Dict[str, Any]] = None):
        """
        List all entities in a given category (using GSI1).
        Category here must be a single string (e.g., "fashion").
        """
        kwargs = {
            "IndexName": "GSI1_CategoryEntity",
            "KeyConditionExpression": Key("GSI1PK").eq(f"CATEGORY#{category.lower()}"),
            "Limit": limit,
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key

        resp = self.table.query(**kwargs)
        return resp.get("Items", []), resp.get("LastEvaluatedKey")
