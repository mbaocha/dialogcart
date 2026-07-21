"""
Intent registry — single source of truth for intent taxonomy and Stage 2 group mapping.

INTENT_GROUPS is the canonical definition. All derived lookups (ALL_INTENTS, RAG_INTENTS,
_INTENT_TO_STAGE2_GROUP) are computed once at import time from this dict.
"""
from typing import Dict, Optional

INTENT_GROUPS: Dict[str, dict] = {
    "booking": {
        "requires_booking_verb": True,
        "search_query": False,
        "intents": {
            "CREATE_APPOINTMENT": "booking a timed service (haircut, massage, trim…)",
            "CREATE_RESERVATION": "booking accommodation/space for a date range (room, suite…)",
            "MODIFY_BOOKING":     "changing an existing booking",
            "CANCEL_BOOKING":     "cancelling an existing booking",
        },
    },
    "booking_query": {
        "requires_booking_verb": False,
        "search_query": False,
        "intents": {
            "BOOKING_INQUIRY": "asking about an existing booking",
            "AVAILABILITY":    "asking what times/slots are free",
            "PAYMENT":         "wants to pay",
            "PAYMENT_STATUS":  "asking about payment status",
        },
    },
    "informational": {
        "requires_booking_verb": False,
        "search_query": True,
        "intents": {
            "DISCOVERY":       "asking what services are offered",
            "DETAILS":         "asking for service details or info",
            "QUOTE":           "asking for price/cost",
            "RECOMMENDATION":  "asking for a recommendation",
            "GENERAL_INQUIRY": "business FAQ not covered above (policies, hours, location, payments, store info — not world knowledge)",
        },
    },
    "out_of_scope": {
        "requires_booking_verb": False,
        "search_query": False,
        "intents": {
            "OFF_TOPIC": "coherent, understood request outside this business's domain (world knowledge, jokes, unrelated topics — not business FAQs)",
        },
    },
    "dialog": {
        "requires_booking_verb": False,
        "search_query": False,
        "intents": {
            "CONFIRM_ACTION": "confirming a proposed action (yes, confirm, ok, sure)",
            "REJECT_ACTION":  "rejecting a proposed action (no, cancel that, don't)",
            "CORRECTION":     "correcting or replacing a slot in the CURRENT flow — ONLY when conversation context shows an active booking intent",
        },
    },
    "fallback": {
        "requires_booking_verb": False,
        "search_query": False,
        "intents": {
            "UNKNOWN": "utterance not understood — gibberish, fragments, or truly indeterminate (not a coherent off-topic request)",
        },
    },
}

# ── Stage 2 group assignment ─────────────────────────────────────────────────
# Maps each intent to the Stage 2 group extractor that handles it.
# Intents with None are handled directly by the pipeline (no slot extraction needed).
_STAGE2_GROUP: Dict[str, Optional[str]] = {
    # booking
    "CREATE_APPOINTMENT":  "create",
    "CREATE_RESERVATION":  "create",
    "MODIFY_BOOKING":      "modify",
    "CANCEL_BOOKING":      "cancel",
    # booking_query
    "BOOKING_INQUIRY":     "view",
    "AVAILABILITY":        "availability",
    "PAYMENT":             "view",
    "PAYMENT_STATUS":      "view",
    # informational
    "DISCOVERY":           "faq",
    "DETAILS":             "faq",
    "QUOTE":               "faq",
    "RECOMMENDATION":      "faq",
    "GENERAL_INQUIRY":     "faq",
    # out_of_scope — validated by FAQ Stage 2 (GENERAL_INQUIRY vs OFF_TOPIC)
    "OFF_TOPIC":           "faq",
    # dialog — pipeline handles these, no Stage 2 slot extraction
    "CONFIRM_ACTION":      None,
    "REJECT_ACTION":       None,
    "CORRECTION":          "create",  # re-uses create group; handles slot updates for active booking
    # fallback
    "UNKNOWN":             None,
}

# ── Derived lookups (computed once) ─────────────────────────────────────────
ALL_INTENTS: list = [i for g in INTENT_GROUPS.values() for i in g["intents"]]
RAG_INTENTS: set = {i for g in INTENT_GROUPS.values() if g["search_query"] for i in g["intents"]}


def get_stage2_group(intent: str) -> Optional[str]:
    """Return the Stage 2 group name for a given intent, or None if not applicable."""
    return _STAGE2_GROUP.get(intent)


def format_intent_registry() -> str:
    """Compact intent registry for use in Stage 1 and Stage 2 prompts."""
    verb_groups: Dict[bool, list] = {True: [], False: []}
    for group in INTENT_GROUPS.values():
        for intent, desc in group["intents"].items():
            if intent != "UNKNOWN":
                verb_groups[group["requires_booking_verb"]].append(
                    f"- {intent:<22} — {desc}"
                )
    lines = [
        "Intents that REQUIRE an explicit booking verb (book, reserve, schedule, cancel, modify, etc.):",
        *verb_groups[True],
        "",
        "Intents that do NOT require a booking verb:",
        *verb_groups[False],
        "- UNKNOWN                  — utterance not understood (gibberish/fragments; not a coherent off-topic request)",
    ]
    return "\n".join(lines)
