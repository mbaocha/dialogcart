# agents/intents.py
# Regex-first intent detector. No LLM calls or semantic fallback.
# Enhanced to handle multi-item add/remove requests and cleaner single-item parsing.
# Supports UPDATE_CART_QUANTITY intent (set/increase/decrease quantities)
# and suppresses invalid product tokens from entities while still detecting intent.

import re
from typing import Dict, Any, List, Tuple, Optional

# =============================
# Normalization & helpers
# =============================
WS = re.compile(r"\s+")
NEG = re.compile(r"\b(?:no|not|don'?t|do\s+not|without|never|cant|can't|cannot)\b", re.I)

DEV_CONTEXT = re.compile(
    r"\b(api|endpoint|schema|database|sql|migration|sdk|tutorial|code|regex|pattern|"
    r"function|method|class|model|component|controller|request|response|json|xml|yaml|"
    r"curl|http|swagger|openapi|postman|insomnia|react|next\.js|vue|angular|django|flask|rails|spring|"
    r"matplotlib|plotly|chart\.?js|grafana|kibana|powerbi|looker|superset)\b",
    re.I
)

# Invalid product names to suppress in entities (but still detect intent)
INVALID_PRODUCT_TOKENS = {
    # user’s original list
    "i", "me", "you", "he", "she", "we", "they", "u", "ya", "ur", "yours",
    # expanded pronouns (subject/object/possessive/reflexive)
    "my", "mine", "myself",
    "your", "yourself", "yourselves",
    "him", "his", "himself",
    "her", "hers", "herself",
    "us", "our", "ours", "ourselves",
    "them", "their", "theirs", "themselves",
    "it", "its", "itself",
}

def norm(text: str) -> str:
    return WS.sub(" ", (text or "").strip())

def has_negation_near(text: str, verb_span, window: int = 4) -> bool:
    """
    Check for a negation token up to N tokens before the verb.
    Accepts verb_span as tuple (start,end), slice, or Match-like (start()/end()).
    """
    if isinstance(verb_span, tuple):
        v_start, _ = verb_span
    elif isinstance(verb_span, slice):
        v_start = verb_span.start
    elif hasattr(verb_span, "start") and hasattr(verb_span, "end"):
        v_start = verb_span.start()
    else:
        raise TypeError("verb_span must be (start,end), slice, or Match-like")

    tokens = text.split()
    offsets = []
    idx = 0
    for tok in tokens:
        s = text.find(tok, idx)
        e = s + len(tok)
        offsets.append((tok, s, e))
        idx = e

    verb_tok_i = 0
    for i, (_, s, e) in enumerate(offsets):
        if s <= v_start < e:
            verb_tok_i = i
            break

    lo = max(0, verb_tok_i - window)
    window_text = " ".join(t for t, _, _ in offsets[lo:verb_tok_i + 1])
    return bool(NEG.search(window_text))


# =============================
# Quantity parsing helpers
# =============================
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "dozen": 12, "half-dozen": 6,
    "couple": 2, "pair": 2,
}
UNITS = r"(?:kg|g|kilograms?|grams?|lb|lbs|pounds?|pc|pcs|pieces?|pack|packs?|bag|bags?|bunch|bunches|crate|crates|carton|cartons|box|boxes|bottle|bottles|tin|tins|can|cans)"

QTY_PHRASE = re.compile(
    rf"(?P<num>(?:\d+(?:\.\d+)?|{'|'.join(map(re.escape, NUMBER_WORDS.keys()))}))\s*(?P<unit>{UNITS})?\b",
    re.I
)

def parse_quantity(text: str) -> Dict[str, Any]:
    # forms like "x2" or "2x"
    m = re.search(r"\b(?:x\s*(\d+)|(\d+)\s*x)\b", text, re.I)
    if m:
        qty = int(m.group(1) or m.group(2))
        return {"quantity": qty, "unit": None, "raw": m.group(0)}

    m = QTY_PHRASE.search(text)
    if not m:
        m2 = re.search(r"\ba\s+(couple|pair)\b", text, re.I)
        if m2:
            return {"quantity": NUMBER_WORDS[m2.group(1).lower()], "unit": None, "raw": m2.group(0)}
        return {"quantity": None, "unit": None, "raw": None}

    num_txt = m.group("num").lower()
    qty = NUMBER_WORDS.get(num_txt)
    if qty is None:
        try:
            f = float(num_txt)
            qty = int(f) if f.is_integer() else f
        except Exception:
            qty = None
    unit = m.group("unit")
    return {"quantity": qty, "unit": unit.lower() if unit else None, "raw": m.group(0)}

def strip_qty_from(text: str) -> str:
    t = text
    t = re.sub(rf"^\s*(?:{QTY_PHRASE.pattern})\s*of\s+", "", t, flags=re.I)
    t = re.sub(rf"^\s*(?:{QTY_PHRASE.pattern})\s*", "", t, flags=re.I)
    t = re.sub(r"^\s*(?:a|an)\s+", "", t, flags=re.I)
    return t.strip(" -,.!?")

def _clean_product_text(t: str) -> str:
    """Clean up product text by removing common noise words and normalizing spacing."""
    t = re.sub(r"\b(?:please|pls|thanks?|thank\s*you|sir|madam|ma'am|excuse\s*me|sorry|pardon|kindly)\b", "", t, flags=re.I)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" ,.-")


# =============================
# Common vocab
# =============================
CART_WORDS = r"(?:cart|basket|bag|trolley|shopping\s*(?:cart|basket|bag|trolley))"
CART_OR_TYPO = rf"(?:{CART_WORDS}|char?t)"  # allow "chart" as a typo for cart in user text


# =============================
# Regex intent patterns
# =============================

# SHOW PRODUCT LIST
SHOW_ACTIONS = r"(?:show|list|browse|see|view|display|explore|open|look(?:\s*at)?|get|find|check)"
SHOW_OBJECTS = r"(?:products?|catalog(?:ue)?|inventor(?:y|ies)|categor(?:y|ies)|items?|goods|stock|menu|range|selection|grocer(?:y|ies)|price\s*list)"
SHOW_STRONG = re.compile(rf"\b(?:product\s*list|price\s*list|list\s+of\s+(?:products?|items?|categories)|browse\s+catalog(?:ue)?)\b", re.I)
SHOW_VERB_OBJ = re.compile(rf"\b{SHOW_ACTIONS}\b.*?\b{SHOW_OBJECTS}\b", re.I)
SHOW_WHAT_HAVE = re.compile(r"\bwhat(?:'s| is)?\s+(?:available|in\s*stock)\b|\bwhat\s+do\s+you\s+(?:have|sell|carry)\b", re.I)

# PRODUCT EXISTENCE (catalog availability; not about user's cart)
EXIST_QUERY = re.compile(
    rf"(?:\bdo\s+you\s+(?:have|carry|sell|stock)\b|\bgot\b|\bis\b|\bare\b).*?(?P<product>[\w][\w\s\-'/&]+?)\s*(?:\?|\.|!|$)",
    re.I
)
EXIST_AVAILABLE = re.compile(
    r"\b(?:is|are)\s+(?P<product>[\w][\w\s\-'/&]+?)\s+(?:available|in\s*stock|on\s*hand|for\s*sale)\b",
    re.I
)
TRAILING_STOP = re.compile(r"\b(?:available|in\s*stock|on\s*hand|for\s*sale|please|pls|thanks?|thank\s*you)\b.*$", re.I)

# ADD / REMOVE verbs
ADD_VERBS = r"(?:add|put|include|insert|throw|stick|drop|place)"
REMOVE_VERBS = r"(?:remove|delete|take\s*(?:out|off)|discard|subtract|minus|pull)"

# Canonical patterns (forward + reverse): we parse items from captured 'tail'
ADD_TO_CART_CANON = re.compile(
    rf"\b(?P<verb>{ADD_VERBS})\b(?P<tail>.+?)\b(?:to|into|in)\s+(?:my\s+)?{CART_OR_TYPO}\b",
    re.I
)
ADD_TO_CART_CANON_REV = re.compile(
    rf"\b(?P<verb>{ADD_VERBS})\b\s+\b(?:to|into|in)\s+(?:my\s+)?{CART_OR_TYPO}\b\s+(?P<tail>.+)$",
    re.I
)

REMOVE_FROM_CART_CANON = re.compile(
    rf"\b(?P<verb>{REMOVE_VERBS})\b(?P<tail>.+?)\b(?:from|in)\s+(?:my\s+)?{CART_WORDS}\b",
    re.I
)
REMOVE_IMPLICIT = re.compile(rf"\b(?P<verb>{REMOVE_VERBS})\b\s+(?P<tail>.+)$", re.I)
ADD_IMPLICIT = re.compile(rf"\b(?P<verb>{ADD_VERBS})\b\s+(?P<tail>.+)$", re.I)

# VIEW CART
VIEW_VERBS = r"(?:show|view|see|display|open|check|list|look(?:\s*at)?)"
VIEW_CART_VERB = re.compile(
    rf"\b(?P<verb>{VIEW_VERBS})\s+(?:what'?s\s+in\s+)?(?:my\s+)?{CART_OR_TYPO}\b",
    re.I
)
VIEW_CART_Q = re.compile(
    rf"\b(?:"
    rf"what\s+(?:items?|products?)\s+(?:are|exist|are\s+there)\s+in\s+(?:my\s+)?{CART_OR_TYPO}"
    rf"|which\s+(?:items?|products?)\s+(?:are\s+)?(?:listed\s+)?in\s+(?:my\s+)?{CART_OR_TYPO}"
    rf"|what\s+(?:do\s+i\s+)?(?:have|got)\s+in\s+(?:my\s+)?{CART_OR_TYPO}"
    rf"|(?:items?|products?)\s+in\s+(?:my\s+)?{CART_OR_TYPO}"
    rf"|(?:my\s+)?{CART_OR_TYPO}\s+(?:items?|contents?)"
    rf")\b",
    re.I
)

# CLEAR CART
CLEAR_VERBS = r"(?:clear|empty|reset|wipe|trash|dump)"
CLEAR_CART = re.compile(
    rf"\b(?P<verb>{CLEAR_VERBS})\b.*?\b(?:all\s+)?(?:items?\s+)?(?:from\s+)?(?:my\s+)?{CART_OR_TYPO}\b",
    re.I
)
REMOVE_ALL = re.compile(
    rf"\b(?:remove|delete|drop)\s+(?:all|everything)\s+(?:from\s+)?(?:my\s+)?{CART_WORDS}\b",
    re.I
)

# RESTORE / UNDO CART
RESTORE_VERBS = r"(?:restore|recover|retrieve|revert|rollback|roll\s*back|bring\s*back|put\s*back|get\s*back|undo)"
RESTORE_CART = re.compile(
    rf"\b(?P<verb>{RESTORE_VERBS})\b.*?\b(?:my\s+)?{CART_OR_TYPO}\b",
    re.I
)

# explicit "undo clear/empty/wipe" patterns that mention cart
UNDO_CLEAR_EXPLICIT = re.compile(
    rf"\bundo\b.*?\b(?:{CLEAR_VERBS})\b.*?\b(?:my\s+)?{CART_OR_TYPO}\b",
    re.I
)

# common “get my cart back” without a restore verb
GET_CART_BACK = re.compile(
    rf"\b(?:get|bring|put)\s+(?:my\s+)?{CART_OR_TYPO}\s+back\b",
    re.I
)

# =============================
# UPDATE CART QUANTITY (new)
# =============================
# Ops
SET_VERBS = r"(?:set|change|update|adjust|modify|make)"
INC_VERBS = r"(?:increase|increment|raise|bump|add\s*(?:more)?)"
DEC_VERBS = r"(?:decrease|reduce|lower|drop|cut|deduct|lessen)"

# Absolute: "... quantity of X to 3", "set beans to 4", "make okra 2kg"
UPDATE_SET_CANON = re.compile(
    rf"\b(?P<verb>{SET_VERBS})\b(?P<tail>.+?)"
    rf"(?:\b(?:quantity\s+of|qty\s+of)\b\s+)?"
    rf"(?P<prod>[a-z][\w\s\-'&/]+?)\s*(?:to|=)\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>{UNITS})?\b",
    re.I
)

# Relative by-amount: "increase X by 2", "reduce yams by 1", "raise rice 2" (allow missing 'by')
UPDATE_REL_CANON = re.compile(
    rf"\b(?P<verb>(?:{INC_VERBS})|(?:{DEC_VERBS}))\b(?P<tail>.+?)"
    rf"(?:\b(?:the\s+)?(?:quantity|qty)\s+of\b\s+)?"
    rf"(?P<prod>[a-z][\w\s\-'&/]+?)\s*(?:by\s+)?(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>{UNITS})?\b",
    re.I
)

# Multi-item absolute: "set yams to 3 and plantains to 5"
UPDATE_SET_MULTI = re.compile(
    rf"\b(?P<prod>[a-z][\w\s\-'&/]+?)\s*(?:to|=)\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>{UNITS})?\b"
    rf"(?=(?:\s*(?:,|\band\b)\s*|\s+(?:in|into|to)\s+(?:my\s+)?{CART_OR_TYPO}\b|$))",
    re.I
)

def _normalize_cart_typos_text(t: str) -> str:
    return re.sub(r"\bchar?t\b", "cart", t, flags=re.I)

def _strip_preamble(text: str) -> str:
    # Drop verbs/aux and cart phrases; keep the item list clean
    t = re.sub(r"\b(?:please|pls|thanks?|thank\s*you|kindly|sorry|pardon|excuse\s*me)\b", "", text, flags=re.I)
    t = re.sub(rf"\b(?:{ADD_VERBS}|{REMOVE_VERBS}|{SET_VERBS}|{INC_VERBS}|{DEC_VERBS})\b", "", t, flags=re.I)
    # remove any leading or trailing "to/into/in (my) cart" remnants
    t = re.sub(rf"\b(?:to|into|in)\s+(?:my\s+)?{CART_OR_TYPO}\b", "", t, flags=re.I)
    t = re.sub(rf"\b{CART_OR_TYPO}\b", "", t, flags=re.I)
    t = re.sub(r"\b(?:to|into|in)\b", "", t, flags=re.I)
    t = re.sub(r"^\s*(?:and|,)\s*", "", t, flags=re.I)
    return _norm2(t)

# =============================
# Multi-item extractor (pure regex)
# =============================

UNITS_RX = UNITS  # reuse
CART_RX  = CART_WORDS
CART_OR_TYPO_RX = CART_OR_TYPO
WS2 = re.compile(r"\s{2,}")
QTY_UNIT_ANY = r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>{units})".format(units=UNITS_RX)

# Two main orders:
# A) 2kg yam
PAIR_A = re.compile(
    rf"\b{QTY_UNIT_ANY}\s+(?P<prod>[a-z][\w\s\-'&/]+?)\b"
    rf"(?=,|(?:\s+and\s+)|\s+(?:to|into|in)\s+(?:my\s+)?{CART_OR_TYPO_RX}\b|$)",
    re.I
)
# B) yam 2kg
PAIR_B = re.compile(
    rf"\b(?P<prod>[a-z][\w\s\-'&/]+?)\s+{QTY_UNIT_ANY}\b"
    rf"(?=,|(?:\s+and\s+)|\s+(?:to|into|in)\s+(?:my\s+)?{CART_OR_TYPO_RX}\b|$)",
    re.I
)

# C/D) quantity without unit: "2 plantains" or "plantains 2"
PAIR_C = re.compile(
    r"\b(?P<num>\d+(?:\.\d+)?)\s+(?P<prod>[a-z][\w\s\-'&/]+?)\b"
    r"(?=,|(?:\s+and\s+)|\s+(?:to|into|in)\s+(?:my\s+)?(?:cart|basket|bag|trolley|shopping\s*(?:cart|basket|bag|trolley)|char?t)\b|$)",
    re.I
)
PAIR_D = re.compile(
    r"\b(?P<prod>[a-z][\w\s\-'&/]+?)\s+(?P<num>\d+(?:\.\d+)?)\b"
    r"(?=,|(?:\s+and\s+)|\s+(?:to|into|in)\s+(?:my\s+)?(?:cart|basket|bag|trolley|shopping\s*(?:cart|basket|bag|trolley)|char?t)\b|$)",
    re.I
)

# E) number words: "a couple of yams", "two yams"
NUM_WORDS_RX = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozen|half\-dozen|couple|pair)"
PAIR_WORDS = re.compile(
    rf"\b(?P<word>{NUM_WORDS_RX})\b(?:\s+of)?\s+(?P<prod>[a-z][\w\s\-'&/]+?)\b"
    rf"(?=,|(?:\s+and\s+)|\s+(?:to|into|in)\s+(?:my\s+)?{CART_OR_TYPO_RX}\b|$)",
    re.I
)
WORD_MAP = {
    "one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,
    "eleven":11,"twelve":12,"dozen":12,"half-dozen":6,"couple":2,"pair":2
}

MULTI_X = re.compile(r"\b(?:x\s*(\d+)|(\d+)\s*x)\b", re.I)

def _norm2(s: str) -> str:
    return WS2.sub(" ", (s or "").strip())

def _parse_qty_unit(m: re.Match) -> Tuple[float, Optional[str], str]:
    num_txt = m.group("num")
    unit = (m.group("unit") or "").lower()
    qty = float(num_txt) if "." in num_txt else int(num_txt)
    return qty, (unit or None), m.group(0)

def _cleanup_prod(p: str) -> str:
    p = re.sub(r"\b(?:and|,)\b$", "", p.strip(), flags=re.I)
    p = re.sub(r"\b(?:of|the|a|an)\b\s+", "", p, flags=re.I)
    p = re.sub(CART_OR_TYPO_RX, "", p, flags=re.I)
    return _norm2(p)

def _is_invalid_product_name(prod: str) -> bool:
    """
    Returns True if product name is in INVALID_PRODUCT_TOKENS after aggressive cleaning.
    Cleaning removes spaces and non-word chars, and lowercases.
    """
    cleaned = re.sub(r"[^\w]+", "", (prod or "").lower())
    return cleaned in INVALID_PRODUCT_TOKENS

def _append_item_if_valid(items: List[Dict[str, Any]], it: Dict[str, Any]) -> None:
    prod = (it.get("product") or "").strip()
    if not prod:
        return
    if _is_invalid_product_name(prod):
        return
    items.append(it)

def extract_line_items(text: str) -> List[Dict[str, Any]]:
    """
    Returns a list of {"product": str, "quantity": num|None, "unit": str|None, "raw": str|None}
    for each item mentioned. Does not validate against catalog.
    Suppresses invalid product names from entities.
    """
    t = _normalize_cart_typos_text(_strip_preamble(text))

    items: List[Dict[str, Any]] = []
    taken_spans: List[Tuple[int, int]] = []

    def _free(s, e):
        return all(not (s < te and e > ts) for ts, te in taken_spans)

    # Explicit qty+unit pairs first
    for m in PAIR_A.finditer(t):
        s, e = m.span()
        if not _free(s, e):
            continue
        qty, unit, raw = _parse_qty_unit(m)
        prod = _cleanup_prod(m.group("prod"))
        _append_item_if_valid(items, {"product": prod, "quantity": qty, "unit": unit, "raw": raw})
        taken_spans.append((s, e))

    for m in PAIR_B.finditer(t):
        s, e = m.span()
        if not _free(s, e):
            continue
        qty, unit, raw = _parse_qty_unit(m)
        prod = _cleanup_prod(m.group("prod"))
        _append_item_if_valid(items, {"product": prod, "quantity": qty, "unit": unit, "raw": raw})
        taken_spans.append((s, e))

    # qty without unit
    for m in PAIR_C.finditer(t):
        s, e = m.span()
        if not _free(s, e):
            continue
        num_txt = m.group("num")
        qty = float(num_txt) if "." in num_txt else int(num_txt)
        prod = _cleanup_prod(m.group("prod"))
        _append_item_if_valid(items, {"product": prod, "quantity": qty, "unit": None, "raw": m.group(0)})
        taken_spans.append((s, e))

    for m in PAIR_D.finditer(t):
        s, e = m.span()
        if not _free(s, e):
            continue
        num_txt = m.group("num")
        qty = float(num_txt) if "." in num_txt else int(num_txt)
        prod = _cleanup_prod(m.group("prod"))
        _append_item_if_valid(items, {"product": prod, "quantity": qty, "unit": None, "raw": m.group(0)})
        taken_spans.append((s, e))

    # number-words
    for m in PAIR_WORDS.finditer(t):
        s, e = m.span()
        if not _free(s, e):
            continue
        w = m.group("word").lower()
        qty = WORD_MAP.get(w)
        prod = _cleanup_prod(m.group("prod"))
        _append_item_if_valid(items, {"product": prod, "quantity": qty, "unit": None, "raw": m.group(0)})
        taken_spans.append((s, e))

    # Remove captured spans to parse leftovers (e.g., "okro", or "okro x2")
    leftovers = []
    i = 0
    for s, e in sorted(taken_spans):
        if i < s:
            leftovers.append(t[i:s])
        i = e
    if i < len(t):
        leftovers.append(t[i:])
    rem = _norm2(" ".join(leftovers))

    if rem:
        parts = re.split(r"\s*(?:,|\band\b)\s*", rem, flags=re.I)
        for p in parts:
            p = p.strip()
            if not p:
                continue
            mx = MULTI_X.search(p)
            qty = int(mx.group(1) or mx.group(2)) if mx else None
            prod = MULTI_X.sub("", p).strip()
            prod = _cleanup_prod(prod)
            _append_item_if_valid(items, {"product": prod, "quantity": qty, "unit": None, "raw": mx.group(0) if mx else None})

    return items

# =============================
# Quantity update extractor (new)
# =============================
def extract_quantity_updates(text: str) -> List[Dict[str, Any]]:
    """
    Parse 'set/increase/decrease' requests into a list of entities:
    { product, quantity, unit, raw, update_op: 'set'|'increase'|'decrease', is_relative: bool }
    Suppresses invalid product names in entities.
    """
    t = _normalize_cart_typos_text(norm(text))
    entities: List[Dict[str, Any]] = []

    # Absolute, canonical with explicit products
    for m in UPDATE_SET_CANON.finditer(t):
        prod = _cleanup_prod(m.group("prod"))
        if prod and not _is_invalid_product_name(prod):
            num_txt = m.group("num")
            qty = float(num_txt) if "." in num_txt else int(num_txt)
            unit = (m.group("unit") or "").lower() or None
            entities.append({
                "product": prod, "quantity": qty, "unit": unit, "raw": m.group(0),
                "update_op": "set", "is_relative": False
            })

        # Also sweep for "and <prod> to <num>" segments after the first verb
        tail_text = t[m.end():]
        for m2 in UPDATE_SET_MULTI.finditer(tail_text):
            prod2 = _cleanup_prod(m2.group("prod"))
            if prod2 and not _is_invalid_product_name(prod2):
                num_txt2 = m2.group("num")
                qty2 = float(num_txt2) if "." in num_txt2 else int(num_txt2)
                unit2 = (m2.group("unit") or "").lower() or None
                entities.append({
                    "product": prod2, "quantity": qty2, "unit": unit2, "raw": m2.group(0),
                    "update_op": "set", "is_relative": False
                })
        break  # avoid duplicates if multiple verbs present

    # Relative (increase/decrease), allow multiple occurrences
    for m in UPDATE_REL_CANON.finditer(t):
        verb = m.group("verb").lower()
        op = "increase" if re.match(INC_VERBS, verb, re.I) else "decrease"
        prod = _cleanup_prod(m.group("prod"))
        if prod and not _is_invalid_product_name(prod):
            num_txt = m.group("num")
            qty = float(num_txt) if "." in num_txt else int(num_txt)
            unit = (m.group("unit") or "").lower() or None
            entities.append({
                "product": prod, "quantity": qty, "unit": unit, "raw": m.group(0),
                "update_op": op, "is_relative": True
            })

    # Light absolute fallback like "make beans 3", "set okra 2kg"
    if not entities:
        light_abs = re.compile(
            rf"\b(?P<verb>{SET_VERBS})\b\s+(?P<prod>[a-z][\w\s\-'&/]+?)\s+(?:to\s+)?(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>{UNITS})?\b",
            re.I
        )
        for m in light_abs.finditer(t):
            prod = _cleanup_prod(m.group("prod"))
            if prod and not _is_invalid_product_name(prod):
                num_txt = m.group("num")
                qty = float(num_txt) if "." in num_txt else int(num_txt)
                unit = (m.group("unit") or "").lower() or None
                entities.append({
                    "product": prod, "quantity": qty, "unit": unit, "raw": m.group(0),
                    "update_op": "set", "is_relative": False
                })

    return entities


# =============================
# Regex-first classifier
# =============================
def _with_items_entities(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Build entities list for the new simplified structure.
    Returns a list of items directly as the entities.
    """
    if not items:
        return []
    return items

def _regex_classify(t_raw: str) -> Dict[str, Any]:
    result = {"intent": "NONE", "confidence": "low", "entities": []}
    if not t_raw:
        return result

    # Normalize common typo "chart"->"cart"
    t = _normalize_cart_typos_text(norm(t_raw))
    devy = bool(DEV_CONTEXT.search(t))

    # 1) CLEAR CART
    m = CLEAR_CART.search(t)
    if m and not has_negation_near(t, m.span("verb")):
        if devy and re.search(r"\bchar?t\b", t, re.I) and not re.search(CART_WORDS, t, re.I):
            pass
        else:
            return {"intent": "CLEAR_CART", "confidence": "high", "entities": []}
    if REMOVE_ALL.search(t):
        return {"intent": "CLEAR_CART", "confidence": "high", "entities": []}

    # 2) ADD TO CART (canonical forward or reverse)
    m = ADD_TO_CART_CANON.search(t) or ADD_TO_CART_CANON_REV.search(t)
    if m and not has_negation_near(t, m.span("verb")):
        tail = m.group("tail")
        items = extract_line_items(tail)
        if items:
            return {"intent": "ADD_TO_CART", "confidence": "high", "entities": _with_items_entities(items)}
        # Tail present but all items suppressed (e.g., "add him to cart")
        if not devy:
            return {"intent": "ADD_TO_CART", "confidence": "low", "entities": []}

    # 2b) ADD implicit
    m = ADD_IMPLICIT.search(t)
    if m and not has_negation_near(t, m.span("verb")):
        tail = m.group("tail")
        if not DEV_CONTEXT.search(tail):
            items = extract_line_items(tail)
            if items:
                return {"intent": "ADD_TO_CART", "confidence": "medium", "entities": _with_items_entities(items)}
            # Verb indicates add, but entities suppressed
            return {"intent": "ADD_TO_CART", "confidence": "low", "entities": []}

    # 3) REMOVE FROM CART
    m = REMOVE_FROM_CART_CANON.search(t)
    if m and not has_negation_near(t, m.span("verb")):
        tail = m.group("tail")
        items = extract_line_items(tail)
        if items:
            return {"intent": "REMOVE_FROM_CART", "confidence": "high", "entities": _with_items_entities(items)}
        if not devy:
            return {"intent": "REMOVE_FROM_CART", "confidence": "low", "entities": []}

    # 3b) REMOVE implicit
    m = REMOVE_IMPLICIT.search(t)
    if m and not has_negation_near(t, m.span("verb")):
        tail = m.group("tail")
        if not DEV_CONTEXT.search(tail):
            items = extract_line_items(tail)
            if items:
                return {"intent": "REMOVE_FROM_CART", "confidence": "medium", "entities": _with_items_entities(items)}
            return {"intent": "REMOVE_FROM_CART", "confidence": "low", "entities": []}
    
    # 3c) UPDATE CART QUANTITY (set/increase/decrease)
    if not devy:
        vm = (re.search(rf"\b({SET_VERBS}|{INC_VERBS}|{DEC_VERBS})\b", t, re.I))
        if vm and not has_negation_near(t, vm.span()):
            updates = extract_quantity_updates(t)
            if updates:
                return {"intent": "UPDATE_CART_QUANTITY", "confidence": "high", "entities": updates}
            # Verb suggests quantity update, but entities suppressed
            return {"intent": "UPDATE_CART_QUANTITY", "confidence": "low", "entities": []}

    # 1b) RESTORE / UNDO CART
    m = RESTORE_CART.search(t) or UNDO_CLEAR_EXPLICIT.search(t) or GET_CART_BACK.search(t)
    if m and not has_negation_near(t, m.span("verb") if hasattr(m, "span") and "verb" in getattr(m, "groupdict", lambda: {})() else (m.start(), m.end())):
        if not devy:
            return {"intent": "RESTORE_CART", "confidence": "high", "entities": []}

    # 4) VIEW CART
    m = VIEW_CART_VERB.search(t)
    if m:
        neg = False
        try:
            neg = has_negation_near(t, m.span("verb"))
        except Exception:
            pass
        return {"intent": "VIEW_CART", "confidence": ("medium" if neg else "high"), "entities": []}

    if VIEW_CART_Q.search(t):
        return {"intent": "VIEW_CART", "confidence": "high", "entities": []}

    # 5) SHOW PRODUCT LIST
    if (SHOW_STRONG.search(t) or SHOW_VERB_OBJ.search(t) or SHOW_WHAT_HAVE.search(t)) and not devy and not NEG.search(t):
        return {"intent": "SHOW_PRODUCT_LIST", "confidence": "high", "entities": []}

    # 6) PRODUCT EXISTENCE
    if not re.search(CART_WORDS, t, re.I):
        m = EXIST_AVAILABLE.search(t)
        if m and not NEG.search(t):
            prod = TRAILING_STOP.sub("", m.group("product")).strip()
            if prod and not DEV_CONTEXT.search(prod):
                prod_clean = re.sub(r"[^\w]+", "", prod.lower())
                if prod_clean in INVALID_PRODUCT_TOKENS:
                    return {"intent": "CHECK_PRODUCT_EXISTENCE", "confidence": "low", "entities": []}
                return {"intent": "CHECK_PRODUCT_EXISTENCE", "confidence": "high", "entities": [{"product": prod}]}

        m = EXIST_QUERY.search(t)
        if m and not NEG.search(t):
            prod = TRAILING_STOP.sub("", m.group("product")).strip()
            if prod and not DEV_CONTEXT.search(prod):
                prod = re.sub(r"\b(?:available|in\s*stock|please|thanks?)\b.*$", "", prod, flags=re.I).strip(" ,.-")
                if prod:
                    prod_clean = re.sub(r"[^\w]+", "", prod.lower())
                    if prod_clean in INVALID_PRODUCT_TOKENS:
                        return {"intent": "CHECK_PRODUCT_EXISTENCE", "confidence": "low", "entities": []}
                    return {"intent": "CHECK_PRODUCT_EXISTENCE", "confidence": "medium", "entities": [{"product": prod}]}

    return result


# =============================
# Entities-only extraction when intent is provided
# =============================
def _extract_entities_for_intent(text: str, intent: str) -> List[Dict[str, Any]]:
    """
    Extract entities only, based on the provided intent (no intent detection).
    Mirrors the regex logic used in _regex_classify, but skips negation/dev-context guards.
    Supports multi-item outputs via entities.items.
    Returns a list of entity dictionaries.
    """
    t = _normalize_cart_typos_text(norm(text))
    intent_up = (intent or "").upper().strip()

    if intent_up in {"ADD_TO_CART", "REMOVE_FROM_CART"}:
        if intent_up == "ADD_TO_CART":
            m = ADD_TO_CART_CANON.search(t) or ADD_TO_CART_CANON_REV.search(t)
        else:
            m = REMOVE_FROM_CART_CANON.search(t)
        tail = m.group("tail") if m else t
        items = extract_line_items(tail)
        if not items:
            # fallback: try single-item parse then validate
            qty = parse_quantity(tail)
            prod_text = tail
            if qty.get("raw"):
                prod_text = re.sub(rf"\b(?:of\s+)?{re.escape(str(qty['raw']))}\b", "", prod_text, flags=re.I)
            prod_text = _clean_product_text(strip_qty_from(prod_text))
            prod_text = re.sub(CART_OR_TYPO, "", prod_text, flags=re.I).strip()
            if prod_text and not _is_invalid_product_name(prod_text):
                it = {"product": prod_text, "quantity": qty.get("quantity"), "unit": qty.get("unit"), "raw": qty.get("raw")}
                return _with_items_entities([it])
            return []
        return _with_items_entities(items)

    if intent_up == "UPDATE_CART_QUANTITY":
        return extract_quantity_updates(t)

    if intent_up == "CHECK_PRODUCT_EXISTENCE":
        m = EXIST_AVAILABLE.search(t) or EXIST_QUERY.search(t)
        if m and m.groupdict().get("product"):
            prod = TRAILING_STOP.sub("", m.group("product")).strip()
            prod = re.sub(r"\b(?:available|in\s*stock|please|thanks?)\b.*$", "", prod, flags=re.I).strip(" ,.-")
            if prod and not DEV_CONTEXT.search(prod) and not _is_invalid_product_name(prod):
                return [{"product": prod}]
        qty = parse_quantity(t)
        prod_text = t
        if qty.get("raw"):
            prod_text = re.sub(rf"\b(?:of\s+)?{re.escape(str(qty['raw']))}\b", "", prod_text, flags=re.I)
        prod_text = _clean_product_text(strip_qty_from(prod_text))
        if prod_text and not _is_invalid_product_name(prod_text):
            return [{"product": prod_text}]
        return []

    if intent_up in {"VIEW_CART", "SHOW_PRODUCT_LIST", "CLEAR_CART", "RESTORE_CART", "NONE"}:
        return []

    return []


# =============================
# Public API
# =============================

def classify_intent(text: str, intent: Optional[str] = None) -> Dict[str, Any]:
    """
    Classify a user's text into an intent with extracted entities.

    Returns:
        {
            "intent": <str>,
            "confidence": "low" | "medium" | "high",
            "entities": [
                {
                  "product": <str>,
                  "quantity": <num|None>,
                  "unit": <str|None>,
                  "raw": <str|None>,
                  # only for UPDATE_CART_QUANTITY:
                  "update_op": "set"|"increase"|"decrease",
                  "is_relative": bool
                },
                ...
            ]
        }
    """
    if intent:
        entities = _extract_entities_for_intent(text, intent)
        return {"intent": intent, "confidence": "high", "entities": entities}

    t = norm(text)
    res = _regex_classify(t)
    return res


# =============================
# CLI for quick testing
# =============================
SAMPLES = [
    "show product list",
    "Can I browse your catalogue?",
    "what do you have in stock?",
    "what do you sell",
    "which products are listed in my cart",
    "what items are in my cart",
    "what item exist in my cart",
    "Do you have Yoruba yams?",
    "is 5kg of basmati rice available?",
    "add 3 plantains to my basket",
    "put a couple of yams into the cart please",
    "remove 2 plantains from cart",
    "take out the yam from my trolley",
    "empty my cart",
    "clear chart",
    "clear chart in matplotlib",
    "implement add-to-cart API",
    # multi
    "add 2kg yam and 4kg beans to cart",
    "add 1 box stockfish, 3kg crayfish and okro x2 to my cart",
    "remove 2kg yam and 1kg beans from my basket",
    # reversed order canonical
    "add to chart 3 plantain",
    "add to cart fish",
    "add fish to cart",
    
    "restore my cart",
    "recover my basket",
    "get my cart back",
    "put back my trolley items",
    "undo clear my cart",
    "undo emptying my basket",
    "can you restore the cart I cleared?",

    # single variants
    ("yam 3", "ADD_TO_CART"),
    ("3 yams", "ADD_TO_CART"),
    ("four yams", "ADD_TO_CART"),
    ("add 3 plantains to my basket", "ADD_TO_CART"),
    ("remove 1 yam from cart", "REMOVE_FROM_CART"),

    # ===== UPDATE CART QUANTITY samples =====
    "set yams to 3",
    "change the quantity of beans to 5",
    "update okra to 4kg in my cart",
    "make plantain 6",
    "increase yam by 2",
    "reduce beans by 1",
    "raise rice 2 and decrease beans 1",
    "set yams to 3 and plantains to 5",

    # ===== invalid product name suppression but intent detection =====
    "add him to cart",
    "remove her from my basket",
    "set you to 3",
    "increase ur by 2",
    "do you have you?",
    ("increase rice by 2", "UPDATE_CART_QUANTITY"),
    ("decrease beans by 1", "UPDATE_CART_QUANTITY"),
    ("set okra to 4kg", "UPDATE_CART_QUANTITY"),
]

def _print_banner():
    print("🤖 Intent Classifier - Interactive Mode")
    print("========================================")
    print("Type 'quit' to exit")
    print("Type 'samples' to see example classifications")
    print("Tip: pass an explicit intent by typing: intent=ADD_TO_CART your text\n")

def _run_samples():
    for s in SAMPLES:
        if isinstance(s, tuple):
            text, forced_intent = s
            print(f"{text!r} with intent={forced_intent} =>", classify_intent(text, forced_intent))
        else:
            print(s, "=>", classify_intent(s))

if __name__ == "__main__":
    _print_banner()
    while True:
        try:
            s = input("Enter text to classify: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break
        if not s:
            continue
        if s.lower() in {"quit", "exit"}:
            print("Bye!")
            break
        if s.lower() == "samples":
            _run_samples()
            continue
        forced_intent = None
        if s.lower().startswith("intent="):
            parts = s.split(None, 1)
            forced_intent = parts[0].split("=", 1)[1]
            s = parts[1] if len(parts) > 1 else ""
        print("🎯 Intent:", classify_intent(s, forced_intent))
