"""
NLU API — Flask stub serving the same /resolve contract as luma, on port 9002.

Request body (POST /resolve):
    {
        "text": str,                        # user message
        "tenant_context": {
            "aliases": {str: str},          # service alias map
            "booking_mode": "service"|"reservation",
            "booking_id": {                 # optional — booking reference ID format
                "pattern": str,               # full-match regex (default: ^[A-Z]{2,}\\d{3,}$)
                "scan_pattern": str,          # optional text scan regex
                "examples": [str]             # optional Haiku prompt hints only
            }
        },
        "conversation_context": {           # optional — omit for stateless behaviour
            "last_intent": str,             # intent from the previous turn
            "last_search_query": str,       # search_query from the previous turn
            "turns": [                      # prior turns, max 3 used
                {
                    "user": str,
                    "assistant": str,
                    "intent": str,
                    "search_query": str | null
                }
            ]
        },
        "test_now": str,                    # optional ISO datetime for deterministic tests
        "timezone": str                     # optional, defaults to "UTC"
    }

Response contract (mirrors luma /resolve):
    {
        "intent": {"name": str, "confidence": float},
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": str | null,
            "booking_id": str | null
        },
        "time_constraint": {...} | null,    # only when a time window is present
        "search_query": str | null          # only for RAG intents
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
    conversation_context = body.get("conversation_context") or None

    result = _pipeline.run(
        text, tenant_context, now=test_now, timezone=timezone,
        conversation_context=conversation_context,
    )

    response = {
        "intent": result.intent,
        "facts": result.facts,
    }
    if result.time_constraint is not None:
        response["time_constraint"] = result.time_constraint
    if result.date_constraint is not None:
        response["date_constraint"] = result.date_constraint
    if result.search_query is not None:
        response["search_query"] = result.search_query

    return jsonify(response)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "9002"))
    app.run(host="0.0.0.0", port=port)
