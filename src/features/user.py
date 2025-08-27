from typing import List, Optional, Dict, Any
from langchain.tools import tool
from db.user import UserDB
from utils.response import standard_response

# ---- Pure Python Business Logic ----
db = UserDB()

def _filter_user_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    """Extract only the specified user fields to reduce data size."""
    return {
        "user_id": item.get("user_id"),
        "email": item.get("email"),
        "first_name": item.get("first_name"),
        "last_name": item.get("last_name"),
        "created": item.get("created_at"),  # Using created_at as created
        "phone": item.get("phone"),
        "source": item.get("source"),
        "status": item.get("status")
    }


@tool
def get_user(user_id: str) -> Dict[str, Any]:
    """Fetch a user by their unique user_id."""
    item = db.get_user(user_id)
    if item:
        return standard_response(True, data=_filter_user_fields(item))
    else:
        return standard_response(False, error="User not found")

@tool
def is_user_registered(phone: str) -> Dict[str, Any]:
    """Check if a user is registered and active by phone number."""
    try:
        # Search for users with the given phone number
        users = db.search_users(phone)
        
        # Check if any user has the exact phone number and status=active
        for user in users:
            if user.get("phone") == phone and user.get("status") == "active":
                return standard_response(True, data={"is_registered": True, "user_id": user.get("user_id")})
        
        return standard_response(True, data={"is_registered": False})
    except Exception as e:
        return standard_response(False, error=str(e))


# The following functions are state management functions and exposes sensitive data and must not be function call.
# Making them function calls exposes vulnerability and will bloat the LLM's context window.
# If you need to access these as function calls, please create a new function that calls these functions and returns mnimal result
# as in above functions.


def save_user(
    user_id: str,
    first_name: str,
    last_name: str,
    email: str,
    phone: str,
    source: str,
    consent_time: Optional[str] = None,
    status: str = "inprogress",
    is_admin: bool = False,   # <-- Added here with default False
    state_data: Optional[Dict[str, Any]] = None,
    chat_summary: Optional[str] = None,
) -> Dict[str, Any]:
    """Save a user (create if new, update if exists)."""
    try:
        item = db.save_user(
            user_id=user_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            source=source,
            consent_time=consent_time,
            status=status,
            is_admin=is_admin,   # <-- Pass it here
            state_data=state_data,
            chat_summary=chat_summary,
        )
        return standard_response(True, data=item)
    except Exception as e:
        return standard_response(False, error=str(e))

def lookup_user_by_phone(phone: str) -> Dict[str, Any]:
    """Fetch a user by their phone number."""
    item = db.lookup_user_by_phone(phone)
    if item:
        return standard_response(True, data=item)
    else:
        return standard_response(False, error="User not found")

def register_user(phone_number: str, user_profile: dict, status: str = "active") -> Dict[str, Any]:
    """Register a user with the given profile and status."""
    try:
        # Get current timestamp for consent_time only if user_profile is not empty
        consent_time = None
        if user_profile:
            from datetime import datetime, timezone
            consent_time = datetime.now(timezone.utc).isoformat()
        
        # Generate UUID for user_id
        import uuid
        user_id = uuid.uuid4().hex
        
        # Call save_user to register the user
        user_result = save_user(
            user_id=user_id,
            first_name=user_profile.get("first_name", ""),
            last_name=user_profile.get("last_name", ""),
            email=user_profile.get("email", ""),
            phone=phone_number,
            source="whatsapp",
            status=status,
            consent_time=consent_time
        )
        
        return user_result
    except Exception as e:
        return standard_response(False, error=str(e))

def load_state_data(user_id: str) -> Dict[str, Any]:
    """Load state_data for a user by user_id."""
    try:
        state_data = db.load_state_data(user_id)
        if state_data is not None:
            return standard_response(True, data=state_data)
        else:
            return standard_response(False, error="User not found or no state data")
    except Exception as e:
        return standard_response(False, error=str(e))

def save_state_data(state_data: Dict[str, Any]) -> Dict[str, Any]:
    """Save state_data for a user. user_id is extracted from state_data."""
    try:
        success = db.save_state_data(state_data)
        if success:
            return standard_response(True, data={"message": "State data saved successfully"})
        else:
            return standard_response(False, error="Failed to save state data")
    except Exception as e:
        return standard_response(False, error=str(e))


def update_user(agent_state_dict: dict) -> Dict[str, Any]:
    """
    Update an existing user with the provided AgentState dictionary.
    
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
        user_id = agent_state_dict.get("user_id")
        if not user_id:
            return standard_response(False, error="user_id is required in agent_state_dict")
        
        # Extract user profile information
        user_profile = agent_state_dict.get("user_profile", {})
        
        # Map AgentState fields to database fields
        update_fields = {
            "user_id": user_id,
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
            "chat_summary": f"User {'registered' if agent_state_dict.get('just_registered') else 'active'} - {agent_state_dict.get('turns', 0)} turns"
        }
        
        # Call save_user with the mapped fields
        # DynamoDB will preserve existing values for fields not specified
        user_result = save_user(**update_fields)
        
        return user_result
    except Exception as e:
        return standard_response(False, error=str(e))







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