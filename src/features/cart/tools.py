from typing import Optional, Dict, Any
from langchain.tools import tool
from utils.response import standard_response
from features.cart.service import CartService

# Initialize the service
cart_service = CartService()


def get_cart_formatted(customer_id: str) -> str:
    """
    Get cart contents formatted in a beautiful bullet-point style.

    MANDATORY: Call this when the user asks to view cart, show cart, see cart contents,
    or similar cart viewing operations. This provides a pre-formatted, user-friendly display.
    """
    data = cart_service.get_cart_formatted(user_id=customer_id)
    
    if data.get("success"):
        cart_text = data["data"]["text"]
        intro = "Here's what's currently in your shopping cart at Bulkpot:"
        outro = "Ready to place your order or need to make any changes? Just let me know! 😊"
        return f"{intro}\n\n{cart_text}\n\n{outro}"
    else:
        return f"Error: {data.get('error', 'Unknown error')}"


def add_item_to_cart(
    catalog_id: str,
    customer_id: Optional[str] = None,
    quantity: Optional[float] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Add an item to the user's cart.

    Use this when the user says things like:
    - "add 2 boxes of frozen chicken"
    - "put 5 kg of onion in my cart"
    - "add plantain"

    You must provide a catalog_id to add items to the cart.
    If the user didn't give a quantity, call me with quantity=None — I'll return
    'missing_quantity' so you can ask a follow-up.
    """
    if not customer_id:
        return standard_response(False, error="missing_customer_id")
    if not catalog_id:
        return standard_response(False, error="missing_catalog_id")
    if quantity is None:
        return standard_response(False, error="missing_quantity")

    return cart_service.add_item_to_cart(
        user_id=customer_id,
        quantity=quantity,
        catalog_id=catalog_id,
        tenant_id=tenant_id,
    )


def remove_item_from_cart(customer_id: str, catalog_id: str, tenant_id: str | None = None, quantity: int | None = None) -> Dict[str, Any]:
    """Remove a specific product from a user's shopping cart.
       Resolves the correct variant automatically from the user's cart.
       
       Args:
           customer_id: The customer's ID
           catalog_id: The catalog ID to remove
           tenant_id: Optional tenant ID
           quantity: Optional quantity to reduce by. If None, removes the entire item.
    """
    return cart_service.remove_item(user_id=customer_id, catalog_id=catalog_id, tenant_id=tenant_id, quantity=quantity)


def update_cart_quantity(customer_id: str, catalog_id: str, quantity: float, update_op: str = "set") -> Dict[str, Any]:
    """Update the quantity of a product in the user's cart.
       Use if the user asks to: update quantity, change quantity, modify quantity, 
       or similar quantity modification operations. 
       User does not need to provide user ID."""
    print(f"[DEBUG] update_cart_quantity -> customer_id: {customer_id}, catalog_id: {catalog_id}, quantity: {quantity}, update_op: {update_op}")
    return cart_service.update_cart_quantity(user_id=customer_id, catalog_id=catalog_id, quantity=quantity, update_op=update_op)


def clear_cart(customer_id: str) -> Dict[str, Any]:
    """Remove all items from a user's shopping cart. 
       Use if the user asks to: clear cart, empty cart, remove all items, 
       or similar bulk removal operations. 
       User does not need to provide user ID."""
    result = cart_service.clear_cart(user_id=customer_id)
    
    # Example of using is_pre_formatted to bypass LLM formatting
    if result.get("success"):
        return {
            "output": "✅ Cart cleared. Reply “restore cart” within 30 min to restore.",
            "is_pre_formatted": True
        }
    else:
        return {
            "output": f"❌ Failed to clear cart: {result.get('error', 'Unknown error')}",
            "is_pre_formatted": True
        }


def restore_cart(customer_id: str) -> Dict[str, Any]:
    """Restore cart from the latest valid backup by merging with current cart.
       Use if the user asks to: restore cart, undo clear cart, get back my items,
       or similar restoration operations. User does not need to provide user ID."""
    result = cart_service.restore_cart(user_id=customer_id)
    
    if result.get("success"):
        data = result["data"]
        if data.get("restored"):
            # Format a nice response based on what was restored
            added_count = data.get("added_from_backup", 0)
            increased_count = data.get("increased_existing", 0)
            
            if added_count > 0 and increased_count > 0:
                message = f"✅ Cart restored successfully! Added {added_count} new items and increased quantities for {increased_count} existing items."
            elif added_count > 0:
                message = f"✅ Cart restored successfully! Added {added_count} items from your previous cart."
            elif increased_count > 0:
                message = f"✅ Cart restored successfully! Increased quantities for {increased_count} existing items."
            else:
                message = "✅ Cart restored successfully! Your previous items have been merged with your current cart."
            
            return {
                "output": message,
                "is_pre_formatted": True
            }
        else:
            return {
                "output": f"❌ Failed to restore cart: {data.get('error', 'Unknown error')}",
                "is_pre_formatted": True
            }
    else:
        return {
            "output": f"❌ Failed to restore cart: {result.get('error', 'Unknown error')}",
            "is_pre_formatted": True
        }
