# Customer-Facing Catalog Service Methods

## Overview
The `CatalogService` class has been cleaned up to contain **only customer-facing read operations**. All admin operations (create, update, delete) have been removed.

---

## ✅ Available Methods (Customer-Facing)

### **Product Browsing**

#### `list_catalog_by_categories(tenant_id: str) -> Dict[str, Any]`
Browse all available products grouped by category.
```python
result = service.list_catalog_by_categories(tenant_id="tenant-123")
# Returns: {"success": True, "data": {"🍗 Meat": [...], "🥬 Vegetables": [...]}}
```
- Filters: `in_stock=True`, `available_qty > 0`, `status='active'`
- Groups by category with emoji prefixes
- Sorted alphabetically within each category

#### `list_catalog_by_categories_formatted(tenant_id: str, limit_categories: int = 10, examples_per_category: int = 2) -> Dict[str, Any]`
Get formatted bullet-point text for display.
```python
result = service.list_catalog_by_categories_formatted(
    tenant_id="tenant-123",
    limit_categories=10,
    examples_per_category=5
)
# Returns formatted text suitable for chatbot/UI display
```

---

### **Product Search**

#### `search_catalog(catalog_name: str, tenant_id: str, catalog_items: Dict = None, threshold: float = 0.6) -> Dict[str, Any]`
Search for products by name with fuzzy matching support.
```python
# Fast GSI4 prefix search (default)
result = service.search_catalog("chicken", tenant_id="tenant-123")

# Fuzzy search mode (supports typos)
catalog_dict = service.list_catalog_flat(tenant_id="tenant-123")
result = service.search_catalog("chiken", catalog_items=catalog_dict, threshold=0.6)
```
- **Without `catalog_items`**: Uses GSI4_TitlePrefix (fast O(log n))
- **With `catalog_items`**: Uses fuzzy matching (handles typos, slower O(n))
- Multi-tenant isolated: searches only within tenant's products

#### `list_catalog_flat(tenant_id: str, limit: int = 10_000) -> Dict[str, str]`
Get simple mapping of catalog_id → title.
```python
catalog_dict = service.list_catalog_flat(tenant_id="tenant-123")
# Returns: {"catalog-001": "Frozen Chicken", "catalog-002": "Fresh Tomatoes", ...}
```
- Used for fuzzy search pre-loading
- Filters: `in_stock=True`
- Returns max 10,000 items

---

### **Product Details**

#### `get_catalog_item(catalog_id: str, tenant_id: str) -> Dict[str, Any]`
Get catalog item metadata (reference data).
```python
result = service.get_catalog_item(catalog_id="catalog-123", tenant_id="tenant-123")
# Returns: {"success": True, "data": {catalog_id, handle, title, vendor, category, ...}}
```

#### `get(tenant_id: str, catalog_id: str) -> Optional[Dict[str, Any]]`
Legacy method - same as `get_catalog_item` but returns raw data (no wrapper).
```python
item = service.get(tenant_id="tenant-123", catalog_id="catalog-123")
# Returns: {catalog_id, handle, title, ...} or None
```

#### `get_variant(tenant_id: str, variant_id: str) -> Dict[str, Any]`
Get specific variant details (purchasable SKU).
```python
result = service.get_variant(tenant_id="tenant-123", variant_id="variant-456")
# Returns: {"success": True, "data": {variant_id, price_num, sku, options, available_qty, ...}}
```

#### `list_variants_for_catalog_item(tenant_id: str, catalog_id: str, limit: int = 100) -> Dict[str, Any]`
List all variants for a catalog item (e.g., different sizes/colors).
```python
result = service.list_variants_for_catalog_item(
    tenant_id="tenant-123",
    catalog_id="catalog-123",
    limit=100
)
# Returns: {"success": True, "data": {"variants": [...], "last_key": ...}}
```
- Use case: Show customer all available options for a product
- Example: T-shirt with variants for Small/Medium/Large

---

## ❌ Removed Methods (Admin Operations)

The following admin methods have been **removed** from the customer-facing service:

### Removed Create Operations:
- ❌ `create_catalog_item()` - Create new catalog reference
- ❌ `create_variant()` - Create new variant/SKU

### Removed Update Operations:
- ❌ `update_inventory()` - Update stock levels
- ❌ `update_price()` - Update pricing
- ❌ `update_variant_title()` - Update variant title

### Removed Delete Operations:
- ❌ `delete_catalog_item()` - Delete catalog item and variants

---

## Admin Operations Alternative

If you need admin operations, use the repository layer directly:

```python
from db.catalog import CatalogDB

# For admin operations
db = CatalogDB()

# Create operations
db.put_catalog_item(tenant_id, catalog_item_data)
db.put_variant(tenant_id, variant_data)

# Update operations
db.update_inventory(tenant_id, variant_id, available_qty, inventory_policy)
db.update_price(tenant_id, variant_id, new_price, compare_at)
db.update_title(tenant_id, variant_id, new_title)

# Delete operations
db.delete_catalog_item_and_variants(tenant_id, catalog_id)
```

Or create a separate `CatalogAdminService` class that wraps these operations.

---

## Performance Characteristics

### Multi-Tenant Isolation
- Each search/query is isolated to ONE tenant's data
- With 500 products/tenant: **O(500)** regardless of total tenant count
- Total system: 1000 tenants × 500 products = 500K products (all isolated)

### Search Performance
| Method | Time Complexity | Typical Latency | Use Case |
|--------|----------------|-----------------|----------|
| `search_catalog()` (GSI4) | O(log n) | ~50ms | Fast prefix search |
| `search_catalog()` (fuzzy) | O(n) | ~70ms | Typo-tolerant search |
| `list_catalog_by_categories()` | O(n) | ~100ms | Browse all products |
| `get_catalog_item()` | O(1) | ~10ms | Direct lookup |
| `get_variant()` | O(1) | ~10ms | Direct lookup |

### Scalability
- ✅ **Current setup:** Scales perfectly with 500 products/tenant
- ✅ **Up to 1000 products/tenant:** Still efficient
- ⚠️ **Beyond 5000 products/tenant:** Consider Elasticsearch/Algolia for fuzzy search

---

## Usage Examples

### Example 1: Customer browsing catalog
```python
service = CatalogService()

# Show categorized product list
result = service.list_catalog_by_categories_formatted(
    tenant_id="tenant-123",
    limit_categories=10,
    examples_per_category=5
)

# Display to customer
print(result["data"]["text"])
```

### Example 2: Customer searching for product
```python
# Step 1: Try fast prefix search first
result = service.search_catalog("chicken", tenant_id="tenant-123")

if result["success"] and result["data"]["count"] > 0:
    # Found matches
    matches = result["data"]["matches"]
else:
    # Step 2: Fall back to fuzzy search for typos
    catalog_dict = service.list_catalog_flat(tenant_id="tenant-123")
    result = service.search_catalog(
        "chiken",  # typo
        catalog_items=catalog_dict,
        threshold=0.6
    )
```

### Example 3: Customer viewing product details
```python
# Get catalog item
catalog = service.get_catalog_item(catalog_id="catalog-123", tenant_id="tenant-123")

# Get all variants (sizes, colors, etc.)
variants = service.list_variants_for_catalog_item(
    tenant_id="tenant-123",
    catalog_id="catalog-123"
)

# Customer can now see all options and pick a variant
for variant in variants["data"]["variants"]:
    print(f"{variant['variant_title']} - ${variant['price_num']}")
```

---

## Key Design Principles

1. **Read-Only:** All methods are read-only queries
2. **Multi-Tenant:** Every method requires `tenant_id` for isolation
3. **Customer-Centric:** Filters for `in_stock`, `active`, and `available_qty > 0`
4. **Performance:** Uses GSI indexes where possible for fast queries
5. **Fuzzy Search:** Supports typo-tolerant search when needed
6. **Scalable:** O(n) for tenant's products, not entire system

---

## Next Steps

If you need admin operations:
1. Create `CatalogAdminService` class in a separate file
2. Use `db.catalog.CatalogDB` directly
3. Implement proper authorization/authentication for admin endpoints
4. Keep admin API separate from customer-facing API

