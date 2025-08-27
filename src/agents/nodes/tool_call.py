# agents/nodes/tool_call.py
import inspect
import time
from typing import Any, Dict, Optional, List

from agents.state import AgentState
from agents.utils import enforce_agent_state


from agents.tools import TOOL_REGISTRY

def _lookup_tool_entry(name: Optional[str]) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    for entry in (TOOL_REGISTRY or []):
        if entry.get("name") == name:
            return entry
    return None


def _prune_kwargs(arg_names: List[str], args: Optional[dict]) -> dict:
    """Keep only parameters that the function actually accepts."""
    args = args or {}
    allowed = set(arg_names or [])
    if not allowed:
        # Fallback to signature if arg list missing
        return args
    return {k: v for k, v in args.items() if k in allowed}


@enforce_agent_state
def process_tool_call(state: AgentState) -> AgentState:
    """
    Execute a discovered tool function directly (no LLM).
    Uses global TOOL_REGISTRY entries: {"name", "args", "func"}.
    Writes result fields to state and clears tool_name/tool_call_args.
    """
    incoming = dict(getattr(state, "tool_call_args", {}) or {})
    tool_name = state.tool_name
    print("[DEBUG] process_tool_call -> tool_call_args:", incoming)
    print(f"[DEBUG] process_tool_call -> tool_name: {tool_name!r}")

    if not tool_name:
        return state  # nothing to do

    # 1) Resolve tool entry from registry
    entry = _lookup_tool_entry(tool_name)
    if not entry:
        intent_meta = dict(getattr(state, "intent_meta", {}) or {})
        intent_meta["executed_tool"] = {"name": tool_name, "ok": False}

        last_tool = {
            "name": tool_name,
            "input": {},
            "output": None,
            "error": f"Tool not found: {tool_name}",
            "ok": False,
            "duration_ms": 0,
        }
        return state.model_copy(update={
            "last_tool": last_tool,
            "tool_name": None,
            "tool_call_args": {},
            "intent_meta": intent_meta,
        })

    func = entry.get("func")
    arg_names = list(entry.get("args") or [])
    if not callable(func):
        err = f"Registry entry for '{tool_name}' is not callable."
        print(f"[ERROR] {err}")
        intent_meta = dict(getattr(state, "intent_meta", {}) or {})
        intent_meta["executed_tool"] = {"name": tool_name, "ok": False}
        last_tool = {
            "name": tool_name,
            "input": {},
            "output": None,
            "error": err,
            "ok": False,
            "duration_ms": 0,
        }
        return state.model_copy(update={
            "last_tool": last_tool,
            "tool_name": None,
            "tool_call_args": {},
            "intent_meta": intent_meta,
        })

    # 2) If args list is empty (or for safety), try introspecting the signature once
    if not arg_names:
        try:
            sig = inspect.signature(func)
            arg_names = [p.name for p in sig.parameters.values()]
        except Exception as e:
            print(f"[WARN] Could not inspect signature for {tool_name}: {e}")
            arg_names = []

    # 3) Prepare args (already sanitized upstream; prune to function params just in case)
    safe_args = _prune_kwargs(arg_names, incoming)
    print(f"[DEBUG] (direct) Invoking {tool_name} with args: {safe_args}")

    # 4) Execute
    started = time.perf_counter()
    output: Any = None
    ok = True
    err_text: Optional[str] = None
    try:
        output = func(**safe_args)
        print(f"[DEBUG] Tool {tool_name} returned: {output}")
    except Exception as e:
        ok = False
        err_text = f"{type(e).__name__}: {e}"
        print(f"[ERROR] Tool {tool_name} failed: {err_text}")
    duration_ms = int((time.perf_counter() - started) * 1000)

    # 5) Persist result
    intent_meta = dict(getattr(state, "intent_meta", {}) or {})
    intent_meta["executed_tool"] = {"name": tool_name, "ok": ok}

    last_tool = {
        "name": tool_name,
        "input": safe_args,
        "output": (output if ok else None),
        "error": err_text,
        "ok": ok,
        "duration_ms": duration_ms,
        # Optional hint for formatters: true if tool returned a ready-to-display string
        "is_pre_formatted": isinstance(output, str) and ok,
    }

    updates: Dict[str, Any] = {
        "last_tool": last_tool,
        "tool_name": None,      # prevent accidental re-run next tick
        "tool_call_args": {},   # clear exec args
        "intent_meta": intent_meta,
    }
    return state.model_copy(update=updates)
