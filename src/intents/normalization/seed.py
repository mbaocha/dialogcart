import requests
import re
import json

OUTPUT_FILE = "global_entities_products.json"

def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())

def to_dynamo_item(canonical: str, category: str):
    return {
        "pk": f"PRODUCT#{canonical}",
        "sk": "PRODUCT",
        "type": "product",
        "canonical": canonical,
        "synonyms": [],   # leave empty for now, can be enriched later
        "category": [category]
    }

def load_baseline():
    """Hard-coded African groceries starter list."""
    groceries = [
        "yam", "plantain", "garri", "palm oil", "beans", "egusi",
        "ogbono", "crayfish", "catfish", "stockfish", "mackerel",
        "ponmo", "cassava", "fufu", "semo", "okro", "tomato", "pepper"
    ]
    return [to_dynamo_item(normalize_name(p), "groceries") for p in groceries]

def load_google_taxonomy():
    """Fetch Google Product Taxonomy and filter fashion + beauty terms."""
    url = "https://www.google.com/basepages/producttype/taxonomy.en-US.txt"
    resp = requests.get(url)
    products = []
    for line in resp.text.splitlines():
        if not line or line.startswith("#"):
            continue
        name = line.split(">")[-1].strip()
        canonical = normalize_name(name)
        if any(k in line.lower() for k in ["apparel", "clothing", "shoes", "accessories"]):
            products.append(to_dynamo_item(canonical, "fashion"))
        if any(k in line.lower() for k in ["beauty", "cosmetics", "makeup", "fragrance", "hair"]):
            products.append(to_dynamo_item(canonical, "beauty"))
    return products

def load_open_food_facts(limit=200, search="africa"):
    """
    Query Open Food Facts for grocery products with search term filter.
    Fallbacks to African groceries but you can pass e.g. "plantain" or "yam".
    """
    url = f"https://world.openfoodfacts.org/cgi/search.pl"
    params = {
        "search_terms": search,
        "json": 1,
        "page_size": limit,
    }
    resp = requests.get(url, params=params)

    if resp.status_code != 200:
        print(f"⚠ Open Food Facts request failed: {resp.status_code}")
        return []

    try:
        data = resp.json()
    except Exception as e:
        print("⚠ Failed to parse Open Food Facts response as JSON:", e)
        print(resp.text[:300])
        return []

    products = []
    for p in data.get("products", []):
        name = p.get("product_name")
        categories = p.get("categories", "").lower()
        if not name:
            continue

        # Filter down to groceries / African groceries / beauty keywords
        if any(keyword in categories for keyword in ["africa", "plantain", "yam", "cassava", "rice", "beans", "oil", "spices", "beauty", "cosmetic"]):
            products.append({
                "canonical": name.strip().lower(),
                "type": "product",
                "category": ["groceries"],  # or ["beauty"] if cosmetics
                "synonyms": [],
            })

    return products

def dedupe(products):
    seen = set()
    deduped = []
    for p in products:
        key = (p["canonical"], tuple(sorted(p["category"])))
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    return deduped

def main():
    baseline = load_baseline()
    google = load_google_taxonomy()
    off = load_open_food_facts(limit=200)

    all_products = baseline + google + off
    clean = dedupe(all_products)

    print(f"Collected {len(all_products)} → {len(clean)} unique DynamoDB items")
    with open(OUTPUT_FILE, "w") as f:
        json.dump(clean, f, indent=2)
    print(f"✓ Saved {len(clean)} items to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
