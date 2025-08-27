from typing import List, Optional, Dict, Any
from langchain.tools import tool
from fastapi import FastAPI, APIRouter, HTTPException
from db.order import OrderDB
from db.address import AddressDB
from utils.response import standard_response

# ---- Business Logic Functions ----
order_db = OrderDB()
address_db = AddressDB()

def create_order(
    user_id: str,
    items: List[Dict[str, Any]],
    total_amount: float,
    status: str = "pending",
    address_id: Optional[str] = None,
    address: Optional[Any] = None,
    payment_status: Optional[str] = None,   # <-- changed
    last_payment_id: Optional[str] = None,  # <-- changed
    notes: Optional[str] = None,
    delivery_method: Optional[str] = None,
    tracking_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a new order.

    - Pass address_id (from user_addresses) OR a full address map.
    - If address_id is provided, address is snapshot into order record.
    """
    try:
        order_address = None
        if address_id:
            addr = address_db.get_address(address_id)
            if not addr:
                return standard_response(False, error="Address not found")
            order_address = {k: v for k, v in addr.items() if k not in ("address_id", "user_id")}
        elif address:
            order_address = address

        order = order_db.create_order(
            user_id=user_id,
            items=items,
            total_amount=total_amount,
            status=status,
            address=order_address,
            payment_status=payment_status,        # <-- changed
            last_payment_id=last_payment_id,      # <-- changed
            notes=notes,
            delivery_method=delivery_method,
            tracking_info=tracking_info,
        )
        return standard_response(True, data=order)
    except Exception as e:
        return standard_response(False, error=str(e))

def get_order(order_id: str) -> Dict[str, Any]:
    item = order_db.get_order(order_id)
    if item:
        return standard_response(True, data=item)
    else:
        return standard_response(False, error="Order not found")

def list_orders(user_id: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    items = order_db.list_orders(user_id=user_id, limit=limit)
    return standard_response(True, data=items)

def update_order_status(order_id: str, status: str) -> Dict[str, Any]:
    try:
        success = order_db.update_status(order_id, status)
        if success:
            return standard_response(True, data={"order_id": order_id, "status": status})
        else:
            return standard_response(False, error="Order not found or not updated")
    except Exception as e:
        return standard_response(False, error=str(e))

# ---- LangChain Tools ----

@tool
def create_order_tool(**kwargs):
    """Create a new order with items, total amount, and optional address and payment details."""
    return create_order(**kwargs)

@tool
def get_order_tool(**kwargs):
    """Retrieve order details by order ID."""
    return get_order(**kwargs)

@tool
def list_orders_tool(**kwargs):
    """List orders optionally filtered by user ID with pagination support."""
    return list_orders(**kwargs)

# ---- FastAPI Endpoint ----

app = FastAPI()
router = APIRouter()

@router.post("/orders/{order_id}/status")
def update_order_status_endpoint(order_id: str, status: str):
    result = update_order_status(order_id, status)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result

app.include_router(router)
