import requests

BASE_URL = "https://world.openfoodfacts.org/category"

ALLOWED_GROCERY_CATEGORIES = [
    "groceries",
    "food",
    "beverages",
    "african-groceries"
]

def fetch_category(category, page=1, page_size=100):
    """
    Fetch products from OpenFoodFacts by category.
    """
    url = f"{BASE_URL}/{category}/{page}.json?page_size={page_size}"
    print(f"  Fetching: {url}")
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    
    # Debug: Check response content
    print(f"  Response status: {resp.status_code}")
    print(f"  Response content type: {resp.headers.get('content-type', 'unknown')}")
    print(f"  Response length: {len(resp.text)}")
    
    if not resp.text.strip():
        print(f"  Warning: Empty response for {url}")
        return []
    
    try:
        data = resp.json()
        return data.get("products", [])
    except requests.exceptions.JSONDecodeError as e:
        print(f"  Error: Invalid JSON response for {url}")
        print(f"  Response preview: {resp.text[:200]}...")
        return []

def fetch_all_grocery_products(limit=500):
    """
    Fetch across all ALLOWED_GROCERY_CATEGORIES.
    """
    all_products = []
    for cat in ALLOWED_GROCERY_CATEGORIES:
        print(f"Fetching category: {cat}")
        page = 1
        while len(all_products) < limit:
            products = fetch_category(cat, page=page, page_size=100)
            if not products:
                break
            all_products.extend(products)
            print(f"  → got {len(products)} items (total {len(all_products)})")
            page += 1
            if len(all_products) >= limit:
                break
    return all_products[:limit]

def main():
    groceries = fetch_all_grocery_products(limit=200)
    print(f"Fetched {len(groceries)} grocery products")
    # Show a sample
    for g in groceries[:10]:
        print("-", g.get("product_name") or g.get("generic_name") or g.get("brands"))

if __name__ == "__main__":
    main()
