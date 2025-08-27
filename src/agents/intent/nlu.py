# agents/nlu.py
# spaCy-based NLU: intent + multi-entity extraction with the same output shape as your regex classifier.

from __future__ import annotations
import re
from typing import Dict, Any, List, Optional, Tuple

# =============== spaCy loader (lazy) ===============
_NLP = None
def nlp():
    global _NLP
    if _NLP is None:
        import spacy
        _NLP = spacy.load("en_core_web_sm")
    return _NLP

# =============== Config/Vocab ===============
UNITS = [
    "kg","g","kilogram","kilograms","gram","grams",
    "lb","lbs","pound","pounds",
    "pc","pcs","piece","pieces",
    "pack","packs","bag","bags","bunch","bunches",
    "crate","crates","carton","cartons","box","boxes",
    "bottle","bottles","tin","tins","can","cans"
]
UNIT_SET = set(u.lower() for u in UNITS)

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "dozen": 12, "half-dozen": 6,
    "couple": 2, "pair": 2,
}

CART_WORDS = {"cart","basket","bag","trolley"}
CART_ALIASES = {"shopping cart","shopping basket","shopping bag","shopping trolley"}
CART_TYPO = {"chart"}  # typo to map → cart

DEV_KEYWORDS = {
    "api","endpoint","schema","database","sql","migration","sdk","tutorial","code","regex","pattern",
    "function","method","class","model","component","controller","request","response","json","xml","yaml",
    "curl","http","swagger","openapi","postman","insomnia","react","next.js","vue","angular","django","flask",
    "rails","spring","matplotlib","plotly","chart.js","grafana","kibana","powerbi","looker","superset"
}

NEG_WORDS = {"no","not","don't","dont","do","without","never","cant","can't","cannot"}

# =============== Utils ===============
WS2 = re.compile(r"\s{2,}")

def _norm(s: str) -> str:
    return WS2.sub(" ", (s or "").strip())

def _lower_tokens(doc):
    return [t.text.lower() for t in doc]

def _has_dev_context(doc) -> bool:
    lo = _lower_tokens(doc)
    return any(w in DEV_KEYWORDS for w in lo)

def _contains_cart_word(doc) -> bool:
    txt = doc.text.lower()
    if any(w in txt.split() for w in CART_WORDS):  # simple
        return True
    for alias in CART_ALIASES:
        if alias in txt:
            return True
    if "chart" in txt and "cart" in txt:  # both appear, still cart context
        return True
    return False

def _normalize_cart_typos(text: str) -> str:
    # standalone "chart" → "cart" when near shopping context (best effort).
    return re.sub(r"\bchar?t\b", "cart", text, flags=re.I)

def _has_neg_before(token, window: int = 5) -> bool:
    # check N tokens to the left for negation words
    i = token.i
    start = max(0, i - window)
    for t in token.doc[start:i]:
        if t.lemma_.lower() in NEG_WORDS or t.text.lower() in NEG_WORDS:
            return True
    return False

def _is_unit(tok) -> bool:
    return tok.text.lower() in UNIT_SET

def _num_from_token(tok) -> Optional[float]:
    # numeric: "2", "1.5"
    if tok.like_num:
        try:
            f = float(tok.text)
            return int(f) if f.is_integer() else f
        except Exception:
            return None
    # word numbers
    val = NUMBER_WORDS.get(tok.text.lower())
    if val is not None:
        return val
    return None

def _extract_x_multiplier(txt: str) -> Optional[int]:
    m = re.search(r"\b(?:x\s*(\d+)|(\d+)\s*x)\b", txt, flags=re.I)
    if not m:
        return None
    return int(m.group(1) or m.group(2))

def _strip_preamble_for_items(text: str) -> str:
    # remove the action & cart phrases to leave only the items list tail
    t = re.sub(r"\b(?:please|pls|thanks?|thank\s*you|kindly|sorry|pardon|excuse\s*me)\b", "", text, flags=re.I)
    t = re.sub(r"\b(?:add|put|include|insert|throw|stick|drop|place|remove|delete|take\s*out|take\s*off|discard|subtract|minus|pull)\b", "", t, flags=re.I|re.UNICODE)
    t = re.sub(r"\b(?:to|into|in)\s+(?:my\s+)?(?:cart|basket|bag|trolley|shopping\s+(?:cart|basket|bag|trolley))\b", "", t, flags=re.I)
    t = re.sub(r"\b(?:from|in)\s+(?:my\s+)?(?:cart|basket|bag|trolley|shopping\s+(?:cart|basket|bag|trolley))\b", "", t, flags=re.I)
    t = re.sub(r"^\s*(?:and|,)\s*", "", t, flags=re.I)
    return _norm(t)

# =============== Intent detection (ruley, spaCy-backed) ===============
ADD_LEMMAS = {"add","put","include","insert","throw","stick","drop","place"}
REMOVE_LEMMAS = {"remove","delete","take","discard","subtract","minus","pull"}
VIEW_LEMMAS = {"show","view","see","display","open","check","list","look"}
CLEAR_LEMMAS = {"clear","empty","reset","wipe","trash","dump"}

def _intent_rules(doc) -> Tuple[str, str]:
    """
    Returns (intent, confidence)
    """
    lo = _lower_tokens(doc)
    dev = _has_dev_context(doc)

    # CLEAR CART
    for t in doc:
        if t.lemma_.lower() in CLEAR_LEMMAS and not _has_neg_before(t):
            if dev and "chart" in lo and "cart" not in lo:
                break
            if _contains_cart_word(doc):
                return "CLEAR_CART", "high"

    if re.search(r"\b(?:remove|delete|drop)\s+(?:all|everything)\s+(?:from\s+)?(?:my\s+)?(?:cart|basket|bag|trolley)\b", doc.text, flags=re.I):
        return "CLEAR_CART", "high"

    # VIEW CART
    if any(t.lemma_.lower() in VIEW_LEMMAS for t in doc):
        if _contains_cart_word(doc):
            # if there's a neg near the verb, degrade confidence a bit
            v = next((t for t in doc if t.lemma_.lower() in VIEW_LEMMAS), None)
            return "VIEW_CART", ("medium" if v and _has_neg_before(v) else "high")

    if re.search(
        r"\b(?:what.*\b(?:in\s+)?(?:my\s+)?(?:cart|basket|bag|trolley)|which.*\b(?:in\s+)?(?:my\s+)?(?:cart|basket|bag|trolley)|cart\s+(?:items?|contents?))\b",
        doc.text, flags=re.I):
        return "VIEW_CART", "high"

    # SHOW PRODUCT LIST
    if not dev:
        if re.search(r"\b(?:product\s*list|price\s*list|list\s+of\s+(?:products?|items?|categories)|browse\s+catalog(?:ue)?)\b", doc.text, flags=re.I):
            return "SHOW_PRODUCT_LIST", "high"
        if re.search(r"\b(?:show|list|browse|see|view|display|explore|open|look|get|find|check)\b.*\b(?:products?|catalog(?:ue)?|inventor(?:y|ies)|categor(?:y|ies)|items?|goods|stock|menu|range|selection|grocer(?:y|ies)|price\s*list)\b", doc.text, flags=re.I):
            return "SHOW_PRODUCT_LIST", "high"
        if re.search(r"\bwhat(?:'s| is)?\s+(?:available|in\s*stock)\b|\bwhat\s+do\s+you\s+(?:have|sell|carry)\b", doc.text, flags=re.I):
            return "SHOW_PRODUCT_LIST", "high"

    # ADD / REMOVE
    add_tok = next((t for t in doc if t.lemma_.lower() in ADD_LEMMAS and not _has_neg_before(t)), None)
    rem_tok = next((t for t in doc if t.lemma_.lower() in REMOVE_LEMMAS and not _has_neg_before(t)), None)

    if add_tok:
        # high if we see cart phrase; else medium
        conf = "high" if _contains_cart_word(doc) else "medium"
        return "ADD_TO_CART", conf

    if rem_tok:
        conf = "high" if _contains_cart_word(doc) else "medium"
        return "REMOVE_FROM_CART", conf

    # CHECK_PRODUCT_EXISTENCE (only if no cart mention)
    if not _contains_cart_word(doc):
        if re.search(r"\b(?:is|are)\s+[\w\-'/&\s]+?\s+(?:available|in\s*stock|on\s*hand|for\s*sale)\b", doc.text, flags=re.I):
            return "CHECK_PRODUCT_EXISTENCE", "high"
        if re.search(r"(?:\bdo\s+you\s+(?:have|carry|sell|stock)\b|\bgot\b|\bis\b|\bare\b).+\?$", doc.text.strip(), flags=re.I):
            return "CHECK_PRODUCT_EXISTENCE", "medium"

    return "NONE", "low"

# =============== Tail extraction for add/remove ===============
def _extract_tail_for_add(doc) -> str:
    txt = doc.text
    # forward: "add <tail> to cart"
    m = re.search(r"\b(?:add|put|include|insert|throw|stick|drop|place)\b(.+?)\b(?:to|into|in)\s+(?:my\s+)?(?:cart|basket|bag|trolley|shopping\s+(?:cart|basket|bag|trolley))\b", txt, flags=re.I)
    if m:
        return _strip_preamble_for_items(m.group(1))
    # reverse: "add to cart <tail>"
    m = re.search(r"\b(?:add|put|include|insert|throw|stick|drop|place)\b\s+\b(?:to|into|in)\s+(?:my\s+)?(?:cart|basket|bag|trolley|shopping\s+(?:cart|basket|bag|trolley))\b\s+(.+)$", txt, flags=re.I)
    if m:
        return _strip_preamble_for_items(m.group(1))
    # implicit: everything after the verb
    m = re.search(r"\b(?:add|put|include|insert|throw|stick|drop|place)\b\s+(.+)$", txt, flags=re.I)
    if m:
        return _strip_preamble_for_items(m.group(1))
    return _strip_preamble_for_items(txt)

def _extract_tail_for_remove(doc) -> str:
    txt = doc.text
    m = re.search(r"\b(?:remove|delete|take(?:\s*(?:out|off))?|discard|subtract|minus|pull)\b(.+?)\b(?:from|in)\s+(?:my\s+)?(?:cart|basket|bag|trolley)\b", txt, flags=re.I)
    if m:
        return _strip_preamble_for_items(m.group(1))
    m = re.search(r"\b(?:remove|delete|take(?:\s*(?:out|off))?|discard|subtract|minus|pull)\b\s+(.+)$", txt, flags=re.I)
    if m:
        return _strip_preamble_for_items(m.group(1))
    return _strip_preamble_for_items(txt)

# =============== Multi-item extraction (spaCy-first with small regex help) ===============
def _best_product_phrase(doc) -> str:
    # Prefer longest noun chunk; if none, fallback to content words
    chunks = [nc.text.strip() for nc in doc.noun_chunks]
    if chunks:
        return max(chunks, key=len)
    # fallback: keep non-function words
    keep = []
    for t in doc:
        if t.is_space: continue
        if t.pos_ in {"DET","ADP","PRON","PART","AUX","CCONJ","SCONJ","PUNCT"}:
            continue
        keep.append(t.text)
    return _norm(" ".join(keep))

def _split_on_and_commas(text: str) -> List[str]:
    # split item list respecting simple delimiters
    parts = re.split(r"\s*(?:,|\band\b)\s*", text, flags=re.I)
    return [p for p in map(_norm, parts) if p]

def _extract_item_from_span(span_text: str) -> Dict[str, Any]:
    """
    Extract one item from a phrase like:
      - "2kg yam" / "yam 2 kg" / "2 boxes stockfish" / "okro x2" / "two yams"
    """
    s = _norm(span_text)

    # x2 / 2x (no unit)
    mult = _extract_x_multiplier(s)

    doc = nlp()(s)

    # pass 1: try to find quantity + unit sequence near each other
    qty: Optional[float] = None
    unit: Optional[str] = None

    # scan tokens; consider patterns:
    # NUM UNIT NOUN...,  NOUN... NUM UNIT,  NUM NOUN (no unit),  WORDNUM NOUN
    toks = list(doc)
    for i, tok in enumerate(toks):
        # numeric
        q = _num_from_token(tok)
        if q is not None:
            qty = q
            # look right for unit
            if i+1 < len(toks) and _is_unit(toks[i+1]):
                unit = toks[i+1].text.lower()
                break
            # look left for unit
            if i-1 >= 0 and _is_unit(toks[i-1]):
                unit = toks[i-1].text.lower()
                break
            # quantity-only case (unit stays None)
            break

    # product phrase: remove qty/unit tokens and "of/the/a/an"
    # Build a mask of tokens to drop
    drop_idx = set()
    if qty is not None:
        # drop num token(s)
        for i, tok in enumerate(toks):
            if tok.like_num or tok.text.lower() in NUMBER_WORDS:
                # keep only the first numeric consumed
                drop_idx.add(i)
                break
    if unit:
        for i, tok in enumerate(toks):
            if _is_unit(tok):
                drop_idx.add(i)

    keep_tokens = []
    for i, tok in enumerate(toks):
        if i in drop_idx:
            continue
        if tok.text.lower() in {"of","the","a","an"}:
            continue
        keep_tokens.append(tok)

    prod_doc = nlp()(" ".join(t.text for t in keep_tokens)) if keep_tokens else doc
    product = _best_product_phrase(prod_doc)
    product = re.sub(r"\b(?:to|into|in|my)\b", "", product, flags=re.I)
    product = re.sub(r"\b(?:cart|basket|bag|trolley)\b", "", product, flags=re.I)
    product = _norm(product)

    # if we still don't have a number but we have x2 => treat as qty
    if qty is None and mult is not None:
        qty = mult

    raw = span_text.strip()
    return {"product": product, "quantity": qty, "unit": unit, "raw": raw if raw else None}

def _extract_items(text: str) -> List[Dict[str, Any]]:
    """
    Split the tail into multiple item phrases and extract each.
    """
    parts = _split_on_and_commas(text)
    items: List[Dict[str, Any]] = []
    for p in parts:
        if not p:
            continue
        # quick guard to drop leftover cart words if present
        p2 = re.sub(r"\b(?:to|into|in|from)\b\s+(?:my\s+)?(?:cart|basket|bag|trolley|shopping\s+(?:cart|basket|bag|trolley))\b", "", p, flags=re.I)
        p2 = re.sub(r"\b(?:please|pls|thanks?|thank\s*you|kindly)\b", "", p2, flags=re.I)
        p2 = _norm(p2)
        if not p2:
            continue
        items.append(_extract_item_from_span(p2))
    return items

# =============== Public API ===============
def _with_items_entities(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Build entities list for the new simplified structure.
    Returns a list of items directly as the entities.
    """
    if not items:
        return []
    return items

def nlu_parse(text: str, intent: Optional[str] = None) -> Dict[str, Any]:
    """
    spaCy NLU that mirrors your regex classifier's return shape.

    Returns:
      {
        "intent": <str>,
        "confidence": "low"|"medium"|"high",
        "entities": [
          { "product": str, "quantity": num|None, "unit": str|None, "raw": str|None },
          ...
        ]
      }
    """
    txt = _normalize_cart_typos(text or "")
    doc = nlp()(txt)

    # If caller forces intent, only do entity extraction for that lane.
    if intent:
        it = intent.upper().strip()
        if it == "ADD_TO_CART":
            tail = _extract_tail_for_add(doc)
            items = _extract_items(tail)
            return {"intent": "ADD_TO_CART", "confidence": "high", "entities": _with_items_entities(items)}
        if it == "REMOVE_FROM_CART":
            tail = _extract_tail_for_remove(doc)
            items = _extract_items(tail)
            return {"intent": "REMOVE_FROM_CART", "confidence": "high", "entities": _with_items_entities(items)}
        if it == "VIEW_CART":
            return {"intent": "VIEW_CART", "confidence": "high", "entities": []}
        if it == "SHOW_PRODUCT_LIST":
            return {"intent": "SHOW_PRODUCT_LIST", "confidence": "high", "entities": []}
        if it == "CHECK_PRODUCT_EXISTENCE":
            # naive: keep object phrase
            # try: "do you have X", "is X available"
            m = re.search(r"(?:do\s+you\s+(?:have|carry|sell|stock)\s+|is\s+|are\s+)(?P<p>[\w\-'/&\s]+?)(?:\s+(?:available|in\s*stock|on\s*hand|for\s*sale))?\??$", txt, flags=re.I)
            prod = _norm(m.group("p")) if m else _norm(txt)
            return {"intent": "CHECK_PRODUCT_EXISTENCE", "confidence": "high", "entities": [{"product": prod}]}
        if it == "CLEAR_CART":
            return {"intent": "CLEAR_CART", "confidence": "high", "entities": []}
        return {"intent": "NONE", "confidence": "low", "entities": []}

    # Otherwise detect intent
    intent_str, conf = _intent_rules(doc)

    if intent_str == "ADD_TO_CART":
        tail = _extract_tail_for_add(doc)
        items = _extract_items(tail)
        return {"intent": "ADD_TO_CART", "confidence": conf, "entities": _with_items_entities(items)}

    if intent_str == "REMOVE_FROM_CART":
        tail = _extract_tail_for_remove(doc)
        items = _extract_items(tail)
        return {"intent": "REMOVE_FROM_CART", "confidence": conf, "entities": _with_items_entities(items)}

    if intent_str == "VIEW_CART":
        return {"intent": "VIEW_CART", "confidence": conf, "entities": []}

    if intent_str == "SHOW_PRODUCT_LIST":
        return {"intent": "SHOW_PRODUCT_LIST", "confidence": conf, "entities": []}

    if intent_str == "CHECK_PRODUCT_EXISTENCE":
        # Try to pull a product chunk
        m = re.search(r"(?:do\s+you\s+(?:have|carry|sell|stock)\s+|is\s+|are\s+)(?P<p>[\w\-'/&\s]+?)(?:\s+(?:available|in\s*stock|on\s*hand|for\s*sale))?\??$", txt, flags=re.I)
        prod = _norm(m.group("p")) if m else ""
        if not prod:
            # fallback: longest noun chunk
            prod = max((nc.text.strip() for nc in doc.noun_chunks), key=len, default="")
        return {"intent": "CHECK_PRODUCT_EXISTENCE", "confidence": conf, "entities": ([{"product": prod}] if prod else [])}

    return {"intent": "NONE", "confidence": "low", "entities": []}

# =============== Small CLI for quick testing ===============
if __name__ == "__main__":
    print("🤖 NLU (spaCy) - Interactive Mode")
    print("=================================")
    print("Type 'quit' to exit")
    print("Type 'samples' to see example parses")
    print("Tip: pass an explicit intent by typing: intent=ADD_TO_CART your text\n")

    SAMPLES = [
        "show product list",
        "what do you sell",
        "which products are listed in my cart",
        "empty my cart",
        "add 2kg fish to cart",
        "add fish 2kg",
        "put a couple of yams into the cart please",
        "remove 2 plantains from cart",
        "add 2kg yam and 4kg beans to cart",
        "add 1 box stockfish, 3kg crayfish and okro x2 to my cart",
        "remove 2kg yam and 1kg beans from my basket",
        "add to chart 3 plantain",
        "add fish to cart",
        # forced-intent
        ("yam 3", "ADD_TO_CART"),
        ("3 yams", "ADD_TO_CART"),
        ("four yams", "ADD_TO_CART"),
        ("remove 1 yam from cart", "REMOVE_FROM_CART"),
    ]

    while True:
        try:
            s = input("Enter text: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break
        if not s:
            continue
        if s.lower() in {"quit","exit"}:
            print("Bye!")
            break
        if s.lower() == "samples":
            for ex in SAMPLES:
                if isinstance(ex, tuple):
                    text, forced = ex
                    print(f"{text!r} with intent={forced} =>", nlu_parse(text, forced))
                else:
                    print(ex, "=>", nlu_parse(ex))
            continue
        forced_intent = None
        if s.lower().startswith("intent="):
            parts = s.split(None, 1)
            forced_intent = parts[0].split("=", 1)[1]
            s = parts[1] if len(parts) > 1 else ""
        print("🎯 NLU:", nlu_parse(_normalize_cart_typos(s), forced_intent))
