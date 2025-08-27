from typing import List, Optional, Dict, Any
from langchain.tools import tool
from fastapi import FastAPI, APIRouter, HTTPException, Request
from db.payment import PaymentDB
from utils.response import standard_response
import stripe
import os


stripe.api_key = os.getenv("STRIPE_SECRET_KEY")  # Load securely from env


payment_db = PaymentDB()

def create_stripe_checkout_session(
    order_id: str,
    user_id: str,               # Add user_id param to create payment record
    amount_pence: int,          # amount in smallest currency unit
    currency: str = "GBP",
) -> Dict[str, Any]:
    try:
        # Create payment record with status "pending"
        payment_db.create_payment(
            order_id=order_id,
            user_id=user_id,
            amount=amount_pence / 100,  # convert pence to pounds for your DB
            currency=currency,
            method="card",
            provider="Stripe",
            status="pending",
            reference=None,
            paid_at=None,
            raw_response=None,
        )
        
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": currency,
                    "product_data": {"name": f"Order {order_id}"},
                    "unit_amount": amount_pence,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"https://yourdomain.com/payment-success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"https://yourdomain.com/payment-cancelled?session_id={{CHECKOUT_SESSION_ID}}",
            metadata={
                "order_id": order_id,
                "user_id": user_id,   # Pass user_id so webhook can use it
            },
        )
        return standard_response(True, data={"checkout_url": session.url})
    except Exception as e:
        return standard_response(False, error=str(e))

def create_payment(
    order_id: str,
    user_id: str,
    amount: float,
    currency: str = "GBP",
    method: str = "card",
    provider: str = "Stripe",
    status: str = "pending",
    reference: Optional[str] = None,
    paid_at: Optional[str] = None,
    raw_response: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        payment = payment_db.create_payment(
            order_id=order_id,
            user_id=user_id,
            amount=amount,
            currency=currency,
            method=method,
            provider=provider,
            status=status,
            reference=reference,
            paid_at=paid_at,
            raw_response=raw_response,
        )
        return standard_response(True, data=payment)
    except Exception as e:
        return standard_response(False, error=str(e))

def get_payment(payment_id: str) -> Dict[str, Any]:
    payment = payment_db.get_payment(payment_id)
    if payment:
        return standard_response(True, data=payment)
    else:
        return standard_response(False, error="Payment not found")

def list_payments(user_id: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    payments = payment_db.list_payments(user_id=user_id, limit=limit)
    return standard_response(True, data=payments)

# ---- LangChain Tools ----

@tool
def create_payment_tool(**kwargs):
    """Create a new payment record with order and user details."""
    return create_payment(**kwargs)

@tool
def get_payment_tool(**kwargs):
    """Retrieve payment details by payment ID."""
    return get_payment(**kwargs)

@tool
def list_payments_tool(**kwargs):
    """List payments optionally filtered by user ID."""
    return list_payments(**kwargs)

@tool
def create_checkout_session_tool(**kwargs):
    """Generate a Stripe checkout URL for the given order."""
    return create_stripe_checkout_session(**kwargs)

# ---- FastAPI webhook endpoint for updating payment status ----

app = FastAPI()
router = APIRouter()

@router.post("/payments/webhook")
async def payment_webhook(request: Request):
    """
    Webhook endpoint for payment provider to update payment status.
    The payment processor calls this endpoint with payment updates.
    """
    try:
        payload = await request.json()
        # Extract relevant fields from payload - adapt to your processor's webhook format
        payment_id = payload.get("payment_id")
        status = payload.get("status")
        reference = payload.get("reference")
        paid_at = payload.get("paid_at")
        raw_response = payload  # store entire webhook payload optionally

        if not payment_id or not status:
            raise HTTPException(status_code=400, detail="Missing payment_id or status")

        success = payment_db.update_status(
            payment_id=payment_id,
            status=status,
            reference=reference,
            paid_at=paid_at,
            raw_response=raw_response,
        )
        if not success:
            raise HTTPException(status_code=404, detail="Payment not found or not updated")

        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

app.include_router(router)
