from typing import Any, Dict
import json
from langchain_core.messages import AIMessage
from agents.state import AgentState
from agents.utils import enforce_agent_state


def _safe_json(data: Any, limit: int = 4000) -> str:
    """Stable JSON stringifier for tool args/output; truncates to keep prompts lean."""
    try:
        s = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        s = str(data)
    return s if len(s) <= limit else s[:limit] + " …(truncated)"


def _coalesce_ok(state: AgentState, last_tool: Dict[str, Any], tool_output: Any, tool_error: Any) -> bool:
    """
    Decide success without relying on a dedicated state flag.
    Prefer explicit flags set in process_tool_call; otherwise infer from output/error.
    """
    intent_meta: Dict[str, Any] = getattr(state, "intent_meta", {}) or {}
    executed = intent_meta.get("executed_tool") or {}

    if last_tool.get("ok") is True:
        return True
    if executed.get("ok") is True:
        return True

    # Infer success: we have some output and no error string
    return (tool_error in (None, "")) and (tool_output is not None)


@enforce_agent_state
def format_output_llm(state: AgentState, history_manager, llm, get_system_message) -> AgentState:
    """
    Generic, LLM-driven formatter. No special-casing of tool names.
    Converts tool call context into a short user-facing message.
    
    NEW FEATURE: is_pre_formatted flag
    -----------------------------------
    Tools can now bypass LLM formatting by returning:
    {
        "output": "Your pre-formatted message here",
        "is_pre_formatted": True
    }
    
    When is_pre_formatted=True, the output is presented directly without LLM processing.
    This saves API calls and gives tools full control over their output formatting.
    """
    print(f"[DEBUG] format_output_llm -> assistant_message: {getattr(state, 'assistant_message', None)}")
    if getattr(state, "assistant_message", None):
        return state
    msgs = list(getattr(state, "messages", []) or [])

    # Pull context from state
    intent_meta: Dict[str, Any] = getattr(state, "intent_meta", {}) or {}
    executed = intent_meta.get("executed_tool") or {}
    last_tool: Dict[str, Any] = getattr(state, "last_tool", {}) or {}

    tool_name = (
        last_tool.get("name")
        or executed.get("name")
        or getattr(state, "tool_name", None)  # typically None post-call
    )

    # Args: prefer what was actually passed to the tool; fall back to sanitized args
    args = (
        last_tool.get("input")
        or getattr(state, "tool_call_args", {})                       # executable kwargs
        or (intent_meta.get("validated_tool") or {}).get("cleaned_args", {})  # sanitized, pre-call
        or getattr(state, "tool_args", {})                            # builder output
        or {}
    )

    # Output / error: prefer last_tool snapshot; fall back to top-level
    tool_output = last_tool.get("output", None)
    if tool_output is None:
        tool_output = getattr(state, "tool_output", None)

    tool_error = (
        getattr(state, "tool_error", None)
        or last_tool.get("error")
        or last_tool.get("tool_error")
    )

    ok = _coalesce_ok(state, last_tool, tool_output, tool_error)

    # Check if output is pre-formatted (bypass LLM)
    is_pre_formatted = last_tool.get("is_pre_formatted", False)
    
    if is_pre_formatted and ok and tool_output:
        # Use the pre-formatted output directly, no LLM needed
        content = str(tool_output)
        print(f"[DEBUG] format_output_llm -> using pre-formatted output for {tool_name}")
    else:
        # Prepare compact payload for the LLM
        payload = {
            "tool_name": tool_name,
            "ok": ok,
            "args": args,
            "output": tool_output,
            "error": tool_error,
        }

        # Build messages
        system = (
            "You are Ella, a concise shopping assistant for Bulkpot.\n"
            "You will receive a JSON summary of a tool call.\n"
            "Produce ONE short, user-friendly message (< 3 sentences) explaining the result.\n"
            "- If ok=true: summarize the outcome (no internal jargon), focusing on what the user cares about.\n"
            "- If ok=false: briefly explain the failure and suggest the next actionable step.\n"
            "- Do NOT invent details not present in the JSON.\n"
            "- Keep it polite and clear.\n"
        )

        user = (
            "Here is the tool call summary in JSON. Only use information from this JSON in your reply.\n\n"
            f"```json\n{_safe_json(payload)}\n```"
        )

        # Ask the LLM
        content = None
        try:
            resp = llm.invoke([
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ])
            maybe_text = getattr(resp, "content", resp)
            if isinstance(maybe_text, str) and maybe_text.strip():
                content = maybe_text.strip()
        except Exception as e:
            print(f"[WARN] format_output_llm LLM failed: {type(e).__name__}: {e}")

        # Fallback if LLM fails or returns empty
        if not content:
            if ok:
                snippet = _safe_json(tool_output if tool_output is not None else args, limit=300)
                content = f"✅ Success.\n{snippet}"
            else:
                content = f"❌ Error.\n{tool_error or 'Please try again.'}"

    # Append assistant message and update display_output (list[str])
    msgs.append(AIMessage(content=content))
    disp = list(getattr(state, "display_output", []) or [])
    disp.append(content)

    print(
        f"[DEBUG] (format_output_llm) ok={ok} tool_name={tool_name!r} "
        f"flags: last_tool.ok={last_tool.get('ok')!r} "
        f"exec.ok={executed.get('ok')!r} has_output={tool_output is not None} "
        f"has_error={tool_error is not None}"
    )

    return state.model_copy(update={
        "messages": msgs,
        "display_output": disp,
    })
