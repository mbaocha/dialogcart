"""
NLU API — Flask service serving the `/resolve` contract on port 9002.

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
        "entity_schema": {                  # optional — schema-driven CREATE business entities
            "version": 1,
            "fields": [
                {
                    "name": str,
                    "type": "catalog" | "enum" | "text",
                    "description": str,
                    "role": "bookable_item" | "staff" | "booking_subject",  # optional metadata
                    "catalog": {str: str},    # required for catalog — phrase → id
                    "values": [str]           # required for enum
                }
            ]
        },
        # When entity_schema is present, declared fields are preserved on facts.
        # Each catalog field resolves independently against its own catalog.
        # bookable_item still emits facts.service_id + service_candidates for Core.
        # When entity_schema is present, for identical normalized (case-insensitive)
        # lookup keys: entity_schema.catalog > tenant_context.aliases (legacy flat merge).
        # Prompt text preserves original catalog phrase labels; resolve uses
        # lowercased keys. Compiled once in the pipeline; CREATE does not recompile.
        "conversation_context": {           # optional — omit for stateless behaviour
            "last_intent": str,             # intent from the previous turn
            "last_search_query": str,       # search_query from the previous turn
            "active_booking_intent": str,     # durable booking session after FAQ detour (optional)
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
        "entity_resolutions": {             # always present; authoritative business entities
            "<schema field name>": {
                "resolution": "RESOLVED" | "AMBIGUOUS" | "UNRESOLVED",
                "value": object,            # RESOLVED only
                "candidate_values": [object] # AMBIGUOUS only, >= 2 distinct
            }
        },
        "time_constraint": {...} | null,    # only when a time window is present
        "date_constraint": {...} | null,    # only when date mode is flexible
        "search_query": str | null,         # only for RAG intents
        "off_topic_query": str | null,      # only for OFF_TOPIC (canonical question)
        "answerable": bool | null,          # only for OFF_TOPIC (Core digression evidence)
        "answer": str | null,               # only for OFF_TOPIC (brief evidence answer)
        "operation": str | null,            # structured interaction subtype (e.g. browse_next)
        "service_category": {               # optional category semantic evidence
            "name": str,
            "resolution": "RESOLVED" | "AMBIGUOUS" | "UNRESOLVED"
        },
        "catalog_selection": {              # optional presented catalogue ordinal
            "presentation_ref": str,
            "kind": "category" | "service",
            "option": int
        },
        "declined_entities": [str, ...],    # schema field names explicitly declined (optional)
        "temporal": {...} | null,           # canonical temporal with optional nested resolution
        "turn": {"understanding": "UNDERSTOOD" | "UNRECOGNIZED_INPUT"}
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
from .entity_resolution import serialize_entity_resolutions
from .stages.stage2.entity_schema import EntitySchemaValidationError

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
    # Absent key and explicit null both mean legacy path (no schema).
    entity_schema = body.get("entity_schema", None)

    try:
        result = _pipeline.run(
            text,
            tenant_context,
            now=test_now,
            timezone=timezone,
            conversation_context=conversation_context,
            entity_schema=entity_schema,
        )
    except EntitySchemaValidationError as exc:
        logger.warning("entity_schema validation failed: %s", exc)
        return jsonify({
            "error": "entity_schema_invalid",
            "message": str(exc),
        }), 400

    response = {
        "intent": result.intent,
        "facts": result.facts,
        "entity_resolutions": serialize_entity_resolutions(result.entity_resolutions),
    }
    if result.time_constraint is not None:
        response["time_constraint"] = result.time_constraint
    if result.date_constraint is not None:
        response["date_constraint"] = result.date_constraint
    if result.search_query is not None:
        response["search_query"] = result.search_query
    if result.off_topic_query is not None:
        response["off_topic_query"] = result.off_topic_query
    if result.answerable is not None:
        response["answerable"] = result.answerable
    if result.answer is not None:
        response["answer"] = result.answer
    if result.service_candidates:
        response["service_candidates"] = result.service_candidates
    if result.operation is not None:
        response["operation"] = result.operation
    if result.service_category is not None:
        response["service_category"] = result.service_category
    if result.catalog_selection is not None:
        response["catalog_selection"] = result.catalog_selection
    if result.response_act is not None:
        response["response_act"] = result.response_act
    if result.declined_entities:
        response["declined_entities"] = list(result.declined_entities)
    if result.temporal is not None:
        response["temporal"] = result.temporal
    if result.understanding:
        response["turn"] = {"understanding": result.understanding}

    return jsonify(response)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


def main():
    """Run the Flask development server (canonical NLU service)."""
    port = int(os.getenv("PORT", "9002"))
    logger.info("NLU API starting on http://localhost:%s", port)
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
