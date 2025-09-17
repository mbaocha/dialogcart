from __future__ import annotations
from typing import Any, Dict, List, Union
from decimal import Decimal
from utils.coreutil import get_number_emoji

DEFAULT_CATEGORY_EMOJI = "📦"
CURRENCY = "$"

IRREG_PLURALS = {"bunch": "bunches", "box": "boxes", "crate": "crates", "bag": "bags"}

def _fmt_money(x: Union[int, float, Decimal, None]) -> str:
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
    if not unit:
        return ""
    try:
        n = float(qty)
    except Exception:
        n = None
    ul = unit.lower()
    if ul in {"kg", "l"}:
        return unit
    if n is None or n != 1:
        return IRREG_PLURALS.get(ul, unit + "s")
    return unit

def _fmt_package_size(p: Dict[str, Any]) -> str:
    """Return '10 kg per box' if unit=box and package_size given."""
    unit = (p.get("unit") or "").strip().lower()
    if unit != "box":
        return ""
    ps = p.get("package_size")
    if not ps:
        return ""
    if isinstance(ps, dict):
        val = ps.get("value")
        u = _norm_unit(str(ps.get("unit") or ""))
        try:
            n = float(val)
            val_s = str(int(n)) if n.is_integer() else str(n)
        except Exception:
            val_s = str(val)
        if val_s and u:
            return f"{val_s} {u} per box"
        return ""
    if isinstance(ps, str):
        return ps
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

    # availability
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
        try:
            min_val = float(aq["min"])
        except Exception:
            min_val = aq["min"]
        if not (isinstance(min_val, (int, float)) and float(min_val) == 1.0):
            min_txt = f"min {_qty_to_str(min_val)} {unit}".strip()
    elif isinstance(aq, list) and len(aq) > 1:
        nums = ", ".join(_qty_to_str(x) for x in aq)
        tail_unit = f" {_pluralize(unit, 2)}" if unit.lower() == "box" else (f" {unit}" if unit else "")
        sizes_txt = f"in {nums}{tail_unit}"

    per_box = _fmt_package_size(p)
    side_bits = [t for t in [per_box, (sizes_txt or min_txt), avail_txt] if t]
    side = f" ({'; '.join(side_bits)})" if side_bits else ""

    unit_part = f"/{unit}" if unit else ""
    return f"{pe} {name} — {price_part}{unit_part}{side}".strip()

def _ensure_label_has_emoji(cat_label: str, cat_emoji: str) -> str:
    lab = (cat_label or "").strip()
    return f"{cat_emoji} {lab}" if not lab.startswith(cat_emoji) else lab

def categories_bulleted(
    *,
    categories: Dict[str, List[Dict[str, Any]]],
    limit_categories: int = 10,
    examples_per_category: int = 2,
    default_category_emoji: str = DEFAULT_CATEGORY_EMOJI,
) -> Dict[str, Any]:
    """
    Input: {<category label>: [products...] } (label may or may not have emoji prefix)
    Output: {"text": <bullet string>, "rows": [...], "categories": <input>, "style_used": "bullet"}
    """
    ordered = sorted(categories.items(), key=lambda kv: kv[0].lower())

    lines: List[str] = []
    rows: List[Dict[str, Any]] = []

    for idx, (cat_label, prods) in enumerate(ordered, start=1):
        if idx > limit_categories:
            lines.append("… and more categories. Reply 'more' to see the rest.")
            break

        cat_emoji = (prods[0].get("category_emoji") if prods else None) or default_category_emoji
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

    return {
        "text": "\n\n".join(lines),
        "rows": rows,
        "categories": categories,
        "style_used": "bullet",
    }
