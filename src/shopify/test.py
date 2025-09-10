import requests
import json

SHOP = ""
TOKEN = ""

base_url = f"https://{SHOP}.myshopify.com/admin/api/2025-07/products.json"
headers = {"X-Shopify-Access-Token": TOKEN}

all_products = []
params = {"limit": 250}

while True:
    resp = requests.get(base_url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()
    products = data.get("products", [])
    all_products.extend(products)

    # Look for next page in response headers
    link_header = resp.headers.get("Link")
    if link_header and 'rel="next"' in link_header:
        # Extract page_info param from link header
        import re
        match = re.search(r'page_info=([^&>]+)', link_header)
        if match:
            page_info = match.group(1)
            params = {"limit": 250, "page_info": page_info}
        else:
            break
    else:
        break

print(f"Fetched {len(all_products)} products")
print(json.dumps(all_products[:2], indent=2))  # show first 2 products
