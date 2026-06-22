"""
NLU API — Flask stub serving the same /resolve contract as luma, on port 9002.

Response contract (mirrors luma /resolve):
    {
        "intent": {"name": str, "confidence": float},
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": str | null,
            "booking_id": str | null
        }
    }

Fields that must NOT appear (luma contract):
    status, missing_slots, issues, clarification_reason, clarification, booking
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the nlu package directory (dialogcart/src/nlu/.env)
load_dotenv(Path(__file__).parent / ".env")

from flask import Flask, jsonify, request

from .pipeline import NLUPipeline

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = Flask(__name__)
_pipeline = NLUPipeline()


@app.route("/resolve", methods=["POST"])
def resolve():
    body = request.get_json(force=True) or {}
    text = body.get("text", "")
    tenant_context = body.get("tenant_context", {})
    test_now = body.get("test_now")
    timezone = body.get("timezone", "UTC")

    result = _pipeline.run(text, tenant_context, now=test_now, timezone=timezone)

    response = {
        "intent": result.intent,
        "facts": result.facts,
    }
    if result.time_constraint is not None:
        response["time_constraint"] = result.time_constraint
    if result.search_query is not None:
        response["search_query"] = result.search_query

    return jsonify(response)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "9002"))
    app.run(host="0.0.0.0", port=port)
