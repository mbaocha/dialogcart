import importlib
import inspect
from typing import List, Dict, Any, Tuple, Optional

# Centralized constants for the Bulkpot agent system


# Intent Names (for reference and validation)
INTENT_NAMES = {
    "SHOW_PRODUCT_LIST",
    "VIEW_CART",
    "ADD_TO_CART",
    "REMOVE_FROM_CART", 
    "CLEAR_CART",
    "CHECK_PRODUCT_EXISTENCE",
    "RESTORE_CART",
    "UPDATE_CART_QUANTITY"
}

# Intent to Tool Mappings
INTENT_TO_TOOL = {
    "SHOW_PRODUCT_LIST": "list_catalog_by_categories_formatted",
    "VIEW_CART": "get_cart_formatted",
    "ADD_TO_CART": "add_item_to_cart",
    "REMOVE_FROM_CART": "remove_item_from_cart",
    "CLEAR_CART": "clear_cart",
    "CHECK_PRODUCT_EXISTENCE": "search_catalog",
    "RESTORE_CART": "restore_cart",
    "UPDATE_CART_QUANTITY": "update_cart_quantity"
}

# Entity-based Intent Field Mappings
ENTITY_INTENTS = {
    "ADD_TO_CART": ["product", "quantity", "unit"],
    "REMOVE_FROM_CART": ["product", "quantity", "unit"], 
    "CHECK_PRODUCT_EXISTENCE": ["product"],
    "UPDATE_CART_QUANTITY": ["product", "quantity", "update_op"]
}

# Required slots for each intent to be considered complete
REQUIRED_SLOTS = {
    "ADD_TO_CART": ["product"],
    "REMOVE_FROM_CART": ["product"],
    "CHECK_PRODUCT_EXISTENCE": ["product"],
    "SEARCH_PRODUCTS": ["product"],
    "UPDATE_CART_QUANTITY": ["product", "quantity"]
}

# Tool Categories
PRODUCT_ACTION_TOOLS = {
    "add_item_to_cart", 
    "remove_item_from_cart"
}

# Intent Categories
SAFE_PREV_INTENTS = {
    "ADD_TO_CART", 
    "REMOVE_FROM_CART", 
    "VIEW_CART", 
    "SHOW_PRODUCT_LIST", 
    "CLEAR_CART",
    "RESTORE_CART",
    "UPDATE_CART_QUANTITY"
}


import importlib
import inspect
from typing import List, Dict, Any, Tuple, Optional


DEFAULT_API_MODULES = ["features.catalog", "features.cart"]


def discover_tools(api_modules: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Discover pure Python functions from `tools.py` inside each module in `api_modules`.
    Returns a list of dicts:
      {
        "name": <function_name>,                # simple name
        "qualname": <module.tools.func>,        # fully-qualified
        "args": [<arg names in order>],         # excludes *args/**kwargs names
        "accepts_kwargs": <bool>,               # True if func(**kwargs)
        "func": <callable>                      # actual function
      }
    """
    api_modules = api_modules or DEFAULT_API_MODULES
    discovered: List[Dict[str, Any]] = []
    seen_names: set[str] = set()

    for mod in api_modules:
        module_path = f"{mod}.tools"
        try:
            tools_mod = importlib.import_module(module_path)
        except ImportError as e:
            print(f"[WARN] discover_tools: failed to import {module_path}: {e}")
            continue

        export_names: Optional[List[str]] = getattr(tools_mod, "__all__", None)

        if export_names:
            candidates: List[Tuple[str, Any]] = []
            for name in export_names:
                obj = getattr(tools_mod, name, None)
                if inspect.isfunction(obj):
                    candidates.append((name, obj))
        else:
            candidates = [
                (name, obj)
                for name, obj in inspect.getmembers(tools_mod, inspect.isfunction)
                if not name.startswith("_")
                and getattr(obj, "__module__", "") == tools_mod.__name__
            ]

        for name, func in candidates:
            try:
                sig = inspect.signature(func)
                params = list(sig.parameters.values())
                accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
                arg_names = [p.name for p in params
                             if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                           inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                           inspect.Parameter.KEYWORD_ONLY)]
            except (TypeError, ValueError):
                accepts_kwargs = True
                arg_names = []

            qualname = f"{func.__module__}.{func.__name__}"

            # Optional: warn/guard on duplicate simple names
            if name in seen_names:
                # You can log a warning here, or switch to using qualname as the key
                pass
            seen_names.add(name)

            discovered.append({
                "name": name,
                "qualname": qualname,
                "args": arg_names,
                "accepts_kwargs": accepts_kwargs,
                "func": func,
            })

    # Keep registry stable across runs
    discovered.sort(key=lambda d: (d["name"], d["qualname"]))
    return discovered


TOOL_REGISTRY = discover_tools()
