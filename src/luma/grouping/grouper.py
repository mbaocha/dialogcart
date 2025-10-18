"""
Stage 3: Entity Grouping & Alignment

Entity Grouper for semantic grouping of extracted entities.
Ported from semantics/entity_grouping.py with 100% compatibility.
Groups entities by action and aligns quantities/units/variants to products.
"""
import json
import re
from typing import List, Dict, Any, Optional, Tuple

# ===== CONFIGURATION =====
from luma.config import config, debug_print

# Intent mapper initialization
if config.ENABLE_INTENT_MAPPER:
    from luma.grouping.intent_mapper import IntentMapper
    intent_mapper = IntentMapper()  # initialize once globally
else:
    intent_mapper = None


def log_debug(msg: str, data: Optional[Any] = None):
    """
    Log debug message with optional data.
    
    NOTE: Matches semantics/entity_grouping.py lines 37-46 exactly
    """
    if not config.DEBUG_ENABLED:
        return
    debug_print(f"[DEBUG] {msg}")
    if data is not None:
        try:
            debug_print(json.dumps(data, indent=2))
        except TypeError:
            debug_print(data)
    debug_print("-" * 80)


def route_intent(status: str, groups: List[Dict], tokens: Optional[List] = None, labels: Optional[List] = None) -> Tuple[str, Optional[str]]:
    """
    Determine routing path (rule, memory, llm) based on status and groups.
    
    Args:
        status: Processing status
        groups: List of entity groups
        tokens: Optional token list
        labels: Optional label list
        
    Returns:
        Tuple of (route, route_reason)
        
    NOTE: Matches semantics/entity_grouping.py lines 50-120 exactly
    """
    route_reason = None
    
    if status in ["invalid_slot_fill", "partial_fill"]:
        route = "llm"
        route_reason = status
    
    elif not groups:
        route = "llm"
        route_reason = "no_groups_extracted"
    
    # 2️⃣ Referential requests (pronouns) → Memory
    # Check if products contain pronouns like "it", "that", "them", "one"
    elif any(
        t and t.lower() in {"it", "that", "them", "one"}
        for g in groups
        for t in g.get("products", [])
    ):
        route = "memory"
        route_reason = "referential_request"
    
    elif len(groups) == 1 and not groups[0].get("products") and not groups[0].get("ordinal_ref"):
        # No products AND no ordinal → LLM
        route = "llm"
        route_reason = "no_product_or_ordinal_in_group"
    
    else:
        # Has products OR has ordinal_ref → RULE
        route = "rule"
        route_reason = None
    
    return route, route_reason


def check_group_token_order(grouped_result: Dict, tokens: Optional[List] = None, labels: Optional[List] = None) -> Dict:
    """
    Trigger LLM routing if structural issues are detected:
      - BRAND appears *immediately* after PRODUCT (no gap)
      - consecutive BRAND tokens
      - VARIANT appears *immediately* after PRODUCT
    
    Args:
        grouped_result: Grouping result dict
        tokens: Optional token list
        labels: Optional label list
        
    Returns:
        Grouped result with updated status if issues detected
        
    NOTE: Matches semantics/entity_grouping.py lines 125-163 exactly
    """
    issues = []
    
    if not tokens or not labels:
        debug_print("[DEBUG] check_group_token_order: missing tokens or labels")
        return grouped_result
    
    # Build positional index map of labeled entities
    entity_positions = [
        (i, labels[i].upper()) 
        for i in range(len(labels)) 
        if any(k in labels[i].upper() for k in ["PRODUCT", "BRAND", "VARIANT", "TOKEN"])
    ]
    
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
        debug_print("[DEBUG] check_group_token_order: flagged issues", issues)
    else:
        debug_print("[DEBUG] check_group_token_order: no structural issues detected")
    
    return grouped_result


def extract_entities(tokens: List[str], labels: List[str]) -> Dict[str, Any]:
    """
    Extract entities from token/label lists.
    
    Args:
        tokens: List of token strings
        labels: List of label strings
        
    Returns:
        Dictionary with extracted entities by type
        
    NOTE: Matches semantics/entity_grouping.py lines 168-273 exactly
    NOTE: Extended to support B-ORDINAL for positional references
    """
    action_tokens, brands, products, quantities, units, variants, ordinals = [], [], [], [], [], [], []
    
    for i, (tok, lab) in enumerate(zip(tokens, labels)):
        lab_type = lab.replace("B-", "").replace("I-", "")
        
        if lab_type == "ACTION":
            action_tokens.append(tok)
        elif lab_type == "BRAND":
            brands.append(tok)
        elif lab_type == "PRODUCT":
            products.append(tok)
        elif lab_type == "QUANTITY":
            quantities.append(tok)
        elif lab_type == "UNIT":
            units.append(tok)
        elif lab_type == "TOKEN":
            variants.append(tok)
        elif lab_type == "ORDINAL":
            ordinals.append(tok)
    
    action = " ".join(action_tokens) if action_tokens else ""
    
    return {
        "action": action,
        "brands": brands,
        "products": products,
        "quantities": quantities,
        "units": units,
        "variants": variants,
        "ordinals": ordinals
    }


def align_quantities_to_products(
    tokens: List[str],
    labels: List[str],
    products: List[str],
    quantities: List[str],
    units: List[str]
) -> Tuple[List[Optional[str]], List[Optional[str]]]:
    """
    Align each product to the nearest preceding quantity + unit in token order.
    Simple propagation: quantities propagate to subsequent products unless
    a new quantity+unit pair appears.
    
    Args:
        tokens: List of token strings
        labels: List of label strings
        products: List of product strings
        quantities: List of quantity strings
        units: List of unit strings
        
    Returns:
        Tuple of (aligned_quantities, aligned_units)
        
    NOTE: Matches semantics/entity_grouping.py lines 277-354 exactly
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
        
        # CRITICAL: unit → last one before product, or immediate one after if none before
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


def determine_status(intent: Optional[str], action: str, products: List[str], quantities: List[str], brands: List[str], ordinals: Optional[List[str]] = None) -> Tuple[str, Optional[str]]:
    """
    Determine processing status based on extracted entities.
    
    Args:
        intent: Mapped intent
        action: Action string
        products: List of products
        quantities: List of quantities
        brands: List of brands
        ordinals: List of ordinal references (optional)
        
    Returns:
        Tuple of (status, reason)
        
    NOTE: Matches semantics/entity_grouping.py lines 357-380 exactly
    NOTE: Extended to support ordinal references
    """
    status = "ok"
    reason = None
    
    if not action:
        status = "error"
        reason = "no_action"
    elif not products and not brands and not (ordinals and len(ordinals) > 0):
        # No products/brands AND no ordinals = needs LLM
        status = "needs_llm"
        reason = "no_product_or_brand"
    
    return status, reason


def simple_group_entities(tokens: List[str], labels: List[str], debug: bool = False) -> Dict[str, Any]:
    """
    Group entities by action and product.
    
    Args:
        tokens: List of token strings
        labels: List of label strings
        debug: Enable debug logging
        
    Returns:
        Grouping result with status and groups
        
    NOTE: Matches semantics/entity_grouping.py lines 384-424 EXACTLY
    """
    debug_print(f"[DEBUG] simple_group_entities -> tokens: {tokens}, labels: {labels}")
    ents = extract_entities(tokens, labels)
    action = ents["action"]
    
    # Intent mapping
    # NOTE: Matches semantics/entity_grouping.py lines 390-393 exactly
    if config.ENABLE_INTENT_MAPPER and intent_mapper and action:
        intent, score = intent_mapper.map_action_to_intent(action)
    else:
        intent, score = None, None
    
    products = ents["products"]
    ordinals = ents.get("ordinals", [])
    
    quantities, units = align_quantities_to_products(tokens, labels, products, ents["quantities"], ents["units"])
    
    groups = []
    
    # NEW: If no products but ordinals exist, create one group per ordinal
    if not products and ordinals:
        for i, ord_ref in enumerate(ordinals):
            q = quantities[i] if i < len(quantities) else None
            u = units[i] if i < len(units) else None
            brand = [ents["brands"][i]] if i < len(ents["brands"]) else []
            
            g = {
                "action": action,
                "intent": intent,
                "intent_confidence": score,
                "products": [],  # Empty - ordinal will be resolved by agent
                "quantities": [q] if q else [],
                "units": [u] if u else [],
                "brands": brand,
                "variants": [ents["variants"][i]] if i < len(ents["variants"]) else [],
                "ordinal_ref": ord_ref,  # Each group gets one ordinal
            }
            groups.append(g)
    else:
        # Original logic: group by products
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
                "ordinal_ref": None,  # No ordinal when explicit products present
            }
            groups.append(g)
    
    status, reason = determine_status(intent, action, products, quantities, ents["brands"], ordinals)
    res = {"status": status, "reason": reason, "groups": groups}
    
    debug_print(f"[DEBUG] simple_group_entities -> res: {res}")
    return res


def index_parameterized_tokens(tokens: List[str]) -> List[str]:
    """
    Index tokens like 'producttoken' → 'producttoken_1', 'producttoken_2', etc.
    
    Args:
        tokens: List of token strings
        
    Returns:
        List of indexed tokens
        
    NOTE: Matches semantics/entity_grouping.py lines 431-451 exactly
    """
    counts = {}
    indexed = []
    for tok in tokens:
        if tok.lower() in {"producttoken", "brandtoken", "unittoken", "varianttoken"}:
            base = tok.lower()
            counts[base] = counts.get(base, 0) + 1
            indexed.append(f"{base}_{counts[base]}")
        else:
            indexed.append(tok)
    return indexed


def decide_processing_path(
    tokens: List[str],
    labels: List[str],
    memory_state: Optional[Dict] = None
) -> Tuple[Dict, str, Optional[str]]:
    """
    Main entry point for grouping logic.
    
    Args:
        tokens: List of token strings
        labels: List of label strings
        memory_state: Optional memory state (not used in basic version)
        
    Returns:
        Tuple of (grouped_result, route, route_reason)
        
    NOTE: Matches semantics/entity_grouping.py lines 455-467 EXACTLY
    """
    grouped = simple_group_entities(tokens, labels)
    
    # 🔍 Structural order check before routing
    grouped = check_group_token_order(grouped, tokens, labels)
    
    route, route_reason = route_intent(grouped["status"], grouped["groups"])
    
    return grouped, route, route_reason