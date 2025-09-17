"""
Bulkpot Agent - Main entry point for interactive testing.
"""

import argparse
import os
import sys
from langchain_core.messages import AIMessage, HumanMessage
from agents.state import init_customer_and_agent_state, AgentState
from agents.graph import app


def _latest_assistant_text(state: AgentState) -> str | None:
    """Return the most recent assistant message content, if any."""
    msgs = getattr(state, "messages", []) or []

    # LangChain messages (preferred path)
    for m in reversed(msgs):
        try:
            # AIMessage has .type == "ai"
            if getattr(m, "type", None) == "ai":
                return m.content
        except Exception:
            pass

    # Dict-style messages fallback
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") == "assistant":
            return m.get("content")

    return None


def render_response(state: AgentState) -> None:
    """Print the latest assistant reply; fallback to display_output list."""
    text = _latest_assistant_text(state)
    if not text:
        disp = getattr(state, "display_output", None)
        if isinstance(disp, list):
            text = "\n".join(disp)
        else:
            text = disp
    print(f"\nElla: {text or '...'}\n")


def _assistant_message_count(state: AgentState) -> int:
    """Count assistant messages (LangChain or dict) for previous_message_count gating."""
    msgs = getattr(state, "messages", []) or []
    cnt = 0
    for m in msgs:
        try:
            if getattr(m, "type", None) == "ai":
                cnt += 1
            elif isinstance(m, dict) and m.get("role") == "assistant":
                cnt += 1
        except Exception:
            continue
    return cnt


def _configure_debug_output(show_debug: bool) -> None:
    """Wrap stdout/stderr to hide [DEBUG] lines unless show_debug is True."""
    if show_debug:
        return

    class _DebugFilter:
        def __init__(self, underlying):
            self._u = underlying
            self._buf = ""

        def write(self, s):
            # Buffer until newline so we filter full lines
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if not line.startswith("[DEBUG]"):
                    self._u.write(line + "\n")

        def flush(self):
            if self._buf and not self._buf.startswith("[DEBUG]"):
                self._u.write(self._buf)
            self._buf = ""
            try:
                self._u.flush()
            except Exception:
                pass

        # Pass-through common stream attributes
        def __getattr__(self, item):
            return getattr(self._u, item)

    sys.stdout = _DebugFilter(sys.stdout)  # type: ignore
    sys.stderr = _DebugFilter(sys.stderr)  # type: ignore


def main(show_debug: bool = False):
    """Main interactive test mode."""
    # Configure debug output filtering early
    # Also allow env var AGENT_DEBUG=1 to force-enable
    env_debug = os.getenv("AGENT_DEBUG") in {"1", "true", "True", "YES", "yes"}
    _configure_debug_output(show_debug or env_debug)
    print("🤖 Bulkpot Agent - Interactive Test Mode")
    print("=" * 50)
    print("Type 'quit' to exit\n")

    try:
        # Load or create state for a given customer (by phone number here)
        state = init_customer_and_agent_state("+447399368793")
    except ValueError as e:
        print(f"❌ Error initializing agent: {e}")
        print("Please ensure phone_number is provided and valid.")
        return
    except Exception as e:
        print(f"❌ Unexpected error initializing agent: {e}")
        return

    try:
        while True:
            user_input = input("You: ").strip()
            if user_input.lower() in {"quit", "exit", "q"}:
                # Persist final state before exiting
                try:
                    state.save()
                    print("[DEBUG] Final state persisted before exit")
                except Exception as save_error:
                    print(f"[WARNING] Failed to persist final state: {save_error}")
                print("Goodbye! 👋")
                break
            if not user_input:
                continue

            # Prepare state for this turn
            try:
                # Keep raw input for tools, bump turns, and set previous_message_count
                prev_ai = _assistant_message_count(state)
                state = state.model_copy(update={
                    "user_input": user_input,
                    "turns": (state.turns or 0) + 1,
                    "previous_message_count": prev_ai,   # helps gating 'welcome' node
                })
            except ValueError as e:
                print(f"❌ Validation error: {e}")
                continue

            # Invoke the graph
            try:
                result = app.invoke(state)

                # Rehydrate if needed
                if isinstance(result, dict):
                    result = AgentState(**result)

                assert isinstance(result, AgentState), (
                    f"[DEBUG] app.invoke returned {type(result)}, expected AgentState"
                )

                # Render latest assistant reply (or display_output fallback)
                render_response(result)

                # Persist state after successful turn
                try:
                    result.save()
                    print(f"[DEBUG] State persisted for turn {result.turns}")
                except Exception as save_error:
                    print(f"[WARNING] Failed to persist state: {save_error}")

                # Optional: onboarding notice
                if getattr(result, "is_registered", False) and getattr(result, "just_registered", False):
                    print("\n🎉 Registration completed! You can now use the full agent capabilities.")

                # Prepare for next loop
                state = result

            except ValueError as e:
                print(f"❌ Validation error: {e}")
                print("Please check your input and try again.")
            except Exception as e:
                print(f"Error: {e}")
                if "quota" in str(e).lower() or "429" in str(e):
                    print("⚠️  API quota exceeded. Please check your OpenAI billing or try again later.")
                else:
                    print("Please try again.")
    except KeyboardInterrupt:
        print("\nGoodbye! 👋")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulkpot Agent interactive runner")
    parser.add_argument("--debug", action="store_true", help="Show [DEBUG] logs")
    args = parser.parse_args()
    main(show_debug=args.debug)
