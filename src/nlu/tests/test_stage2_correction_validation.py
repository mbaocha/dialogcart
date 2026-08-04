"""CORRECTION = workflow-state change; informational disagreement is not CORRECTION."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nlu.registry.intent_groups import get_stage2_group
from nlu.stages.stage2 import dispatcher
from nlu.stages.stage2.base_prompt import intent_validation_section
from nlu.stages.stage2.groups.create import _system_prompt as create_prompt
from nlu.stages.stage2.groups.faq import _system_prompt as faq_prompt


def _load_anthropic_key() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    root = Path(__file__).resolve().parents[3]
    for env_path in (root / "src" / "nlu" / ".env", root / "src" / ".env", root / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "ANTHROPIC_API_KEY" and v:
                os.environ["ANTHROPIC_API_KEY"] = v
                return True
    return False

_INFORMATIONAL = frozenset(
    {
        "QUOTE",
        "PAYMENT",
        "PAYMENT_STATUS",
        "GENERAL_INQUIRY",
        "DETAILS",
        "DISCOVERY",
        "RECOMMENDATION",
    }
)

_BOOKING_CTX = {
    "last_intent": "GENERAL_INQUIRY",
    "last_search_query": "pricing cost 105",
    "active_booking_intent": "CREATE_APPOINTMENT",
    "missing_slots": ["date", "time"],
}

_INFORMATIONAL_UTTERANCES = (
    "I thought the £10 fee came off the final price.",
    "I thought breakfast was included.",
    "I thought the deposit was refundable.",
    "I thought the service only cost £95.",
    "Are you sure the total is £105?",
    "That doesn't sound right; shouldn't I have £85 left to pay?",
)

_WORKFLOW_CORRECTIONS = (
    ("Actually make it 10am.", "time"),
    ("Change it to diesel.", "engine"),
    ("Use Premium Full Service instead.", "service"),
    ("I meant tomorrow, not Friday.", "date"),
    ("My registration is AB12CDE, not AAABC123.", "registration"),
)


def test_shared_contract_defines_correction_as_workflow_state_change():
    section = intent_validation_section("CORRECTION")
    assert "CORRECTION vs INFORMATIONAL CLARIFICATION" in section
    assert "workflow or proposed action" in section
    assert "An active booking alone is NOT evidence of CORRECTION" in section
    assert 'Cue words alone ("thought", "meant", "wrong", "actually", "instead")' in section
    assert "Do NOT use CORRECTION when the user questions an explanation" in section
    assert "I thought the fee came off the final price." in section
    assert "Actually make it 10am." in section
    # Present in every group via shared section — not CREATE/FAQ-only.
    for builder, kwargs in (
        (
            create_prompt,
            {
                "now": "2026-08-03T12:00:00",
                "tenant_context": {"booking_mode": "service", "aliases": {}},
                "conversation_context": None,
                "candidate_intent": "CORRECTION",
            },
        ),
        (
            faq_prompt,
            {
                "now": "2026-08-03T12:00:00",
                "conversation_context": None,
                "candidate_intent": "CORRECTION",
            },
        ),
    ):
        prompt = builder(**kwargs)
        assert "CORRECTION vs INFORMATIONAL CLARIFICATION" in prompt


@pytest.fixture(autouse=True)
def _clear_extractor_cache():
    dispatcher._instances.clear()
    yield
    dispatcher._instances.clear()


@pytest.mark.parametrize("text", _INFORMATIONAL_UTTERANCES)
def test_forced_correction_informational_rejects_and_stage3_faq(text):
    """Forced Stage 1 CORRECTION → Stage 2 informational → Stage 3 FAQ; no slot leak."""
    validated = "GENERAL_INQUIRY"
    first_out = {
        "intent": validated,
        "confidence": 0.88,
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "service_term": "SHOULD_NOT_LEAK",
        "time_constraint": None,
        "search_query": None,
        "service_candidates": [],
    }
    second_out = {
        "intent": validated,
        "confidence": 0.91,
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "service_term": None,
        "time_constraint": None,
        "search_query": "pricing clarification",
        "service_candidates": [],
    }

    mock_create = MagicMock()
    mock_create.extract.return_value = first_out
    mock_faq = MagicMock()
    mock_faq.extract.return_value = second_out

    def _get_extractor(group: str):
        return {"create": mock_create, "faq": mock_faq}[group]

    with patch.object(dispatcher, "_get_extractor", side_effect=_get_extractor):
        result = dispatcher.extract_slots(
            intent="CORRECTION",
            text=text,
            now="2026-08-03T12:00:00",
            tenant_context={"booking_mode": "service", "aliases": {}},
            conversation_context=_BOOKING_CTX,
        )

    assert mock_create.extract.call_count == 1
    assert mock_create.extract.call_args.kwargs["candidate_intent"] == "CORRECTION"
    assert mock_faq.extract.call_count == 1
    assert mock_faq.extract.call_args.kwargs["candidate_intent"] == validated
    assert result["intent"] == validated
    assert result["intent"] in _INFORMATIONAL
    assert result["service_term"] is None
    assert result.get("search_query") == "pricing clarification"


@pytest.mark.parametrize("text,kind", _WORKFLOW_CORRECTIONS)
def test_forced_correction_workflow_retains_correction_no_stage3(text, kind):
    """Genuine workflow corrections keep CORRECTION; create extraction only."""
    slot_facts = {
        "time": {"times": ["10:00"], "dates": [], "service_term": None},
        "engine": {"times": [], "dates": [], "service_term": None},
        "service": {"times": [], "dates": [], "service_term": "Premium Full Service"},
        "date": {"times": [], "dates": ["2026-08-04"], "service_term": None},
        "registration": {"times": [], "dates": [], "service_term": None},
    }[kind]
    create_out = {
        "intent": "CORRECTION",
        "confidence": 0.9,
        "facts": {
            "dates": slot_facts["dates"],
            "times": slot_facts["times"],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "service_term": slot_facts["service_term"],
        "time_constraint": None,
        "search_query": None,
        "service_candidates": [],
    }

    mock_create = MagicMock()
    mock_create.extract.return_value = create_out

    def _get_extractor(group: str):
        if group == "faq":
            raise AssertionError("Stage 3 FAQ must not run when CORRECTION is retained")
        return mock_create

    with patch.object(dispatcher, "_get_extractor", side_effect=_get_extractor):
        result = dispatcher.extract_slots(
            intent="CORRECTION",
            text=text,
            now="2026-08-03T12:00:00",
            tenant_context={"booking_mode": "service", "aliases": {}},
            conversation_context={
                "last_intent": "CREATE_APPOINTMENT",
                "active_booking_intent": "CREATE_APPOINTMENT",
            },
        )

    assert mock_create.extract.call_count == 1
    assert result["intent"] == "CORRECTION"
    assert get_stage2_group(result["intent"]) == "create"
    if kind == "time":
        assert result["facts"]["times"] == ["10:00"]
    elif kind == "service":
        assert result["service_term"] == "Premium Full Service"
    elif kind == "date":
        assert result["facts"]["dates"] == ["2026-08-04"]


_HAS_ANTHROPIC = _load_anthropic_key()


@pytest.mark.skipif(not _HAS_ANTHROPIC, reason="ANTHROPIC_API_KEY required for live Stage 2")
@pytest.mark.parametrize("text", _INFORMATIONAL_UTTERANCES)
def test_live_forced_correction_rejects_informational(text):
    """Live: forced CORRECTION on informational clarification must not stick."""
    dispatcher._instances.clear()
    result = dispatcher.extract_slots(
        intent="CORRECTION",
        text=text,
        now="2026-08-03T12:00:00",
        tenant_context={"booking_mode": "service", "aliases": {}},
        conversation_context=_BOOKING_CTX,
    )
    assert result["intent"] != "CORRECTION", result
    assert result["intent"] in _INFORMATIONAL | {"OFF_TOPIC"}, result
    # Informational path should not invent a booking service replacement.
    assert not result.get("service_term")


@pytest.mark.skipif(not _HAS_ANTHROPIC, reason="ANTHROPIC_API_KEY required for live Stage 2")
@pytest.mark.parametrize(
    "text",
    [
        "Actually make it 10am.",
        "Use Premium Full Service instead.",
        "I meant tomorrow, not Friday.",
    ],
)
def test_live_forced_correction_retains_workflow(text):
    dispatcher._instances.clear()
    result = dispatcher.extract_slots(
        intent="CORRECTION",
        text=text,
        now="2026-08-03T12:00:00",
        tenant_context={"booking_mode": "service", "aliases": {}},
        conversation_context={
            "last_intent": "CREATE_APPOINTMENT",
            "active_booking_intent": "CREATE_APPOINTMENT",
            "missing_slots": ["date", "time", "service"],
        },
    )
    assert result["intent"] == "CORRECTION", result
    has_slot = bool(
        result.get("service_term")
        or (result.get("facts") or {}).get("times")
        or (result.get("facts") or {}).get("dates")
        or ((result.get("temporal") or {}).get("start_time"))
        or ((result.get("temporal") or {}).get("start_date"))
        or ((result.get("temporal") or {}).get("start_time_expression"))
        or ((result.get("temporal") or {}).get("start_date_expression"))
    )
    assert has_slot, result
