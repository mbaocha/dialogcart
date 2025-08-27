from typing import Sequence, Dict, Any, List
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, SystemMessage
from typing import Optional


# --- Message conversion utilities ---

def dict_to_message(msg):
    if isinstance(msg, (AIMessage, HumanMessage, ToolMessage, SystemMessage)):
        return msg
    t = msg.get("type") or msg.get("__type__")
    
    # Handle messages with role field but no type field
    if t is None and "role" in msg:
        role = msg["role"]
        if role == "assistant":
            return AIMessage(**msg)
        elif role == "user":
            return HumanMessage(**msg)
        elif role == "tool":
            return ToolMessage(**msg)
        elif role == "system":
            return SystemMessage(**msg)
    
    if t == "ai":
        return AIMessage(**msg)
    if t == "human":
        return HumanMessage(**msg)
    if t == "tool":
        return ToolMessage(**msg)
    if t == "system":
        return SystemMessage(**msg)
    raise ValueError(f"Unknown message type: {t} ({msg})")

def message_to_dict(msg):
    if isinstance(msg, AIMessage):
        # Only save minimal fields
        return {
            "type": "ai",
            "content": msg.content,
            "tool_calls": getattr(msg, "tool_calls", []),
        }
    elif isinstance(msg, HumanMessage):
        return {
            "type": "human",
            "content": msg.content,
        }
    elif isinstance(msg, ToolMessage):
        return {
            "type": "tool",
            "content": msg.content,
            "tool_call_id": getattr(msg, "tool_call_id", None),
        }
    elif isinstance(msg, SystemMessage):
        return {
            "type": "system",
            "content": msg.content,
        }
    else:
        # Already a dict or unknown: return as is
        return msg

# --- Tool call repair (operates on objects) ---

def repair_broken_tool_calls(messages):
    from langchain_core.messages import AIMessage, ToolMessage

    def is_aimessage(msg):
        return isinstance(msg, AIMessage) and (
            getattr(msg, "tool_calls", None) or (
                hasattr(msg, "additional_kwargs") and msg.additional_kwargs.get("tool_calls")
            )
        )

    def get_tool_calls(msg):
        tc = getattr(msg, "tool_calls", None)
        if tc is None and hasattr(msg, "additional_kwargs"):
            tc = msg.additional_kwargs.get("tool_calls")
        return tc or []

    def get_tool_call_id(tc):
        return getattr(tc, "id", None) if not isinstance(tc, dict) else tc.get("id")

    def is_toolmessage(msg):
        return isinstance(msg, ToolMessage)

    def get_tool_call_id_from_toolmessage(msg):
        return getattr(msg, "tool_call_id", None)

    i = 0
    while i < len(messages):
        msg = messages[i]
        if is_aimessage(msg):
            tool_calls = get_tool_calls(msg)
            tool_call_ids = [get_tool_call_id(tc) for tc in tool_calls]
            expected = len(tool_call_ids)
            found = 0
            j = i + 1
            while found < expected and j < len(messages):
                next_msg = messages[j]
                if is_toolmessage(next_msg) and get_tool_call_id_from_toolmessage(next_msg) == tool_call_ids[found]:
                    found += 1
                    j += 1
                else:
                    break
            # Insert missing ToolMessages
            for k in range(found, expected):
                fake_tool_msg = ToolMessage(
                    tool_call_id=tool_call_ids[k],
                    content="[REPAIRED: ToolMessage was missing, inserted automatically]"
                )
                messages.insert(i + 1 + found, fake_tool_msg)
                found += 1
            i = i + 1 + expected
        else:
            i += 1

# --- AgentState definition ---

class AgentState(BaseModel):
    user_id: str  # DynamoDB PK
    phone_number: str
    messages: Sequence[Any] = Field(default_factory=list)
    all_time_history: Sequence[Any] = Field(default_factory=list)
    chat_summaries: List[str] = Field(default_factory=list)

    user_input: str = ""
    is_registered: bool = False
    user_profile: Dict[str, Any] = Field(default_factory=dict)
    just_registered: bool = False
    turns: int = 0
    display_output: List[str] = Field(default_factory=list)
    previous_message_count: int = 0
    is_disabled: bool = False

    tool_name: Optional[str] = None
    tool_args: Dict[str, Any] = Field(default_factory=dict)
    tool_call_args: Dict[str, Any] = Field(default_factory=dict)

    last_tool: Dict[str, Any] = Field(default_factory=dict)


    # --- intent classification fields ---
    
    # --- new fields for injector/name→id flow ---
    #products_index: Optional[Dict[str, str]] = None                  # index -> product_id mapping
    #cart_index: Optional[Dict[str, str]] = None
    products: Dict[str, Any] = Field(default_factory=dict)
    intent_meta: Dict[str, Any] = Field(default_factory=dict)
    force_llm_path: bool = False
    
    # --- disambiguation fields ---
    awaiting_user_clarification: bool = False
    pending_disambiguation: Optional[Dict[str, Any]] = None
    
    # --- metadata for LLM context ---
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_state_data(cls, state_data: dict) -> "AgentState":
        if "user_id" not in state_data or "phone_number" not in state_data:
            raise ValueError("user_id and phone_number must be present in state_data")
        data = dict(state_data)
        # convert to objects
        data["messages"] = [dict_to_message(m) for m in data.get("messages", [])]
        data["all_time_history"] = [dict_to_message(m) for m in data.get("all_time_history", [])]
        return cls(**data)

    def save(self):
        if not self.user_id:
            raise ValueError("user_id required to save state!")
        
        # Create a temporary LLMHistoryManager to trim messages
        from agents.llm_history import LLMHistoryManager
        from agents.config import SYSTEM_MESSAGE
        
        # Create a temporary history manager for trimming
        temp_history_manager = LLMHistoryManager(SYSTEM_MESSAGE)
        
        # Trim only messages for storage (reduce data size)
        # Don't trim all_time_history as it has its own internal trimming mechanism
        trimmed_messages = temp_history_manager.smart_trim(self.messages)
        
        state_data = self.dict(exclude={"phone_number"})
        # Save trimmed messages and full all_time_history
        state_data["messages"] = [message_to_dict(m) for m in trimmed_messages]
        state_data["all_time_history"] = [message_to_dict(m) for m in self.all_time_history]
        
        from features.user import save_state_data
        result = save_state_data(state_data)
        if result.get("success"):
            print(f"[DEBUG] State saved for user {self.user_id}")
        else:
            print(f"[DEBUG] Failed to save state for user {self.user_id}: {result.get('error')}")

    class Config:
        arbitrary_types_allowed = True

# --- State loader ---

def init_user_and_agent_state(phone_number: str) -> AgentState:
    from features.user import lookup_user_by_phone, register_user

    result = lookup_user_by_phone(phone_number)
    if not result.get("success"):
        result = register_user(phone_number=phone_number, user_profile={}, status="inprogress")
        if not result.get("success"):
            raise Exception("User registration failed")
    data = result["data"]

    if 'state_data' not in data:
        return AgentState(
            user_id=data["user_id"],
            phone_number=phone_number
        )
    data["user_id"] = data["user_id"]
    data["phone_number"] = phone_number

    # Extract user profile
    if "user_profile" not in data or data["user_profile"] is None:
        data["user_profile"] = {
            "first_name": data.get("first_name"),
            "last_name": data.get("last_name"),
            "email": data.get("email")
        }

    state_data = data.get("state_data", {})
    agent_state_data = {
        "user_id": data["user_id"],
        "phone_number": data["phone_number"],
        "user_profile": data["user_profile"],
        "is_registered": data.get("status") == "active",
        "just_registered": False,
        "messages": [dict_to_message(m) for m in state_data.get("messages", [])],
        "all_time_history": [dict_to_message(m) for m in state_data.get("all_time_history", [])],
        "chat_summaries": state_data.get("chat_summaries", []),
        "user_input": state_data.get("user_input", ""),
        "turns": state_data.get("turns", 0),
        "display_output": state_data.get("display_output", []),
        "previous_message_count": state_data.get("previous_message_count", 0),
        "is_disabled": state_data.get("is_disabled", False)
    }
    # PATCH: Repair all tool call blocks on load
    repair_broken_tool_calls(agent_state_data["messages"])
    repair_broken_tool_calls(agent_state_data["all_time_history"])

    # Print message stats
    import json
    # Convert LangChain message objects to dictionaries for JSON serialization
    messages_dicts = [message_to_dict(msg) for msg in agent_state_data["messages"]]
    messages_json = json.dumps(messages_dicts, indent=2)
    total_chars = len(messages_json)
    estimated_tokens = total_chars // 4  # Rough estimate: 1 token ≈ 4 characters
    message_count = len(agent_state_data["messages"])
    print(f"[DEBUG] State Load - Messages: {message_count} messages, {estimated_tokens} tokens, Total chars: {total_chars}")

    # Print all_time_history stats
    history_dicts = [message_to_dict(msg) for msg in agent_state_data["all_time_history"]]
    history_json = json.dumps(history_dicts, indent=2)
    total_chars_history = len(history_json)
    estimated_tokens_history = total_chars_history // 4  # Rough estimate: 1 token ≈ 4 characters
    message_count_history = len(agent_state_data["all_time_history"])
    print(f"[DEBUG] State Load - All Time History: {message_count_history} messages, {estimated_tokens_history} tokens, Total chars: {total_chars_history}")

    return AgentState(**agent_state_data)
