"""
DynamoDB Table: catalog (multi-tenant, SaaS)

Primary Key (composite):
    - PK (string) → TENANT#{tenant_id}
    - SK (string) → CATALOG#{catalog_id} | VARIANT#{variant_id}

Entities:
    - CATALOG (reference/metadata per catalog item)
    - VARIANT (purchasable SKU; denormalized for speed)

Common Attributes:
    - entity (string)              → "CATALOG" or "VARIANT"
    - tenant_id (string)           → tenant scoping
    - catalog_id (string)          → stable per catalog item (DialogCart ID or source-mapped)
    - handle (string)              → SEO slug / unique identifier
    - title (string)               → item title (used for prefix search)
    - vendor (string, optional)
    - category (map)               → { name, path[], source, confidence }
    - category_name (string)       → duplicated for GSIs
    - tags (list<string>, optional)
    - collections (list<string>, optional)
    - image_src / default_image (string, optional)
    - status (string)              → "active" | "draft" | "archived"
    - updated_at (string, ISO8601)
    - source (string)              → "shopify" | "woocommerce" | "native"
    - source_catalog_id (string, optional)  → upstream product ID
    - source_variant_id (string, optional)  → upstream variant ID
    - source_handle (string, optional)      → upstream handle/slug
    - source_updated_at (string, optional)  → last modified timestamp from source
    - content_hash (string, optional)       → deduplication hash of normalized payload

CATALOG-only Attributes:
    - SK = CATALOG#{catalog_id}
    - reference metadata (title, vendor, tags, etc.)

VARIANT-only Attributes:
    - SK = VARIANT#{variant_id}
    - variant_id (string)
    - variant_title (string)
    - sku (string, optional)
    - options (map)                → e.g. {"Size":"Large","Color":"Black"}
    - price_num (number, Decimal)  → price
    - price_sort (string)          → zero-padded for lexical sort, e.g. "00000070.00"
    - compare_at_num (number, Decimal, optional)
    - inventory_tracked (bool)
    - inventory_policy (string)    → "deny" | "continue"
    - available_qty (number)
    - in_stock (bool)
    - rules (map, optional)        → e.g. {"allowed_quantities":[5,10,20], "min_order_qty":5}

Global Secondary Indexes (GSIs):
    - GSI1_CategoryPrice   → Browse by category (in-stock only), sorted by price
        PK: GSI1PK = TENANT#{tenant_id}#CATEGORY#{category_name}
        SK: GSI1SK = PRICE#{price_sort}#VARIANT#{variant_id}

    - GSI2_TagPrice        → Browse by tag (in-stock only), sorted by price
        PK: GSI2PK = TENANT#{tenant_id}#TAG#{tag}
        SK: GSI2SK = PRICE#{price_sort}#VARIANT#{variant_id}

    - GSI3_CollectionPrice → Browse by collection (in-stock only), sorted by price
        PK: GSI3PK = TENANT#{tenant_id}#COLLECTION#{collection}
        SK: GSI3SK = PRICE#{price_sort}#VARIANT#{variant_id}

    - GSI4_TitlePrefix     → Tenant-scoped title prefix search (typeahead)
        PK: GSI4PK = TENANT#{tenant_id}#TITLE
        SK: GSI4SK = <normalized_lower(title)>

Indexing Strategy:
    - Always set GSI4 (title prefix) for search.
    - Only set GSI1/2/3 if in_stock=True (sparse indexes).
      Remove those attributes when stock drops to 0.
"""

import boto3
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
from boto3.dynamodb.conditions import Key, Attr
from datetime import datetime, timezone
import hashlib
import json

# ---------------------------- helpers ----------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def pk_tenant(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"

def sk_catalog(catalog_id: str) -> str:
    return f"CATALOG#{catalog_id}"

def sk_variant(variant_id: str) -> str:
    return f"VARIANT#{variant_id}"

def zero_pad_price(price: float, width: int = 11, decimals: int = 2) -> str:
    return f"{float(price):0{width}.{decimals}f}"

def to_decimal_or_none(value) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(str(value))

def norm_title(s: str) -> str:
    return " ".join(str(s).lower().split())

def compute_content_hash(payload: Dict[str, Any]) -> str:
    safe_copy = {k: v for k, v in payload.items() if k not in ("updated_at",)}
    serialized = json.dumps(safe_copy, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

# ---------------------------- DAL ----------------------------

class CatalogDB:
    def __init__(self, table_name: str = "catalog", region_name: str = "eu-west-2"):
        self.table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)

    # ... (rest of the methods: put_product, put_variant, upsert_from_source, etc.)


    # ----------- Product CRUD -----------

    def put_catalog_item(self, tenant_id: str, catalog_item: Dict[str, Any]) -> None:
        """
        Upsert a PRODUCT item. Expects:
        {
          "product_id": str,
          "handle": str,
          "title": str,
          "vendor": str (opt),
          "category": {name, path[], source, confidence} (opt),
          "tags": [..],
          "collections": [..],
          "default_image": str (opt),
          "status": "active|draft|archived" (opt),
          "updated_at": ISO8601
        }
        """
        item = dict(catalog_item)  # shallow copy
        item["PK"] = pk_tenant(tenant_id)
        item["SK"] = sk_catalog(item["catalog_id"])
        item["entity"] = "CATALOG"
        if "category" in item and item["category"]:
            item["category_name"] = item["category"].get("name")
        self.table.put_item(Item=item)

    def get_catalog_item(self, tenant_id: str, catalog_id: str) -> Optional[Dict[str, Any]]:
        resp = self.table.get_item(Key={"PK": pk_tenant(tenant_id), "SK": sk_catalog(catalog_id)})
        return resp.get("Item")

    # ----------- Variant CRUD -----------

    def put_variant(self, tenant_id: str, variant: Dict[str, Any]) -> None:
        """
        Upsert a VARIANT item. Expects:
        {
          "variant_id": str,
          "product_id": str,
          "handle": str,
          "title": str,                 # product title (shared)
          "variant_title": str,
          "sku": str (opt),
          "category_name": str (opt),
          "tags": [..],
          "collections": [..],
          "price_num": number,
          "compare_at_num": number (opt),
          "options": map,
          "image_src": str (opt),
          "inventory_tracked": bool,
          "inventory_policy": "deny|continue",
          "available_qty": int,
          "in_stock": bool,
          "updated_at": ISO8601
        }
        """
        it = dict(variant)
        it["PK"] = pk_tenant(tenant_id)
        it["SK"] = sk_variant(it["variant_id"])
        it["entity"] = "VARIANT"

        # numeric conversions + price_sort
        if "price_num" in it:
            it["price_sort"] = zero_pad_price(it["price_num"])
            it["price_num"] = to_decimal_or_none(it["price_num"])
        if "compare_at_num" in it:
            it["compare_at_num"] = to_decimal_or_none(it.get("compare_at_num"))

        # title prefix index (always present for typeahead)
        if "title" in it and it["title"]:
            it["GSI4PK"] = f"TENANT#{tenant_id}#TITLE"
            it["GSI4SK"] = norm_title(it["title"])

        # sparse browse indexes (only when in_stock)
        if it.get("in_stock"):
            self._attach_sparse_browse_indexes(tenant_id, it)

        self.table.put_item(Item=it)

    def get_variant(self, tenant_id: str, variant_id: str) -> Optional[Dict[str, Any]]:
        resp = self.table.get_item(Key={"PK": pk_tenant(tenant_id), "SK": sk_variant(variant_id)})
        return resp.get("Item")

    def delete_catalog_item_and_variants(self, tenant_id: str, catalog_id: str) -> int:
        """
        Deletes the PRODUCT item and all VARIANT items for the given product_id.
        Returns number of deleted items.
        """
        deleted = 0
        # delete catalog item
        prod_key = {"PK": pk_tenant(tenant_id), "SK": sk_catalog(catalog_id)}
        try:
            self.table.delete_item(Key=prod_key)
            deleted += 1
        except Exception:
            pass

        # sweep tenant partition for variants of this product (bounded, batched)
        last_key = None
        while True:
            kwargs = {
                "KeyConditionExpression": Key("PK").eq(pk_tenant(tenant_id)),
                "FilterExpression": Attr("entity").eq("VARIANT") & Attr("catalog_id").eq(str(catalog_id)),
                "ProjectionExpression": "PK, SK",
                "Limit": 200
            }
            if last_key:
                kwargs["ExclusiveStartKey"] = last_key
            resp = self.table.query(**kwargs)
            items = resp.get("Items", [])
            for it in items:
                self.table.delete_item(Key={"PK": it["PK"], "SK": it["SK"]})
                deleted += 1
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break

        return deleted

    # ----------- Updates (keep GSIs in sync) -----------

    def update_inventory(self, tenant_id: str, variant_id: str, available_qty: int,
                         inventory_policy: Optional[str] = None) -> bool:
        """
        Updates available_qty, recomputes in_stock (using inventory_policy + tracked flag),
        and adds/removes sparse browse GSIs accordingly.
        """
        it = self.get_variant(tenant_id, variant_id)
        if not it:
            return False

        tracked = bool(it.get("inventory_tracked", True))
        policy = (inventory_policy or it.get("inventory_policy") or "deny")
        in_stock = True if not tracked else (available_qty > 0 or policy == "continue")

        # Build SET and REMOVE parts separately
        set_names = {}
        set_vals = {
            ":aq": available_qty,
            ":ins": in_stock,
            ":u": now_iso()
        }
        set_parts = ["available_qty = :aq", "in_stock = :ins", "updated_at = :u"]
        remove_names = []

        # sparse GSIs
        if in_stock:
            # ensure we attach
            self._gsi_set_for_variant(it, tenant_id, set_names, set_vals, set_parts)
        else:
            # ensure we remove
            for k in ("GSI1PK","GSI1SK","GSI2PK","GSI2SK","GSI3PK","GSI3SK"):
                remove_names.append(k)

        update_expr = self._compose_update_expression(set_parts, remove_names, set_names)

        resp = self.table.update_item(
            Key={"PK": pk_tenant(tenant_id), "SK": sk_variant(variant_id)},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=(set_names if set_names else None),
            ExpressionAttributeValues=set_vals,
            ReturnValues="UPDATED_NEW"
        )
        return "Attributes" in resp

    def update_price(self, tenant_id: str, variant_id: str,
                     new_price: float, compare_at: Optional[float] = None) -> bool:
        """
        Updates price_num + price_sort (+ compare_at_num if given) and rebuilds browse GSIs if in_stock.
        """
        it = self.get_variant(tenant_id, variant_id)
        if not it:
            return False

        price_sort = zero_pad_price(new_price)

        set_names = {}
        set_vals = {
            ":pn": to_decimal_or_none(new_price),
            ":ps": price_sort,
            ":u": now_iso()
        }
        set_parts = ["price_num = :pn", "price_sort = :ps", "updated_at = :u"]
        remove_names = []

        if compare_at is not None:
            set_vals[":cp"] = to_decimal_or_none(compare_at)
            set_parts.append("compare_at_num = :cp")

        # If still in stock, re-attach GSIs with new price_sort
        if it.get("in_stock"):
            # Temporarily override price_sort on local copy for key build
            it_local = dict(it)
            it_local["price_sort"] = price_sort
            self._gsi_set_for_variant(it_local, tenant_id, set_names, set_vals, set_parts)

        update_expr = self._compose_update_expression(set_parts, remove_names, set_names)

        resp = self.table.update_item(
            Key={"PK": pk_tenant(tenant_id), "SK": sk_variant(variant_id)},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=(set_names if set_names else None),
            ExpressionAttributeValues=set_vals,
            ReturnValues="UPDATED_NEW"
        )
        return "Attributes" in resp

    def update_title(self, tenant_id: str, variant_id: str, new_title: str) -> bool:
        """
        Updates display title and keeps title prefix index (GSI4) in sync.
        """
        resp = self.table.update_item(
            Key={"PK": pk_tenant(tenant_id), "SK": sk_variant(variant_id)},
            UpdateExpression="SET title = :t, GSI4PK = :g4pk, GSI4SK = :g4sk, updated_at = :u",
            ExpressionAttributeValues={
                ":t": new_title,
                ":g4pk": f"TENANT#{tenant_id}#TITLE",
                ":g4sk": norm_title(new_title),
                ":u": now_iso()
            },
            ReturnValues="UPDATED_NEW"
        )
        return "Attributes" in resp

    # ----------- Queries -----------

    def list_by_category(self, tenant_id: str, category: str, limit: int = 24,
                         last_key: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        resp = self.table.query(
            IndexName="GSI1_CategoryPrice",
            KeyConditionExpression=Key("GSI1PK").eq(f"TENANT#{tenant_id}#CATEGORY#{category}"),
            Limit=limit,
            ScanIndexForward=True,
            **({"ExclusiveStartKey": last_key} if last_key else {})
        )
        return resp.get("Items", []), resp.get("LastEvaluatedKey")

    def list_by_tag(self, tenant_id: str, tag: str, limit: int = 24,
                    last_key: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        resp = self.table.query(
            IndexName="GSI2_TagPrice",
            KeyConditionExpression=Key("GSI2PK").eq(f"TENANT#{tenant_id}#TAG#{tag}"),
            Limit=limit,
            ScanIndexForward=True,
            **({"ExclusiveStartKey": last_key} if last_key else {})
        )
        return resp.get("Items", []), resp.get("LastEvaluatedKey")

    def list_by_collection(self, tenant_id: str, collection: str, limit: int = 24,
                           last_key: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        resp = self.table.query(
            IndexName="GSI3_CollectionPrice",
            KeyConditionExpression=Key("GSI3PK").eq(f"TENANT#{tenant_id}#COLLECTION#{collection}"),
            Limit=limit,
            ScanIndexForward=True,
            **({"ExclusiveStartKey": last_key} if last_key else {})
        )
        return resp.get("Items", []), resp.get("LastEvaluatedKey")

    def search_title_prefix(self, tenant_id: str, prefix: str, limit: int = 20,
                            last_key: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        resp = self.table.query(
            IndexName="GSI4_TitlePrefix",
            KeyConditionExpression=Key("GSI4PK").eq(f"TENANT#{tenant_id}#TITLE") & Key("GSI4SK").begins_with(norm_title(prefix)),
            Limit=limit,
            **({"ExclusiveStartKey": last_key} if last_key else {})
        )
        return resp.get("Items", []), resp.get("LastEvaluatedKey")

    def list_variants_for_catalog_item(self, tenant_id: str, catalog_id: str, limit: int = 100,
                                       last_key: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Convenience helper: scans tenant partition (key-conditioned) and filters VARIANTs of a product.
        Keep variants light (<1KB) so paging is fast.
        """
        kwargs = {
            "KeyConditionExpression": Key("PK").eq(pk_tenant(tenant_id)),
            "FilterExpression": Attr("entity").eq("VARIANT") & Attr("catalog_id").eq(str(catalog_id)),
            "Limit": limit
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = self.table.query(**kwargs)
        return resp.get("Items", []), resp.get("LastEvaluatedKey")

    # ---------------- internal utilities ----------------

    def _attach_sparse_browse_indexes(self, tenant_id: str, it: Dict[str, Any]) -> None:
        """
        Mutates item dict to set GSI1/2/3 keys for browse queries, using:
          - category_name (single)
          - first tag (if any)
          - first collection (if any)
        """
        price_sort = it.get("price_sort")
        vid = it.get("variant_id")
        cat = it.get("category_name")

        if cat:
            it["GSI1PK"] = f"TENANT#{tenant_id}#CATEGORY#{cat}"
            it["GSI1SK"] = f"PRICE#{price_sort}#VARIANT#{vid}"

        tag = (it.get("tags") or [None])[0]
        if tag:
            it["GSI2PK"] = f"TENANT#{tenant_id}#TAG#{tag}"
            it["GSI2SK"] = f"PRICE#{price_sort}#VARIANT#{vid}"

        col = (it.get("collections") or [None])[0]
        if col:
            it["GSI3PK"] = f"TENANT#{tenant_id}#COLLECTION#{col}"
            it["GSI3SK"] = f"PRICE#{price_sort}#VARIANT#{vid}"

        # Note: GSI4 (title prefix) is handled in put_variant/update_title

    def _gsi_set_for_variant(self, it: Dict[str, Any], tenant_id: str,
                             set_names: Dict[str, str], set_vals: Dict[str, Any], set_parts: List[str]) -> None:
        """
        Adds 'SET' clauses and values for GSI1/2/3 given a variant dict (with price_sort, category_name, tags, collections).
        """
        vid = it.get("variant_id")
        price_sort = it.get("price_sort")

        if it.get("category_name"):
            set_names["#g1pk"] = "GSI1PK"
            set_names["#g1sk"] = "GSI1SK"
            set_vals[":g1pk"] = f"TENANT#{tenant_id}#CATEGORY#{it['category_name']}"
            set_vals[":g1sk"] = f"PRICE#{price_sort}#VARIANT#{vid}"
            set_parts.append("#g1pk = :g1pk")
            set_parts.append("#g1sk = :g1sk")

        tag = (it.get("tags") or [None])[0]
        if tag:
            set_names["#g2pk"] = "GSI2PK"
            set_names["#g2sk"] = "GSI2SK"
            set_vals[":g2pk"] = f"TENANT#{tenant_id}#TAG#{tag}"
            set_vals[":g2sk"] = f"PRICE#{price_sort}#VARIANT#{vid}"
            set_parts.append("#g2pk = :g2pk")
            set_parts.append("#g2sk = :g2sk")

        col = (it.get("collections") or [None])[0]
        if col:
            set_names["#g3pk"] = "GSI3PK"
            set_names["#g3sk"] = "GSI3SK"
            set_vals[":g3pk"] = f"TENANT#{tenant_id}#COLLECTION#{col}"
            set_vals[":g3sk"] = f"PRICE#{price_sort}#VARIANT#{vid}"
            set_parts.append("#g3pk = :g3pk")
            set_parts.append("#g3sk = :g3sk")

    @staticmethod
    def _compose_update_expression(set_parts: List[str],
                                   remove_names: List[str],
                                   set_names: Dict[str, str]) -> str:
        """
        Builds a valid UpdateExpression combining SET and REMOVE clauses.
        """
        expr = ""
        if set_parts:
            expr += "SET " + ", ".join(set_parts)
        if remove_names:
            # map each to ExpressionAttributeNames entry if not already aliased
            rem_aliases = []
            for attr in remove_names:
                alias = f"#rem_{attr}"
                set_names[alias] = attr
                rem_aliases.append(alias)
            if expr:
                expr += " "
            expr += "REMOVE " + ", ".join(rem_aliases)
        return expr
