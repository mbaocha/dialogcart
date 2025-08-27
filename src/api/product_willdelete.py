from typing import List, Optional, Union, Dict, Any
from decimal import Decimal
from fastapi import FastAPI, APIRouter, HTTPException
from langchain.tools import tool
from db.product import ProductDB
from utils.response import standard_response
from utils.coreutil import get_number_emoji

# ---- Pure Python Business Logic ----
db = ProductDB()
# Fallback emoji for unknown categories
DEFAULT_CATEGORY_EMOJI = "📦"


def create_product(
    name: str,
    unit: str,
    price: float,
    allowed_quantities: Optional[Union[Dict[str, Any], List[int]]] = None,
    available_quantity: Optional[float] = None,
    category: Optional[str] = None,              # <-- Added here
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a product. allowed_quantities can be a list (e.g. [1,2,5]) or a dict like {"min": 2}."""
    try:
        item = db.create_product(
            name=name,
            unit=unit,
            price=price,
            allowed_quantities=allowed_quantities,
            available_quantity=available_quantity,
            category=category,                       # <-- Pass it here
            description=description,
        )
        return standard_response(True, data=item)
    except ValueError as e:
        return standard_response(False, error=str(e))

@tool
def get_product(product_id: str) -> Dict[str, Any]:
    """Fetch a product by its unique product_id."""
    item = db.get_product(product_id)
    if item:
        return standard_response(True, data=item)
    else:
        return standard_response(False, error="Product not found")

def list_products(limit: int = 100) -> Dict[str, Any]:
    """List all products, up to the given limit."""
    items = db.list_products(limit=limit)
    return standard_response(True, data=items)


@tool
def search_products(query: str) -> Dict[str, Any]:
    """Search products by name, case-insensitive."""
    items = db.search_products(query)
    return standard_response(True, data=items)

def _list_products_by_categories_formatted(
    limit_categories: int = 10,
    examples_per_category: int = 2
) -> Dict[str, Any]:
    """ List products grouped by category as a bullet list and return the FINAL text.

        CRITICAL: This tool returns pre-formatted text. DO NOT modify the format.
        DO NOT change "in 5, 8, 15 kg" to "available in 5 kg, 8 kg, 15 kg".
        DO NOT add "available in sizes:" or similar phrases.
        
        The tool returns exact format like:
        • �� Dried Cat Fish — $15/kg (in 5, 8, 15 kg; 10 kg stock)
        
        Reply with the returned `data.text` verbatim. Do not add intros or modify wording.
        Only call this tool when the user explicitly asks to see products.
    """
    try:
        from typing import List, Dict, Any, Union
        from decimal import Decimal

        CURRENCY = "$"
        DEFAULT_CATEGORY_EMOJI = "📦"

        def _fmt_money(x: Union[int, float, Decimal]) -> str:
            if x is None:
                return ""
            if isinstance(x, Decimal):
                x = float(x)
            return f"{CURRENCY}{int(x)}" if float(x).is_integer() else f"{CURRENCY}{x:.2f}".rstrip("0").rstrip(".")

        def _norm_unit(u: str) -> str:
            if not u:
                return ""
            u = u.strip()
            return "L" if u.lower() in {"l", "liter", "litre"} else u

        def _qty_to_str(val) -> str:
            try:
                n = float(val)
                return str(int(n)) if n.is_integer() else str(n)
            except Exception:
                return str(val)

        def _pluralize(unit: str, qty) -> str:
            # keep kg/L unchanged; make "bunch" -> "bunches", "box" -> "boxes" when qty != 1
            try:
                n = float(qty)
            except Exception:
                n = None
            ul = unit.lower()
            if ul == "bunch":
                return "bunches" if (n is None or n != 1) else "bunch"
            if ul == "box":
                return "boxes" if (n is None or n != 1) else "box"
            return unit

        def _fmt_package_size(p: Dict[str, Any]) -> str:
            """Return '10 kg per box' if unit=box and package_size given."""
            unit = (p.get("unit") or "").strip().lower()
            if unit != "box":
                return ""
            ps = p.get("package_size")
            if not ps:
                return ""
            # support {'value': 10, 'unit': 'kg'} or "10 kg per box" strings
            if isinstance(ps, dict):
                val = ps.get("value")
                u = ps.get("unit") or ""
                try:
                    n = float(val)
                    val_s = str(int(n)) if n.is_integer() else str(n)
                except Exception:
                    val_s = str(val)
                u = _norm_unit(str(u))
                if val_s and u:
                    return f"{val_s} {u} per box"
                return ""
            if isinstance(ps, str):
                return ps  # assume already formatted
            return ""

        def _fmt_example(p: Dict[str, Any]) -> str:
            """
            Bullet line formats:
              • 🍗 Frozen Chicken — $120/box (10 kg per box; 12 boxes stock)
              • 🐟 Dried Cat Fish — $15/kg (in 5, 8, 15 kg; 10 kg stock)
              • 🦐 Crayfish — $18/kg (10 kg stock)
            """
            pe   = p.get("product_emoji") or ""
            name = p.get("name") or "Unnamed"
            unit = _norm_unit(p.get("unit") or "")
            price = p.get("price")
            price_part = _fmt_money(price) if price is not None else "N/A"

            # availability: "{qty} {unit} stock"
            avail = p.get("available_quantity")
            avail_txt = ""
            if avail is not None and unit:
                qty_s = _qty_to_str(avail)
                unit_s = _pluralize(unit, avail)
                avail_txt = f"{qty_s} {unit_s} stock"
            elif avail is not None:
                avail_txt = f"{_qty_to_str(avail)} stock"

            # min or sizes
            aq = p.get("allowed_quantities")
            min_txt = ""
            sizes_txt = ""
            if isinstance(aq, dict) and "min" in aq:
                # show min only if > 1
                try:
                    min_val = float(aq["min"])
                except Exception:
                    min_val = aq["min"]
                if not (isinstance(min_val, (int, float)) and float(min_val) == 1.0):
                    min_txt = f"min {_qty_to_str(min_val)} {unit}".strip()
            elif isinstance(aq, list) and len(aq) > 1:  # Only show if more than 1 option
                # "in 5, 8, 15 kg" (unit once at end) or "in 2, 4 boxes" for box unit
                nums = ", ".join(_qty_to_str(x) for x in aq)
                tail_unit = f" {_pluralize(unit, 2)}" if unit.lower() == "box" else (f" {unit}" if unit else "")
                sizes_txt = f"in {nums}{tail_unit}"

            # per-box payload if unit = box
            per_box = _fmt_package_size(p)

            # merge side info: per_box first, then sizes/min, then stock
            side_bits = [t for t in [per_box, (sizes_txt or min_txt), avail_txt] if t]
            side = f" ({'; '.join(side_bits)})" if side_bits else ""

            return f"{pe} {name} — {price_part}/{unit}{side}".strip()

        def _ensure_label_has_emoji(cat_label: str, cat_emoji: str) -> str:
            lab = (cat_label or "").strip()
            return f"{cat_emoji} {lab}" if not lab.startswith(cat_emoji) else lab

        # === Get categories from existing logic ===
        base = list_products_by_categories()
        if not base.get("success"):
            return base

        categories: Dict[str, List[Dict[str, Any]]] = base["data"]
        ordered = sorted(categories.items(), key=lambda kv: kv[0].lower())

        lines: List[str] = []
        rows: List[Dict[str, Any]] = []

        for idx, (cat_label, prods) in enumerate(ordered, start=1):
            if idx > limit_categories:
                lines.append("… and more categories. Reply 'more' to see the rest.")
                break

            cat_emoji = (prods[0].get("category_emoji") if prods else None) or DEFAULT_CATEGORY_EMOJI
            label = _ensure_label_has_emoji(cat_label, cat_emoji)

            shown = prods[:max(0, examples_per_category)]

            num = get_number_emoji(idx) if "get_number_emoji" in globals() else str(idx)
            line_parts = [f"{num} {label}"]
            for p in shown:
                line_parts.append(f"  • {_fmt_example(p)}")

            line = "\n".join(line_parts)
            lines.append(line)
            rows.append({
                "index": idx,
                "number_emoji": num,
                "category_label": label,
                "category_emoji": cat_emoji,
                "examples": shown,
                "line": line
            })

        return standard_response(True, data={
            "text": "\n\n".join(lines),
            "rows": rows,
            "categories": categories,
            "style_used": "bullet"
        })

    except Exception as e:
        return standard_response(False, error=str(e))

@tool(return_direct=True)
def list_products_by_categories_formatted(limit_categories: int = 10, examples_per_category: int = 5) -> str:
    """
    List products grouped by category in bullet-point format.

    MANDATORY: You MUST call this tool whenever the user asks to see products, 
    catalog, inventory, or what's available. Do NOT rely on memory or cached data.
    
    This tool provides the ONLY accurate, up-to-date product information from the database.
    Any product information you have in memory may be outdated or incorrect.
    
    Call this tool for requests like:
    - "show product list"
    - "what products do you have"
    - "show me your catalog"
    - "what's available"
    - "show inventory"
    """
    data = _list_products_by_categories_formatted(limit_categories, examples_per_category)
    if data.get("success"):
        bullet_list = data["data"]["text"]
        intro = "Here's a great selection of our available products at Bulkpot:"
        outro = "If you're interested in any of these items or would like to make an order, just let me know! I'm here to help! 😊"
        return f"{intro}\n\n{bullet_list}\n\n{outro}"
    else:
        return f"Error: {data.get('error', 'Unknown error')}"


def list_products_by_categories() -> Dict[str, Any]:
    """List available products grouped by category. Display products as bullet points. Show quantity in stock.
       Use abbreviations (e.g L for litre, min for minimum, in stock for available quantity).
       Only display allowed quantities if > 1.
    """
    try:
        # Get all products
        all_products = db.list_products()
        
        # Group products by category and filter by status and availability
        categories = {}
        
        for product in all_products:
            # Check if product is enabled
            status = product.get('status', 'enabled')
            if status != 'enabled':
                continue
            
            # Check if product has available quantity > 0
            available_qty = product.get('available_quantity', 0)
            if isinstance(available_qty, Decimal):
                available_qty = float(available_qty)
            
            if available_qty <= 0:
                continue
            
            # Get category (default to "Uncategorized" if not set)
            category = product.get('category', 'Uncategorized')
            
            # Add to category group
            if category not in categories:
                categories[category] = []
            
            categories[category].append(product)
        
        # Sort products within each category by name
        for category in categories:
            categories[category] = sorted(categories[category], key=lambda x: x.get("name", ""))
        
        # Add category emojis to the response using database emoji fields
        result = {}
        for category, products in categories.items():
            # Get emoji from first product in category (they should all have same category_emoji)
            category_emoji = DEFAULT_CATEGORY_EMOJI
            if products:
                category_emoji = products[0].get('category_emoji', DEFAULT_CATEGORY_EMOJI)
            
            result[f"{category_emoji} {category}"] = products
        
        return standard_response(True, data=result)
    except Exception as e:
        return standard_response(False, error=str(e))


