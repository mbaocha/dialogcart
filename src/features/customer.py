from typing import List, Optional, Dict, Any
from langchain.tools import tool
from db.customers import CustomerDB
from utils.response import standard_response

# ---- Pure Python Business Logic ----
db = CustomerDB()

def _filter_customer_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    """Extract only the specified customer fields to reduce data size."""
    return {
        "customer_id": item.get("customer_id"),
        "email": item.get("email"),
        "first_name": item.get("first_name"),
        "last_name": item.get("last_name"),
        "created": item.get("created_at"),  # Using created_at as created
        "phone": item.get("phone"),
        "source": item.get("source"),
        "status": item.get("status")
    }


@tool
def get_customer(tenant_id: str, customer_id: str) -> Dict[str, Any]:
    """Fetch a customer by tenant_id and customer_id."""
    item = db.get_customer(tenant_id, customer_id)
    if item:
        return standard_response(True, data=_filter_customer_fields(item))
    else:
        return standard_response(False, error="Customer not found")

@tool
def is_customer_registered(tenant_id: str, phone: str) -> Dict[str, Any]:
    """Check if a customer is registered and active by phone number for a tenant."""
    try:
        cust = db.lookup_by_phone(tenant_id, phone)
        if cust and cust.get("status") == "active":
            return standard_response(True, data={"is_registered": True, "customer_id": cust.get("customer_id")})
        return standard_response(True, data={"is_registered": False})
    except Exception as e:
        return standard_response(False, error=str(e))


# The following functions are state management functions and exposes sensitive data and must not be function call.
# Making them function calls exposes vulnerability and will bloat the LLM's context window.
# If you need to access these as function calls, please create a new function that calls these functions and returns mnimal result
# as in above functions.


def save_customer(
    tenant_id: str,
    first_name: str,
    last_name: str,
    source: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    consent_time: Optional[str] = None,
    status: str = "inprogress",
    state_data: Optional[Dict[str, Any]] = None,
    chat_summary: Optional[str] = None,
    source_customer_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Save a customer (create new record)."""
    try:
        item = db.save_customer(
            tenant_id=tenant_id,
            first_name=first_name,
            last_name=last_name,
            source=source,
            email=email,
            phone=phone,
            consent_time=consent_time,
            status=status,
            state_data=state_data,
            chat_summary=chat_summary,
            source_customer_id=source_customer_id,
            tags=tags,
            notes=notes,
        )
        return standard_response(True, data=item)
    except Exception as e:
        return standard_response(False, error=str(e))

def lookup_customer_by_phone(tenant_id: str, phone: str) -> Dict[str, Any]:
    """Fetch a customer by phone number for a tenant."""
    item = db.lookup_by_phone(tenant_id, phone)
    if item:
        return standard_response(True, data=item)
    else:
        return standard_response(False, error="Customer not found")

def register_customer(tenant_id: str, phone_number: str, customer_profile: dict, status: str = "active") -> Dict[str, Any]:
    """Register a customer with the given profile and status."""
    try:
        # Get current timestamp for consent_time only if profile is not empty
        consent_time = None
        if customer_profile:
            from datetime import datetime, timezone
            consent_time = datetime.now(timezone.utc).isoformat()
        
        result = save_customer(
            tenant_id=tenant_id,
            first_name=customer_profile.get("first_name", ""),
            last_name=customer_profile.get("last_name", ""),
            email=customer_profile.get("email", ""),
            phone=phone_number,
            source="whatsapp",
            status=status,
            consent_time=consent_time,
        )
        return result
    except Exception as e:
        return standard_response(False, error=str(e))

def load_state_data(tenant_id: str, customer_id: str) -> Dict[str, Any]:
    """Load state_data for a customer by tenant_id and customer_id."""
    try:
        cust = db.get_customer(tenant_id, customer_id)
        state_data = cust.get("state_data") if cust else None
        if state_data is not None:
            return standard_response(True, data=state_data)
        else:
            return standard_response(False, error="Customer not found or no state data")
    except Exception as e:
        return standard_response(False, error=str(e))

def save_state_data_scoped(tenant_id: str, customer_id: str, state_data: Dict[str, Any]) -> Dict[str, Any]:
    """Save state_data for a customer."""
    try:
        success = db.update_state_data(tenant_id, customer_id, state_data)
        if success:
            return standard_response(True, data={"message": "State data saved successfully"})
        else:
            return standard_response(False, error="Failed to save state data")
    except Exception as e:
        return standard_response(False, error=str(e))


def update_customer(agent_state_dict: dict) -> Dict[str, Any]:
    """
    Update or create a customer using an AgentState-like dictionary.
    
    Args:
        agent_state_dict (dict): Dictionary containing AgentState fields:
            - user_id (str): Required - The unique identifier of the user to update
            - phone_number (str): User's phone number
            - user_profile (dict): User profile information (name, email, etc.)
            - is_registered (bool): Whether user has completed registration
            - messages (list): Conversation messages
            - all_time_history (list): Complete conversation history
            - turns (int): Number of conversation turns
            - display_output (list): Debug/display information
            - previous_message_count (int): Previous message count
            - is_disabled (bool): Whether user is disabled
            - user_input (str): Current user input
            - just_registered (bool): Whether user just completed registration
            
    Returns:
        Dict[str, Any]: Standard response with success/error status and data
    """
    try:
        # Extract user_id from agent_state_dict
        tenant_id = agent_state_dict.get("tenant_id")
        customer_id = agent_state_dict.get("customer_id")
        if not tenant_id or not customer_id:
            return standard_response(False, error="tenant_id and customer_id are required in agent_state_dict")
        
        # Extract user profile information
        user_profile = agent_state_dict.get("user_profile", {})
        
        # Map AgentState fields to database fields
        update_fields = {
            "first_name": user_profile.get("first_name", ""),
            "last_name": user_profile.get("last_name", ""),
            "email": user_profile.get("email", ""),
            "phone": agent_state_dict.get("phone_number", ""),  # Add phone from phone_number
            "source": "whatsapp",  # Add source
            "status": "active" if agent_state_dict.get("is_registered", False) else "inprogress",
            "state_data": {
                "messages": agent_state_dict.get("messages", []),
                "all_time_history": agent_state_dict.get("all_time_history", []),
                "user_input": agent_state_dict.get("user_input", ""),
                "is_registered": agent_state_dict.get("is_registered", False),
                "user_profile": agent_state_dict.get("user_profile", {}),
                "just_registered": agent_state_dict.get("just_registered", False),
                "turns": agent_state_dict.get("turns", 0),
                "display_output": agent_state_dict.get("display_output", []),
                "previous_message_count": agent_state_dict.get("previous_message_count", 0),
                "is_disabled": agent_state_dict.get("is_disabled", False)
            },
            "chat_summary": f"Customer {'registered' if agent_state_dict.get('just_registered') else 'active'} - {agent_state_dict.get('turns', 0)} turns"
        }
        
        result = save_customer(tenant_id=tenant_id, **update_fields)
        return result
    except Exception as e:
        return standard_response(False, error=str(e))

# ---- Backward-compatibility wrappers (legacy 'user' API) ----
import os

def _default_tenant_id() -> str:
    return os.getenv("TENANT_ID", "demo-tenant-001")

def update_user(agent_state_dict: dict) -> Dict[str, Any]:
    tenant_id = agent_state_dict.get("tenant_id") or _default_tenant_id()
    # map user_id -> customer_id if needed
    if "customer_id" not in agent_state_dict and agent_state_dict.get("user_id"):
        agent_state_dict = {**agent_state_dict, "tenant_id": tenant_id, "customer_id": agent_state_dict["user_id"]}
    return update_customer(agent_state_dict)

def get_user(user_id: str) -> Dict[str, Any]:
    res = get_customer(_default_tenant_id(), user_id)
    # keep field names backward compatible
    if res.get("success") and isinstance(res.get("data"), dict):
        d = dict(res["data"])
        if "customer_id" in d and "user_id" not in d:
            d["user_id"] = d["customer_id"]
        res["data"] = d
    return res

def lookup_user_by_phone(phone: str) -> Dict[str, Any]:
    return lookup_customer_by_phone(_default_tenant_id(), phone)

def register_user(phone_number: str, user_profile: dict, status: str = "active") -> Dict[str, Any]:
    return register_customer(_default_tenant_id(), phone_number, user_profile, status)

def save_state_data(state_data: Dict[str, Any]) -> Dict[str, Any]:
    tenant_id = state_data.get("tenant_id") or _default_tenant_id()
    customer_id = state_data.get("customer_id") or state_data.get("user_id")
    if not customer_id:
        return standard_response(False, error="customer_id missing for save_state_data")
    return save_state_data_scoped(tenant_id, customer_id, state_data)







#not sure if this is needed
def list_users(status: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    """List all users, optionally filter by status."""
    items = db.list_users(status=status, limit=limit)
    if items:
        filtered_items = [_filter_user_fields(item) for item in items]
        return standard_response(True, data=filtered_items)
    else:
        return standard_response(True, data=[])

#not sure if this is needed
def search_users(query: str) -> Dict[str, Any]:
    """Search users by first name, last name, email, or phone (case-insensitive)."""
    try:
        matches = db.search_users(query)
        if matches:
            filtered_matches = [_filter_user_fields(item) for item in matches]
            return standard_response(True, data=filtered_matches)
        else:
            return standard_response(True, data=[])
    except Exception as e:
        return standard_response(False, error=str(e))