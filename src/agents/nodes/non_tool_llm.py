# agents/nodes/non_tool_llm.py
from pydantic import BaseModel, Field
from typing import List, Optional
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage
from agents.state import AgentState

class NonToolReply(BaseModel):
    reply: str = Field(..., description="WhatsApp-safe, <=2 sentences.")
    handoff_intent: Optional[str] = Field(
        None,
        description="If a next action is obvious (e.g., show categories), set an intent like SHOW_PRODUCT_LIST."
    )
    requested_slots: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(
        default_factory=list,
        description="Up to 3 short suggestions the user can type next (e.g., 'show seafood', 'view cart')."
    )

SYSTEM = """You are Ella, Bulkpot’s WhatsApp assistant.
Do NOT call tools. Do NOT invent products, prices, or availability.
When the user asks to 'show' or 'list' items, DO NOT list items yourself; instead:
- Set handoff_intent="SHOW_PRODUCT_LIST" and offer 2–3 short suggestions the user can type.
For greetings/small talk: greet + nudge to shop with suggestions.
If unclear: ask ONE concise clarifying question.
FAQs: answer only from provided snippets.
Return ONLY the object per schema."""

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM),
    MessagesPlaceholder("history"),
    ("user", "{user_text}"),
])

_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2).with_structured_output(NonToolReply)

def non_tool_handler_llm(state: AgentState) -> AgentState:
    user_text = (state.user_input or "").strip()
    history = state.messages[-6:] if getattr(state, "messages", None) else []

    try:
        result: NonToolReply = (prompt | _llm).invoke({"user_text": user_text, "history": history})

        # Build a reply that includes a gentle CTA with suggestions
        reply = result.reply.strip()
        if result.suggestions:
            # Present as “Try: a · b · c”
            sugg = " · ".join(result.suggestions[:3])
            reply = f"{reply}\nTry: {sugg}"

        new_meta = dict(getattr(state, "intent_meta", {}) or {})
        new_meta.update({
            "tool_name": None,
            "tool_args": {},
            "handoff_intent": result.handoff_intent,   # likely "SHOW_PRODUCT_LIST" on greetings
            "requested_slots": result.requested_slots,
        })

        # Append to messages/display_output so transport renders it
        msgs = list(getattr(state, "messages", []) or [])
        msgs.append(AIMessage(content=reply))
        disp = list(getattr(state, "display_output", []) or [])
        disp.append(reply)

        print(f"[DEBUG] non_tool_handler_llm -> reply: {reply!r}, handoff: {result.handoff_intent}, slots: {result.requested_slots}, suggestions: {result.suggestions}")

        return state.model_copy(update={
            "tool_name": None,
            "tool_args": {},
            "intent_meta": new_meta,
            "assistant_message": reply,
            "messages": msgs,
            "display_output": disp,
        })

    except Exception as e:
        print(f"[ERROR] non_tool_handler_llm exception: {e}")
        fallback = (
            "Hi! I’m Ella 😊 What would you like today—seafood, grains, fruits, or oils?\n"
            "Try: show product list  · view cart"
        )
        msgs = list(getattr(state, "messages", []) or [])
        msgs.append(AIMessage(content=fallback))
        disp = list(getattr(state, "display_output", []) or [])
        disp.append(fallback)

        new_meta = dict(getattr(state, "intent_meta", {}) or {})
        new_meta.update({"tool_name": None, "tool_args": {}, "handoff_intent": "SHOW_PRODUCT_LIST", "requested_slots": []})

        return state.model_copy(update={
            "tool_name": None,
            "tool_args": {},
            "intent_meta": new_meta,
            "assistant_message": fallback,
            "messages": msgs,
            "display_output": disp,
        })
