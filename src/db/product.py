"""
DynamoDB Table: b_products

Partition key: id (string)

Attributes:
    - id (string, PK)
    - name (string)
    - unit (string)        # e.g., 'kg', 'piece'
    - price (number/decimal)
    - allowed_quantities (list[int] or dict)
    - available_quantity (number/decimal, optional)
    - category (string, optional)   # NEW: e.g., 'Rice', 'Tubers', 'Beverages', etc.
    - description (string, optional)
    - created_at (number/decimal)

# Note: Add additional attributes as your schema evolves.
"""

import boto3
from decimal import Decimal
import uuid
from typing import Optional, List, Dict, Any, Union
from db.enums import ProductUnit

class ProductDB:
    def __init__(self, table_name="b_products"):
        self.table = boto3.resource('dynamodb').Table(table_name)

    def create_product(
        self,
        name: str,
        unit: str,
        price: float,
        allowed_quantities: Optional[Union[Dict[str, Any], List[int]]] = None,
        available_quantity: Optional[float] = None,
        category: Optional[str] = None,    # NEW
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Validate unit
        if unit not in [u.value for u in ProductUnit]:
            raise ValueError(
                f"Invalid unit '{unit}'. Allowed units: {[u.value for u in ProductUnit]}"
            )

        product_id = str(uuid.uuid4())
        item = {
            "id": product_id,
            "name": name,
            "unit": unit,  # e.g. "kg", "piece", etc.
            "price": Decimal(str(price)),
            "created_at": Decimal(str(uuid.uuid1().time)),
            "allowed_quantities": allowed_quantities if allowed_quantities is not None else {"min": 1},
        }
        if available_quantity is not None:
            item["available_quantity"] = Decimal(str(available_quantity))
        if category:
            item["category"] = category
        if description:
            item["description"] = description

        self.table.put_item(Item=item)
        return item

    def get_product(self, product_id: str) -> Optional[Dict[str, Any]]:
        response = self.table.get_item(Key={"id": product_id})
        return response.get("Item")

    def search_products(self, product_name: str) -> List[Dict[str, Any]]:
        """
        Searches for products by name (contains case-insensitive).
        Only returns enabled products.
        NOTE: For large tables, use an inverted index or ElasticSearch for scalable text search.
        """
        response = self.table.scan()  # DynamoDB scan: expensive for large tables!
        items = response.get("Items", [])
        result = []
        for product in items:
            # Check if product name matches
            if product_name.lower() in product.get("name", "").lower():
                # Only include enabled products
                if product.get("status", "enabled") == "enabled":
                    result.append(product)
        return result

    def update_product_quantity(self, product_id: str, new_quantity: float) -> bool:
        response = self.table.update_item(
            Key={"id": product_id},
            UpdateExpression="set available_quantity = :q",
            ExpressionAttributeValues={":q": Decimal(str(new_quantity))},
            ReturnValues="UPDATED_NEW"
        )
        return "Attributes" in response

    def list_products(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Lists products with optional limit.
        Only returns enabled products.
        """
        response = self.table.scan(Limit=limit)
        items = response.get("Items", [])
        
        # Filter out disabled products
        enabled_items = []
        for item in items:
            if item.get("status", "enabled") == "enabled":
                enabled_items.append(item)
        
        return enabled_items



    # Add more CRUD methods as needed
