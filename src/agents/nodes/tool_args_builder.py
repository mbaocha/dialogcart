# agents/nodes/tool_args_builder.py
from __future__ import annotations
from typing import Any, Dict, Optional

from agents.state import AgentState
from agents.utils import enforce_agent_state
from features.catalog.service import CatalogService
from agents.tools import INTENT_TO_TOOL

# keys we allow straight through from entities
_PASSTHROUGH = {
    "product", "quantity", "unit", "delta",
    "category", "limit_categories", "examples_per_category", "update_op"
}

def _first_entity(entities: Any) -> Dict[str, Any]:
    if not entities:
        return {}
    if isinstance(entities, dict):
        return dict(entities)
    if isinstance(entities, list) and entities and isinstance(entities[0], dict):
        return dict(entities[0])
    return {}

def _light_normalize(args: Dict[str, Any]) -> None:
    # rename product -> catalog_name for tools
    if "product" in args and "catalog_name" not in args:
        args["catalog_name"] = args.pop("product")


def _build_tool_args(state: AgentState) -> Dict[str, Any]:
    im = getattr(state, "intent_meta", {}) or {}
    entities = _first_entity(im.get("entities"))

    # start with customer_id and tenant_id always
    args: Dict[str, Any] = {"customer_id": state.customer_id, "tenant_id": getattr(state, "tenant_id", None)}

    # pass through only safe keys from entities (validator will enforce correctness)
    for k, v in entities.items():
        if k in _PASSTHROUGH and v is not None and v != "":
            args[k] = v

    _light_normalize(args)
    return args

@enforce_agent_state
def tool_args_builder(state: AgentState) -> AgentState:
    im = getattr(state, "intent_meta", {}) or {}
    intent = (im.get("intent") or "NONE").upper()

    tool_name: Optional[str] = INTENT_TO_TOOL.get(intent)
    tool_args: Dict[str, Any] = _build_tool_args(state) if tool_name else {}

    # augment meta for traceability
    meta = dict(im)
    meta.update({"tool_name": tool_name, "tool_args": tool_args})

    print(f"[DEBUG] tool_args_builder -> tool_name: {tool_name}, tool_args: {tool_args}")

    # ensure products cached for validator/tools (id -> name)
    tenant_id = getattr(state, "tenant_id", None)
    products = state.products or (CatalogService().list_catalog_flat(tenant_id) if tenant_id else {})

    return state.model_copy(update={
        "tool_name": tool_name,
        "tool_args": tool_args,
        "intent_meta": meta,
        "products": products,
    })
