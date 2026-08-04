"""
Tests for _resolve_service_ambiguity with missing_slots context (Phase 2).

LLM extracts service_term (raw phrase); catalog.resolve_service does fuzzy
alias matching. Prior-turn narrowing is gated on 'service_id' in
context['missing_slots'] — core is the authority on what's still awaited.

Matrix:
  A  cold fresh + ambiguous term          → no missing_slots   → candidates, no narrowing
  B  missing_slots:[service_id] + bare term after prior turn   → narrowed via prior text
  C  missing_slots:[service_id] + booking verb present         → still narrowed (verb irrelevant)
  D  service_id satisfied (not in missing_slots) + verb        → no narrowing → candidates
  E  context has turns but no missing_slots key                → no narrowing → candidates
  F  service_candidates in context + stale flexi in turns      → list pick resolves "premium"
  G  resolved_service_id + no service_term (date-only turn)   → reuse locked service
  H  resolved_service_id + context-leaked service_term on 12pm → strip term, reuse locked
  I  resolved_service_id + service_term Flexi (AVAILABILITY) → keep Flexi
"""
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("anthropic", MagicMock())

from nlu.pipeline import NLUPipeline, _strip_unmentioned_service

_ALIASES = {
    "premium haircut": "beauty.premium",
    "standard haircut": "beauty.standard",
    "flexi haircut + prunning": "beauty.flexi",
}
_TENANT_CONTEXT = {"aliases": _ALIASES}
_PRIOR_PREMIUM = {"user": "i want premium", "intent": "CREATE_APPOINTMENT", "search_query": None}
_PRIOR_HAIRCUT = {"user": "i want a haircut", "intent": "CREATE_APPOINTMENT", "search_query": None}


def _resolve(service_term, conversation_context, text=None):
    """Exercise _resolve_service_ambiguity directly with a Phase-2-shaped SLM."""
    pipeline = NLUPipeline()
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "facts": {"service_id": None},
        "service_candidates": [],
        "service_term": service_term,
    }
    result = pipeline._resolve_service_ambiguity(
        slm, _TENANT_CONTEXT, conversation_context, text=text
    )
    return result["facts"].get("service_id"), result.get("service_candidates", [])


class TestServiceAmbiguityContext:
    def test_A_cold_start_no_context_returns_candidates(self):
        """A: no session → no missing_slots → prior narrowing blocked → show candidates."""
        service_id, candidates = _resolve("haircut", conversation_context=None)
        assert service_id is None
        assert len(candidates) > 0

    def test_B_missing_slots_service_id_bare_term_narrowed_via_prior(self):
        """B: bot asked for service, user says bare 'premium' → resolves via prior 'haircut' turn."""
        ctx = {
            "missing_slots": ["service_id"],
            "turns": [_PRIOR_HAIRCUT],
        }
        # "premium" alone is not in the catalog; combined with prior "haircut" → "premium haircut"
        service_id, candidates = _resolve("premium", ctx)
        assert service_id == "premium haircut"
        assert candidates == []

    def test_C_missing_slots_service_id_booking_verb_still_narrowed(self):
        """C: 'haircut' with missing_slots:[service_id] + prior 'premium' turn → narrowed."""
        ctx = {
            "missing_slots": ["service_id"],
            "turns": [_PRIOR_PREMIUM],
        }
        # "haircut" alone ties; combined with prior "i want premium" → "premium haircut"
        service_id, candidates = _resolve("haircut", ctx)
        assert service_id == "premium haircut"
        assert candidates == []

    def test_D_service_id_satisfied_no_prior_narrowing(self):
        """D: service_id already filled (not in missing_slots) → no narrowing → show candidates."""
        ctx = {
            "missing_slots": ["date"],
            "turns": [_PRIOR_PREMIUM],
        }
        service_id, candidates = _resolve("haircut", ctx)
        assert service_id is None
        assert len(candidates) > 0

    def test_E_no_missing_slots_key_blocks_prior_narrowing(self):
        """E: context has turns but no missing_slots key → treated as cold → no narrowing."""
        ctx = {
            "last_intent": "CREATE_APPOINTMENT",
            "turns": [_PRIOR_PREMIUM],
        }
        service_id, candidates = _resolve("haircut", ctx)
        assert service_id is None
        assert len(candidates) > 0

    def test_F_service_candidates_list_pick_ignores_stale_flexi_in_turns(self):
        """F: bot offered haircut options; 'premium' resolves once despite old flexi turn."""
        ctx = {
            "missing_slots": ["service_id"],
            "service_candidates": ["premium haircut", "flexi haircut + prunning"],
            "turns": [
                {"user": "book me for a service", "intent": "CREATE_APPOINTMENT"},
                {"user": "flexi", "intent": "CREATE_APPOINTMENT"},
                {"user": "book me a haircut", "intent": "CREATE_APPOINTMENT"},
            ],
        }
        service_id, candidates = _resolve("premium", ctx)
        assert service_id == "premium haircut"
        assert candidates == []

    def test_G_resolved_service_id_date_only_follow_up(self):
        """G: AVAILABILITY date refinement — no service_term, session service locked."""
        ctx = {
            "last_intent": "CREATE_APPOINTMENT",
            "missing_slots": ["date", "time"],
            "resolved_service_id": "premium haircut",
            "turns": [
                {"user": "book me a haircut", "intent": "CREATE_APPOINTMENT"},
                {"user": "premium", "intent": "CREATE_APPOINTMENT"},
            ],
        }
        service_id, candidates = _resolve(None, ctx)
        assert service_id == "premium haircut"
        assert candidates == []

    def test_I_availability_service_term_overrides_resolved_session(self):
        """I: AVAILABILITY service_term Flexi must not be overwritten by Premium session."""
        ctx = {
            "last_intent": "CREATE_APPOINTMENT",
            "missing_slots": ["date", "time"],
            "resolved_service_id": "premium haircut",
            "turns": [
                {"user": "book me a haircut", "intent": "CREATE_APPOINTMENT"},
                {"user": "premium", "intent": "CREATE_APPOINTMENT"},
            ],
        }
        pipeline = NLUPipeline()
        slm = {
            "intent": "AVAILABILITY",
            "facts": {"service_id": None},
            "service_candidates": [],
            "service_term": "flexi haircut + prunning",
        }
        result = pipeline._resolve_service_ambiguity(slm, _TENANT_CONTEXT, ctx)
        assert result["facts"].get("service_id") == "flexi haircut + prunning"
        assert result.get("service_candidates") == []

    def test_I_legacy_facts_service_id_still_overrides_resolved_session(self):
        """Legacy AVAILABILITY facts.service_id shape still honored when service_term is null."""
        ctx = {
            "last_intent": "CREATE_APPOINTMENT",
            "missing_slots": ["date", "time"],
            "resolved_service_id": "premium haircut",
            "turns": [
                {"user": "book me a haircut", "intent": "CREATE_APPOINTMENT"},
                {"user": "premium", "intent": "CREATE_APPOINTMENT"},
            ],
        }
        pipeline = NLUPipeline()
        slm = {
            "intent": "AVAILABILITY",
            "facts": {"service_id": "flexi haircut + prunning"},
            "service_candidates": [],
            "service_term": None,
        }
        result = pipeline._resolve_service_ambiguity(slm, _TENANT_CONTEXT, ctx)
        assert result["facts"].get("service_id") == "flexi haircut + prunning"
        assert result.get("service_candidates") == []

    def test_J_utterance_flexi_overrides_sticky_premium_when_term_omitted(self):
        """Prior Premium + 'show availability for Flexi' must emit Flexi service_id."""
        ctx = {
            "last_intent": "CREATE_APPOINTMENT",
            "missing_slots": ["date", "time"],
            "resolved_service_id": "premium haircut",
            "turns": [
                {"user": "book me a haircut", "intent": "CREATE_APPOINTMENT"},
                {"user": "premium", "intent": "CREATE_APPOINTMENT"},
            ],
        }
        pipeline = NLUPipeline()
        slm = {
            "intent": "AVAILABILITY",
            "facts": {"service_id": None},
            "service_candidates": [],
            "service_term": None,
        }
        result = pipeline._resolve_service_ambiguity(
            slm,
            _TENANT_CONTEXT,
            ctx,
            text="show availability for Flexi",
        )
        assert result["facts"].get("service_id") == "flexi haircut + prunning"
        assert result.get("service_candidates") == []

    def test_J_bare_show_availability_may_keep_sticky_premium(self):
        """Prior Premium + 'show availability' (no service mention) may remain Premium."""
        ctx = {
            "last_intent": "CREATE_APPOINTMENT",
            "missing_slots": ["date", "time"],
            "resolved_service_id": "premium haircut",
            "turns": [
                {"user": "book me a haircut", "intent": "CREATE_APPOINTMENT"},
                {"user": "premium", "intent": "CREATE_APPOINTMENT"},
            ],
        }
        pipeline = NLUPipeline()
        slm = {
            "intent": "AVAILABILITY",
            "facts": {"service_id": None},
            "service_candidates": [],
            "service_term": None,
        }
        result = pipeline._resolve_service_ambiguity(
            slm,
            _TENANT_CONTEXT,
            ctx,
            text="show availability",
        )
        assert result["facts"].get("service_id") == "premium haircut"
        assert result.get("service_candidates") == []

    def test_H_resolved_service_id_strips_context_leaked_term_on_12pm(self):
        """H: LLM leaks service_term from context on time-only turn — strip before resolve."""
        ctx = {
            "last_intent": "CREATE_APPOINTMENT",
            "missing_slots": ["time"],
            "resolved_service_id": "premium haircut",
            "turns": [
                {"user": "book me a haircut", "intent": "CREATE_APPOINTMENT"},
                {"user": "premium", "intent": "CREATE_APPOINTMENT"},
            ],
        }
        pipeline = NLUPipeline()
        slm = {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": None, "times": ["12:00"]},
            "service_candidates": [],
            "service_term": "premium",
        }
        slm = _strip_unmentioned_service("12pm", slm)
        assert slm.get("service_term") is None
        result = pipeline._resolve_service_ambiguity(slm, _TENANT_CONTEXT, ctx)
        assert result["facts"].get("service_id") == "premium haircut"
        assert result.get("service_candidates") == []
