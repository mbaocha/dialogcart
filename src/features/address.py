from typing import Dict, Any
from langchain.tools import tool
from db.address import AddressDB
from utils.response import standard_response

db = AddressDB()

def create_address(
    user_id: str,
    label: str,
    address_line1: str,
    city: str,
    state: str,
    country: str,
    postal_code: str = None,
    address_line2: str = None,
    lat: float = None,
    lon: float = None,
    is_default: bool = False,
) -> Dict[str, Any]:
    """Create a new address record for a user with label, address details, and optional coordinates.
       Use if the user wants to: add a new address, save delivery address, create shipping address,
       or similar address creation operations. User does not need to provide user ID."""
    
    if not user_id or not label or not address_line1 or not city or not state or not country:
        return standard_response(False, error="user_id, label, address_line1, city, state, and country are required")
    
    try:
        print(f"[DEBUG] create_address called with user_id={user_id}, label={label}, city={city}, state={state}")
        item = db.create_address(
            user_id=user_id,
            label=label,
            address_line1=address_line1,
            city=city,
            state=state,
            country=country,
            postal_code=postal_code,
            address_line2=address_line2,
            lat=lat,
            lon=lon,
            is_default=is_default,
        )
        return standard_response(True, data=item)
    except Exception as e:
        return standard_response(False, error=str(e))

@tool
def get_address(address_id: str) -> Dict[str, Any]:
    """Retrieve address details by address ID.
       Use if the user wants to: view address details, get address information,
       or similar address retrieval operations."""

    if not address_id:
        return standard_response(False, error="address_id is required")

    try:
        print(f"[DEBUG] get_address called with address_id={address_id}")
        item = db.get_address(address_id)
        if item:
            return standard_response(True, data=item)
        else:
            return standard_response(False, error="Address not found")
    except Exception as e:
        return standard_response(False, error=str(e))

@tool
def list_addresses(user_id: str) -> Dict[str, Any]:
    """List all addresses for a specific user.
       Use if the user wants to: view all addresses, see saved addresses,
       or similar address listing operations. User does not need to provide user ID."""

    if not user_id:
        return standard_response(False, error="user_id is required")

    try:
        print(f"[DEBUG] list_addresses called with user_id={user_id}")
        items = db.list_addresses(user_id)
        return standard_response(True, data=items)
    except Exception as e:
        return standard_response(False, error=str(e))

@tool
def update_address(address_id: str, **kwargs) -> Dict[str, Any]:
    """Update an existing address record with new information.
       Use if the user wants to: edit address, modify address details,
       or similar address update operations."""

    if not address_id:
        return standard_response(False, error="address_id is required")

    try:
        print(f"[DEBUG] update_address called with address_id={address_id}, kwargs={kwargs}")
        success = db.update_address(address_id, **kwargs)
        if success:
            return standard_response(True)
        else:
            return standard_response(False, error="Address not updated")
    except Exception as e:
        return standard_response(False, error=str(e))

@tool
def delete_address(address_id: str) -> Dict[str, Any]:
    """Delete an address record by address ID.
       Use if the user wants to: remove address, delete saved address,
       or similar address deletion operations."""
    
    if not address_id:
        return standard_response(False, error="address_id is required")
    
    try:
        print(f"[DEBUG] delete_address called with address_id={address_id}")
        success = db.delete_address(address_id)
        if success:
            return standard_response(True)
        else:
            return standard_response(False, error="Address not deleted")
    except Exception as e:
        return standard_response(False, error=str(e))

@tool
def set_default_address(user_id: str, address_id: str) -> Dict[str, Any]:
    """Set a specific address as the default address for a user.
       Use if the user wants to: set default address, make address primary,
       or similar default address operations. User does not need to provide user ID."""

    if not user_id or not address_id:
        return standard_response(False, error="user_id and address_id are required")

    try:
        print(f"[DEBUG] set_default_address called with user_id={user_id}, address_id={address_id}")
        success = db.set_default(user_id, address_id)
        if success:
            return standard_response(True)
        else:
            return standard_response(False, error="Failed to set default address")
    except Exception as e:
        return standard_response(False, error=str(e))

@tool
def get_default_address(user_id: str) -> Dict[str, Any]:
    """Get the default address for a specific user.
       Use if the user wants to: get default address, view primary address,
       or similar default address retrieval operations. User does not need to provide user ID."""

    if not user_id:
        return standard_response(False, error="user_id is required")

    try:
        print(f"[DEBUG] get_default_address called with user_id={user_id}")
        addresses = db.list_addresses(user_id)
        if not addresses:
            return standard_response(False, error="No address found for user")
        for addr in addresses:
            if addr.get("is_default"):
                return standard_response(True, data=addr)
        return standard_response(False, error="No default address set")
    except Exception as e:
        return standard_response(False, error=str(e))

# ---- LangChain Tool Wrappers ----

@tool
def create_address_tool(**kwargs):
    """Create a new address record for a user with label, address details, and optional coordinates.
       Use if the user wants to: add a new address, save delivery address, create shipping address,
       or similar address creation operations. User does not need to provide user ID."""
    return create_address(**kwargs)

@tool
def get_address_tool(**kwargs):
    """Retrieve address details by address ID.
       Use if the user wants to: view address details, get address information,
       or similar address retrieval operations."""
    return get_address(**kwargs)

@tool
def list_addresses_tool(**kwargs):
    """List all addresses for a specific user.
       Use if the user wants to: view all addresses, see saved addresses,
       or similar address listing operations. User does not need to provide user ID."""
    return list_addresses(**kwargs)

@tool
def update_address_tool(**kwargs):
    """Update an existing address record with new information.
       Use if the user wants to: edit address, modify address details,
       or similar address update operations."""
    return update_address(**kwargs)

@tool
def delete_address_tool(**kwargs):
    """Delete an address record by address ID.
       Use if the user wants to: remove address, delete saved address,
       or similar address deletion operations."""
    return delete_address(**kwargs)

@tool
def set_default_address_tool(**kwargs):
    """Set a specific address as the default address for a user.
       Use if the user wants to: set default address, make address primary,
       or similar default address operations. User does not need to provide user ID."""
    return set_default_address(**kwargs)

@tool
def get_default_address_tool(**kwargs):
    """Get the default address for a specific user.
       Use if the user wants to: get default address, view primary address,
       or similar default address retrieval operations. User does not need to provide user ID."""
    return get_default_address(**kwargs)
