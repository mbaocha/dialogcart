#!/usr/bin/env python3
"""
Simplified grouping layer with separate routing logic.
Groups tokens by action + product.
Routing (rule vs memory vs llm) handled in a dedicated function.
"""

import json
import os
import re


# ===== LOGGING CONFIGURATION =====
# Set DEBUG_NLP=1 in environment to enable debug logs
DEBUG_ENABLED = os.environ.get("DEBUG_NLP", "0") == "1"

def debug_print(*args, **kwargs):
    """Print debug message only if DEBUG_ENABLED is True"""
    if DEBUG_ENABLED:
        print(*args, **kwargs)


# ------------------------------------------------------
# 🧠 Optional Intent Mapper Toggle
# ------------------------------------------------------
ENABLE_INTENT_MAPPER = False

if ENABLE_INTENT_MAPPER:
    from intent_mapper import IntentMapper
    intent_mapper = IntentMapper()  # initialize once globally
else:
    intent_mapper = None


DEBUG = DEBUG_ENABLED  # use environment variable

def log_debug(msg, data=None):
    if not DEBUG:
        return
    debug_print(f"[DEBUG] {msg}")
    if data is not None:
        try:
            debug_print(json.dumps(data, indent=2))
        except TypeError:
            debug_print(data)
    debug_print("-" * 80)
# ------------------------------------------------------
# ⚙️ ROUTING LOGIC
# ------------------------------------------------------
def route_intent(status, groups, tokens=None, labels=None):
    """
    Decide how to handle the request: via rule-based logic, memory, or LLM.
    Uses semantic intent if available (from IntentMapper).
    Returns:
        (route, reason)
        route ∈ {"rule", "memory", "llm"}
        reason = diagnostic string
    """
    route = "rule"
    reason = None

    # 1️⃣ Extraction or structural error
    if status != "ok":
        # Still allow check/availability intents to stay rule-based
        if any(
            (g.get("intent") and g["intent"].startswith("check"))
            for g in groups
        ):
            route, reason = "rule", "availability_check"
        else:
            route, reason = "llm", status
        return route, reason

    # 2️⃣ Referential requests → Memory
    if any(
        t and t.lower() in {"it", "that", "them", "one"}
        for g in groups
        for t in g.get("products", [])
    ):
        route, reason = "memory", "referential_request"
        return route, reason

    # 3️⃣ Open-ended queries → LLM
    if any(
        (g.get("intent") and g["intent"] in {"open_query", "browse_catalog"})
        for g in groups
    ):
        route, reason = "llm", "open_query"
        return route, reason

    # 4️⃣ Multi-intent (multiple distinct intents) → LLM
    intents = {g.get("intent") for g in groups if g.get("intent")}
    if len(intents) > 1:
        route, reason = "llm", "multi_intent_detected"
        return route, reason

    # 4.5️⃣ Exact duplicate groups (same product+brand+quantity+unit) → LLM
    # Only flag truly ambiguous duplicates where ALL key attributes match
    group_signatures = {}
    for i, g in enumerate(groups):
        intent = g.get("intent")
        products = tuple([p.lower() for p in g.get("products", [])])
        brands = tuple(sorted([b.lower() for b in g.get("brands", [])]))
        quantities = tuple(g.get("quantities", []))
        units = tuple(g.get("units", []))
        
        # Create signature from all attributes
        signature = (intent, products, brands, quantities, units)
        
        if signature in group_signatures:
            # Found exact duplicate - this is ambiguous
            route, reason = "llm", "duplicate_product_groups_detected"
            return route, reason
        group_signatures[signature] = i

    # 5️⃣ No product or brand → LLM (unclear context)
    if all(not g.get("products") and not g.get("brands") for g in groups):
        route, reason = "llm", "no_product_or_brand"
        return route, reason

    # ✅ Default → rule
    return route, reason


def check_group_token_order(grouped_result, tokens=None, labels=None):
    """
    Trigger LLM routing only if:
      - BRAND appears *immediately* after PRODUCT (no gap)
      - consecutive BRAND tokens
      - VARIANT appears *immediately* after PRODUCT
    """
    issues = []

    if not tokens or not labels:
        log_debug("check_group_token_order: missing tokens or labels")
        return grouped_result

    # Build positional index map of labeled entities
    entity_positions = [(i, labels[i].upper()) for i in range(len(labels)) if any(k in labels[i].upper() for k in ["PRODUCT", "BRAND", "VARIANT", "TOKEN"])]

    for j in range(len(entity_positions) - 1):
        idx, curr_label = entity_positions[j]
        next_idx, next_label = entity_positions[j + 1]

        # ✅ Only trigger if *truly adjacent* in the original token list
        if next_idx == idx + 1:
            if "PRODUCT" in curr_label and "BRAND" in next_label:
                issues.append(f"brand appears immediately after product ({tokens[idx]} {tokens[next_idx]})")
            elif "BRAND" in curr_label and "BRAND" in next_label:
                issues.append(f"consecutive brands ({tokens[idx]} {tokens[next_idx]})")
            elif "PRODUCT" in curr_label and ("VARIANT" in next_label or "TOKEN" in next_label):
                issues.append(f"product followed immediately by variant ({tokens[idx]} {tokens[next_idx]})")

    if issues:
        grouped_result["status"] = "needs_llm_fix"
        grouped_result["notes"] = grouped_result.get("notes", []) + issues
        log_debug("check_group_token_order: flagged issues", issues)
    else:
        log_debug("check_group_token_order: no structural issues detected")

    return grouped_result



# ------------------------------------------------------
# 🧩 GROUPING LOGIC
# ------------------------------------------------------
def extract_entities(tokens, labels):
    """
    Extract base entity lists from model outputs (tokens + labels).
    """
    action_tokens, products, quantities, units, brands, variants = [], [], [], [], [], []

    for i, (tok, lab) in enumerate(zip(tokens, labels)):
        lab_u = lab.upper()
        tok_l = tok.lower()

        # --- ACTION ---
        if "ACTION" in lab_u:
            action_tokens.append(tok_l)

        # --- QUANTITY ---
        elif "QUANTITY" in lab_u:
            quantities.append(tok_l)

        # --- OTHER ENTITY TYPES ---
        elif "UNIT" in lab_u:
            units.append(tok_l)
        elif "BRAND" in lab_u:
            brands.append(tok_l)
        elif "PRODUCT" in lab_u:
            products.append(tok_l)
        elif "VARIANT" in lab_u or "TOKEN" in lab_u:
            variants.append(tok_l)

    return {
        "action": " ".join(action_tokens).strip() if action_tokens else None,
        "products": products,
        "quantities": quantities,
        "units": units,
        "brands": brands,
        "variants": variants,
    }


def align_quantities_to_products0(products, quantities, units):
    """
    Smart quantity alignment logic.
    Handles cases like:
      - 1 quantity → multiple products (shared)
      - multiple quantities < products (propagate forward)
      - multiple quantities == products (1:1 mapping)
    """
    n_products, n_quantities, n_units = len(products), len(quantities), len(units)

    # Case 1: shared quantity
    if n_quantities == 1 and n_products > 1:
        quantities = [quantities[0]] * n_products
        units = [units[0] if n_units else None] * n_products

    # Case 2: fewer quantities than products (propagate forward)
    elif 0 < n_quantities < n_products:
        expanded_q, expanded_u = [], []
        q_i = 0
        for i in range(n_products):
            expanded_q.append(quantities[q_i])
            expanded_u.append(units[q_i] if q_i < n_units else None)
            # move to next quantity only when next one exists and enough products left
            if q_i + 1 < n_quantities and (i + 1) >= (n_products // n_quantities):
                q_i += 1
        quantities, units = expanded_q, expanded_u

    # Case 3: no quantities → all None
    elif n_quantities == 0:
        quantities = [None] * n_products
        units = [None] * n_products

    # Else: 1:1 mapping or empty
    return quantities, units

def align_quantities_to_products00(tokens, labels, products, quantities, units):
    """
    Align each product to the nearest preceding quantity + unit in token order.
    Simple propagation: quantities propagate to subsequent products unless
    a new quantity+unit pair appears.
    """
    # --- collect token indexes by label type ---
    qty_pos = [i for i, l in enumerate(labels) if "QUANTITY" in l.upper()]
    unit_pos = [i for i, l in enumerate(labels) if "UNIT" in l.upper()]
    prod_pos = [i for i, l in enumerate(labels) if "PRODUCT" in l.upper()]

    aligned_q, aligned_u = [], []

    for pi, p_idx in enumerate(prod_pos):
        # quantity → last one that appears before product
        q_idx = max([i for i in qty_pos if i < p_idx], default=None)
        
        # unit → last one before product, or immediate one after if none before
        u_idx = max([i for i in unit_pos if i < p_idx], default=None)
        if u_idx is None and unit_pos:
            u_idx = min([i for i in unit_pos if i > p_idx], default=unit_pos[-1])

        # fetch value safely
        q_val = None
        u_val = None

        if q_idx is not None and qty_pos:
            q_val = quantities[qty_pos.index(q_idx)] if qty_pos.index(q_idx) < len(quantities) else None
        if u_idx is not None and unit_pos:
            u_val = units[unit_pos.index(u_idx)] if unit_pos.index(u_idx) < len(units) else None

        aligned_q.append(q_val)
        aligned_u.append(u_val)

    return aligned_q, aligned_u

def align_quantities_to_products(tokens, labels, products, quantities, units):
    """
    Align each product to the nearest preceding quantity + unit in token order.
    Simple propagation: quantities propagate to subsequent products unless
    a new quantity+unit pair appears.
    """

    # --- local debug helper ---
    def _dbg(msg, data=None):
        debug_print(f"[DEBUG] {msg}")
        if data is not None:
            import json
            try:
                debug_print(json.dumps(data, indent=2))
            except TypeError:
                debug_print(data)
        debug_print("-" * 60)

    # --- initial input snapshot ---
    _dbg("align_quantities_to_products INPUT", {
        "tokens": tokens,
        "labels": labels,
        "products": products,
        "quantities": quantities,
        "units": units
    })

    # --- collect token indexes by label type ---
    qty_pos = [i for i, l in enumerate(labels) if "QUANTITY" in l.upper()]
    unit_pos = [i for i, l in enumerate(labels) if "UNIT" in l.upper()]
    prod_pos = [i for i, l in enumerate(labels) if "PRODUCT" in l.upper()]

    _dbg("Entity positions", {
        "qty_pos": qty_pos,
        "unit_pos": unit_pos,
        "prod_pos": prod_pos
    })

    aligned_q, aligned_u = [], []

    # --- main alignment loop ---
    for pi, p_idx in enumerate(prod_pos):
        # quantity → last one that appears before product
        q_idx = max([i for i in qty_pos if i < p_idx], default=None)
        
        # unit → last one before product, or immediate one after if none before
        u_idx = max([i for i in unit_pos if i < p_idx], default=None)
        if u_idx is None and unit_pos:
            u_idx = min([i for i in unit_pos if i > p_idx], default=unit_pos[-1])

        # fetch value safely
        q_val = None
        u_val = None

        if q_idx is not None and qty_pos:
            q_val = quantities[qty_pos.index(q_idx)] if qty_pos.index(q_idx) < len(quantities) else None
        if u_idx is not None and unit_pos:
            u_val = units[unit_pos.index(u_idx)] if unit_pos.index(u_idx) < len(units) else None

        aligned_q.append(q_val)
        aligned_u.append(u_val)

        _dbg(f"Alignment for product {pi+1}", {
            "product_token": tokens[p_idx],
            "product_index": p_idx,
            "matched_quantity_index": q_idx,
            "matched_quantity_value": q_val,
            "matched_unit_index": u_idx,
            "matched_unit_value": u_val
        })

    # --- final result snapshot ---
    _dbg("Final aligned quantities/units", {
        "aligned_quantities": aligned_q,
        "aligned_units": aligned_u
    })

    return aligned_q, aligned_u


def determine_status(intent, action, products, quantities, brands):
    """Determine overall extraction status."""
    n_products, n_quantities = len(products), len(quantities)
    status, reason = "ok", None

    if not action and not products and not brands:
        return "needs_llm_fix", "no_action_or_product"

    if intent and intent.startswith(("modify_cart:add", "modify_cart:set", "modify_cart:remove")):
        if n_quantities > n_products:
            return "needs_llm_fix", "extra_quantity_no_product"
        elif 0 < n_quantities < n_products:
            return "needs_llm_fix", "missing_quantity_for_product"
        return "ok", None

    if intent and intent.startswith("check"):
        return "ok", None

    if intent in {"open_query", "browse_catalog"}:
        return "needs_llm_fix", "open_ended_request"

    if n_quantities not in {0, n_products}:
        return "needs_llm_fix", "mismatched_quantity_product_count"

    return status, reason


def simple_group_entities(tokens, labels, debug=False):
    debug_print(f"[DEBUG] simple_group_entities -> tokens: {tokens}, labels: {labels}")
    ents = extract_entities(tokens, labels)
    action = ents["action"]

    # Intent mapping
    if ENABLE_INTENT_MAPPER and intent_mapper and action:
        intent, score = intent_mapper.map_action_to_intent(action)
    else:
        intent, score = None, None

    products = ents["products"]

    quantities, units = align_quantities_to_products(tokens, labels, products, ents["quantities"], ents["units"])


    groups = []
    for i, prod in enumerate(products or [None]):
        q = quantities[i] if i < len(quantities) else None
        u = units[i] if i < len(units) else None

        # ✅ NEW: assign brand by index, not full copy
        brand = [ents["brands"][i]] if i < len(ents["brands"]) else []

        g = {
            "action": action,
            "intent": intent,
            "intent_confidence": score,
            "products": [prod] if prod else [],
            "quantities": [q] if q else [],
            "units": [u] if u else [],
            "brands": brand,          # ✅ fixed
            "variants": [ents["variants"][i]] if i < len(ents["variants"]) else [],
        }
        groups.append(g)

    status, reason = determine_status(intent, action, products, quantities, ents["brands"])
    res = {"status": status, "reason": reason, "groups": groups}

    debug_print(f"[DEBUG] simple_group_entities -> res: {res}")
    return res


# ------------------------------------------------------
# 🧱 Grouping Layer (Pure Structural)
# ------------------------------------------------------

def index_parameterized_tokens(tokens):
    """
    Index tokens like 'producttoken' → 'producttoken_1', 'producttoken_2', etc.
    Returns:
        indexed_tokens (list)
        index_map (dict): maps token to base type, e.g. {"producttoken_1": "producttoken"}
    """
    counters = {"producttoken": 0, "brandtoken": 0, "varianttoken": 0, "unittoken": 0, "quantitytoken": 0}
    indexed_tokens = []
    index_map = {}

    for tok in tokens:
        base = tok.lower()
        if base in counters:
            counters[base] += 1
            indexed = f"{base}_{counters[base]}"
            indexed_tokens.append(indexed)
            index_map[indexed] = base
        else:
            indexed_tokens.append(base)

    return indexed_tokens, index_map


def decide_processing_path(tokens, labels, memory_state=None):
    grouped = simple_group_entities(tokens, labels)

    # 🔍 Structural order check before routing
    # grouped = check_group_token_order(grouped)
    grouped = check_group_token_order(grouped, tokens, labels)


    route, route_reason = route_intent(grouped["status"], grouped["groups"])
    grouped["route"] = route
    grouped["route_reason"] = route_reason or grouped.get("reason")

    if route == "memory" and memory_state:
        grouped["memory_hit"] = {
            "products": memory_state.get("last_products", []),
            "brands": memory_state.get("last_brands", []),
        }

    return grouped


# ------------------------------------------------------
# 🧪 Quick Tests
# ------------------------------------------------------

if __name__ == "__main__":
    examples = [
    # --- ADD / MODIFY CART ---
    (["add", "2", "kg", "rice"],
     ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT"],
     "rule", "ok", "✅ 1:1 quantity-product mapping"),

    (["add", "2", "kg", "rice", "and", "beans"],
     ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT", "O", "B-PRODUCT"],
     "rule", "ok", "✅ shared quantity across multiple products"),

    (["add", "rice", "and", "2", "kg", "beans"],
     ["B-ACTION", "B-PRODUCT", "O", "B-QUANTITY", "B-UNIT", "B-PRODUCT"],
     "rule", "ok", "✅ backward propagation of quantity"),

    (["add", "3", "kg", "rice", "and", "6", "kg", "beans"],
     ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT", "O", "B-QUANTITY", "B-UNIT", "B-PRODUCT"],
     "rule", "ok", "✅ separate quantities for each product"),

    (["add", "rice", "beans", "and", "yam", "5", "6", "7", "kg"],
     ["B-ACTION", "B-PRODUCT", "B-PRODUCT", "O", "B-PRODUCT", "B-QUANTITY", "B-QUANTITY", "B-QUANTITY", "B-UNIT"],
     "rule", "ok", "✅ late quantities matched sequentially"),

    (["add", "2", "bottles", "of", "brandtoken", "producttoken", "varianttoken", "and", "varianttoken"],
     ["B-ACTION", "B-QUANTITY", "B-UNIT", "O", "B-BRAND", "B-PRODUCT", "B-VARIANT", "O", "B-VARIANT"],
     "rule", "ok", "✅ variants attached to same product"),

    (["add", "rice", "and", "beans"],
     ["B-ACTION", "B-PRODUCT", "O", "B-PRODUCT"],
     "rule", "ok", "✅ products without quantity allowed (quantity=None)"),

    (["add", "the", "red", "bag", "to", "cart"],
     ["B-ACTION", "O", "O", "B-PRODUCT", "O", "O"],
     "rule", "ok", "✅ no quantity — still valid add"),

    # --- CHECK / AVAILABILITY ---
    (["do", "you", "sell", "brandtoken"],
     ["B-ACTION", "I-ACTION", "I-ACTION", "B-BRAND"],
     "rule", "ok", "✅ brand-only availability check"),

    (["do", "you", "sell", "brandtoken", "producttoken"],
     ["B-ACTION", "I-ACTION", "I-ACTION", "B-BRAND", "B-PRODUCT"],
     "rule", "ok", "✅ brand+product availability check"),

    (["do", "you", "sell", "brandtoken", "or", "producttoken", "brandtoken"],
     ["B-ACTION", "I-ACTION", "I-ACTION", "B-BRAND", "O", "B-PRODUCT", "B-BRAND"],
     "rule", "ok", "✅ multiple availability comparisons"),

    (["do", "you", "have", "it"],
     ["B-ACTION", "I-ACTION", "I-ACTION", "O"],
     "memory", "ok", "✅ referential memory lookup (it → memory)"),

    (["what", "do", "you", "sell"],
     ["B-ACTION", "I-ACTION", "I-ACTION", "I-ACTION"],
     "llm", "ok", "✅ open-ended query routed to LLM"),

    (["show", "me", "all", "your", "products"],
     ["B-ACTION", "I-ACTION", "I-ACTION", "I-ACTION", "B-PRODUCT"],
     "llm", "ok", "✅ open-ended browse query routed to LLM"),

     (
    ["add", "5", "bag", "rice", "and", "beans", "and", "a", "kg", "yam"],
    ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT", "O", "B-PRODUCT", "O", "B-QUANTITY", "B-UNIT", "B-PRODUCT"],
    "rule",
    "ok",
    "✅ shared 5kg across rice and beans, then 4kg for yam"
    ),

    (
    ["add", "2", "bags", "of", "brandtoken", "producttoken",
     ",", "3", "tins", "of", "brandtoken", "producttoken",
     ",", "and", "a", "crate", "of", "producttoken", "to", "my", "cart"],
    [
        "B-ACTION", "B-QUANTITY", "B-UNIT", "O", "B-BRAND", "B-PRODUCT",
        "O", "B-QUANTITY", "B-UNIT", "O", "B-BRAND", "B-PRODUCT",
        "O", "O", "B-QUANTITY", "B-UNIT", "O", "B-PRODUCT", "O", "O", "O"
    ],
    "rule",
    "ok",
    "✅ multi-product add: 2 bags of Mama Gold rice, 3 tins of Peak milk, 1 crate of Coca-Cola"
    ),


    ]


    memory_state = {
        "last_products": ["rice"],
        "last_brands": ["dangote"],
        "last_action": "add"
    }

    print("\n=== 🧩 Grouping & Routing Validation ===")

    pass_count = 0
    fail_count = 0

    for i, (tokens, labels, expected_route, expected_status, note) in enumerate(examples, 1):
        print(f"\n[{i}] Sentence: {' '.join(tokens)}")

        grouped = simple_group_entities(tokens, labels)
        route, route_reason = route_intent(grouped["status"], grouped["groups"], tokens, labels)

        route_pass = route == expected_route
        status_pass = grouped["status"] == expected_status
        overall_pass = route_pass and status_pass

        if overall_pass:
            print(f"✅ PASS: {note}")
            pass_count += 1
        else:
            print(
                f"❌ FAIL: Expected route={expected_route}, status={expected_status} "
                f"→ Got route={route}, status={grouped['status']}"
            )
            if route_reason:
                print(f"   🧠 Route reason: {route_reason}")
            fail_count += 1

        # Show concise grouped summary
        print(json.dumps({
            "status": grouped["status"],
            "reason": grouped.get("reason"),
            "route": route,
            "route_reason": route_reason,
            "groups": grouped["groups"],
        }, indent=2))

    print(f"\nSummary: {pass_count} passed, {fail_count} failed")

