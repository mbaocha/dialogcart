"""
Reservation Intent Resolver

Rule-based intent resolution for service/appointment/reservation booking.
Replaces legacy ML-based intent mapping with deterministic, explainable rules.

Determines user intent from:
- Extracted entities (services, dates, times, durations)
- Lexical cues (cancel, reschedule, etc.) loaded from config

NO ML. NO embeddings. NO NER dependency.
"""
import re
from pathlib import Path
from typing import Tuple, Dict, Any, List, Set

import yaml


# Canonical intents (locked - 10 production intents)
DISCOVERY = "DISCOVERY"
DETAILS = "DETAILS"
AVAILABILITY = "AVAILABILITY"
QUOTE = "QUOTE"
RECOMMENDATION = "RECOMMENDATION"
CREATE_APPOINTMENT = "CREATE_APPOINTMENT"
CREATE_RESERVATION = "CREATE_RESERVATION"
CREATE_BOOKING = "CREATE_BOOKING"
BOOKING_INQUIRY = "BOOKING_INQUIRY"
MODIFY_BOOKING = "MODIFY_BOOKING"
CANCEL_BOOKING = "CANCEL_BOOKING"
PAYMENT = "PAYMENT"
UNKNOWN = "UNKNOWN"

# Confidence scores (heuristic)
HIGH_CONFIDENCE = 0.95
MEDIUM_CONFIDENCE = 0.85
LOW_CONFIDENCE = 0.75


class ReservationIntentResolver:
    """
    Rule-based intent resolver for appointment/reservation booking.

    Uses ordered rules (first match wins) to determine user intent.
    Deterministic, explainable, and fast.
    """

    def __init__(self):
        """Initialize intent resolver with configuration-driven signals."""
        self.intent_signals, self.intent_meta = self._load_intent_signals()

        # Booking verbs (for CREATE_BOOKING)
        self.booking_verbs = {
            "book", "schedule", "reserve", "appointment", "appoint",
            "set", "arrange", "plan", "make",
            "need", "want", "look", "looking", "get",
            "recommend", "suggest"
        }

    def resolve_intent(
        self,
        osentence: str,
        entities: Dict[str, Any],
        booking_mode: str = "service"
    ) -> Tuple[str, float]:
        """
        Resolve intent from original sentence and extracted entities.

        Uses ordered rules (first match wins) to determine intent.
        Rule precedence: destructive/sensitive → booking → informational → unknown

        Args:
            osentence: Original user sentence (lowercased)
            entities: Extraction output with service_families, dates, times, etc.

        Returns:
            Tuple of (intent, confidence_score)
            intent: One of the 10 canonical intents or UNKNOWN
            confidence: Heuristic confidence score (0.75-0.95)

        Intent Decision Rules (ordered, first match wins):

        DESTRUCTIVE/SENSITIVE (check first):
        1. PAYMENT - Payment language
        2. CANCEL_BOOKING - Cancel patterns
        3. MODIFY_BOOKING - Reschedule/modify patterns

        BOOKING-RELATED:
        4. CREATE_BOOKING - Booking verbs + services + time constraints (entity-driven)
        5. AVAILABILITY - Availability language + date/time
        6. BOOKING_INQUIRY - Questions about existing booking

        INFORMATIONAL:
        7. DETAILS - Questions about service attributes (price, duration, inclusions, policies)
        8. QUOTE - Price/cost + service (contextual pricing)
        9. DISCOVERY - What services/rooms exist (services but no time constraints)
        10. RECOMMENDATION - Suggestions, recommendations

        FALLBACK:
        11. UNKNOWN - Nothing matches safely
        """
        if not osentence:
            return self._build_response(UNKNOWN, LOW_CONFIDENCE, entities)

        normalized_sentence = self._normalize_sentence(osentence)
        sentence_tokens_list, sentence_tokens_set = self._tokenize(
            normalized_sentence)

        # Extract entity counts (entity-driven rules first)
        service_families = entities.get(
            "business_categories") or entities.get("service_families", [])
        dates = entities.get("dates", [])
        dates_absolute = entities.get("dates_absolute", [])
        times = entities.get("times", [])
        time_windows = entities.get("time_windows", [])
        durations = entities.get("durations", [])

        has_services = len(service_families) > 0
        has_dates = len(dates) > 0 or len(dates_absolute) > 0
        has_times = len(times) > 0 or len(time_windows) > 0
        has_durations = len(durations) > 0
        has_time_constraints = has_dates or has_times or has_durations

        # ============================================================
        # DESTRUCTIVE/SENSITIVE INTENTS (check first for safety)
        # ============================================================

        # Rule 1: PAYMENT (destructive - check first)
        if self._matches_signals(normalized_sentence, sentence_tokens_list, sentence_tokens_set, PAYMENT):
            resp = self._build_response(PAYMENT, HIGH_CONFIDENCE, entities)
            return resp["intent"], resp["confidence"]

        # Rule 2: CANCEL_BOOKING (destructive - check early)
        if self._matches_signals(normalized_sentence, sentence_tokens_list, sentence_tokens_set, CANCEL_BOOKING):
            resp = self._build_response(
                CANCEL_BOOKING, HIGH_CONFIDENCE, entities)
            return resp["intent"], resp["confidence"]

        # Rule 3: MODIFY_BOOKING (destructive - check early)
        if self._matches_signals(normalized_sentence, sentence_tokens_list, sentence_tokens_set, MODIFY_BOOKING):
            resp = self._build_response(
                MODIFY_BOOKING, HIGH_CONFIDENCE, entities)
            return resp["intent"], resp["confidence"]

        # ============================================================
        # BOOKING-RELATED INTENTS
        # ============================================================

        # Determine which create intent is enabled by booking_mode
        booking_mode_normalized = "reservation" if booking_mode == "reservation" else "service"
        create_intent_key = CREATE_APPOINTMENT if booking_mode_normalized == "service" else CREATE_RESERVATION

        # Rule 4: CREATE_* (signal-first, preserves availability override and guards)
        if self._matches_signals(normalized_sentence, sentence_tokens_list, sentence_tokens_set, create_intent_key):
            is_availability_question = self._matches_signals(
                normalized_sentence, sentence_tokens_list, sentence_tokens_set, AVAILABILITY)
            if not is_availability_question:
                confidence = HIGH_CONFIDENCE if has_services and has_time_constraints else MEDIUM_CONFIDENCE
                resp = self._build_response(
                    create_intent_key, confidence, entities)
                return resp["intent"], resp["confidence"]

        # Preserve legacy entity-driven rule when signals don't match
        if has_services and has_time_constraints:
            is_availability_question = self._matches_signals(
                normalized_sentence, sentence_tokens_list, sentence_tokens_set, AVAILABILITY)
            if not is_availability_question:
                resp = self._build_response(
                    create_intent_key, HIGH_CONFIDENCE, entities)
                return resp["intent"], resp["confidence"]

        # Fallback rule: booking verbs + at least one of date/time/duration
        # SAFETY: Only permit fallback if at least some booking-relevant info exists (conservative, explainable)
        has_booking_verb = any(
            verb in normalized_sentence for verb in self.booking_verbs)
        if not has_services and has_booking_verb and (has_dates or has_times or has_durations):
            # This fallback enables CREATE_BOOKING intent for utterances like:
            # 'I want to book a full body massage this Friday at 4pm',
            # even if 'full body massage' fails extraction, since booking context is clear.
            resp = self._build_response(
                create_intent_key, MEDIUM_CONFIDENCE, entities)
            return resp["intent"], resp["confidence"]

        # Rule 5: BOOKING_INQUIRY (questions about existing booking - check before availability)
        if self._matches_signals(normalized_sentence, sentence_tokens_list, sentence_tokens_set, BOOKING_INQUIRY):
            resp = self._build_response(
                BOOKING_INQUIRY, HIGH_CONFIDENCE, entities)
            return resp["intent"], resp["confidence"]

        # Rule 6: AVAILABILITY (availability language + date/time OR just availability language)
        if self._matches_signals(normalized_sentence, sentence_tokens_list, sentence_tokens_set, AVAILABILITY):
            if has_time_constraints:
                resp = self._build_response(
                    AVAILABILITY, HIGH_CONFIDENCE, entities)
                return resp["intent"], resp["confidence"]
            elif has_services:
                resp = self._build_response(
                    AVAILABILITY, MEDIUM_CONFIDENCE, entities)
                return resp["intent"], resp["confidence"]
            else:
                # Pure availability check without entities
                resp = self._build_response(
                    AVAILABILITY, MEDIUM_CONFIDENCE, entities)
                return resp["intent"], resp["confidence"]

        # ============================================================
        # INFORMATIONAL INTENTS
        # ============================================================

        # Rule 7: DETAILS (questions about service attributes)
        if self._matches_signals(normalized_sentence, sentence_tokens_list, sentence_tokens_set, DETAILS):
            if has_services:
                resp = self._build_response(DETAILS, HIGH_CONFIDENCE, entities)
                return resp["intent"], resp["confidence"]
            else:
                resp = self._build_response(
                    DETAILS, MEDIUM_CONFIDENCE, entities)
                return resp["intent"], resp["confidence"]

        # Rule 8: QUOTE (price/cost + service)
        if self._matches_signals(normalized_sentence, sentence_tokens_list, sentence_tokens_set, QUOTE):
            if has_services:
                resp = self._build_response(QUOTE, HIGH_CONFIDENCE, entities)
                return resp["intent"], resp["confidence"]
            else:
                resp = self._build_response(QUOTE, MEDIUM_CONFIDENCE, entities)
                return resp["intent"], resp["confidence"]

        # Rule 9: DISCOVERY (what services/rooms exist - no time constraints required)
        discovery_match = self._matches_signals(
            normalized_sentence, sentence_tokens_list, sentence_tokens_set, DISCOVERY
        )
        if not has_time_constraints and (has_services or discovery_match):
            if discovery_match:
                resp = self._build_response(
                    DISCOVERY, HIGH_CONFIDENCE if has_services else MEDIUM_CONFIDENCE, entities)
                return resp["intent"], resp["confidence"]
            has_booking_verb = any(
                verb in normalized_sentence for verb in self.booking_verbs
            )
            if has_services and not has_booking_verb:
                resp = self._build_response(
                    DISCOVERY, MEDIUM_CONFIDENCE, entities)
                return resp["intent"], resp["confidence"]

        # Rule 10: RECOMMENDATION (suggestions, guidance)
        if self._matches_signals(normalized_sentence, sentence_tokens_list, sentence_tokens_set, RECOMMENDATION):
            resp = self._build_response(
                RECOMMENDATION, MEDIUM_CONFIDENCE, entities)
            return resp["intent"], resp["confidence"]

        # ============================================================
        # FALLBACK
        # ============================================================

        # Rule 11: UNKNOWN
        resp = self._build_response(UNKNOWN, LOW_CONFIDENCE, entities)
        return resp["intent"], resp["confidence"]

    def _load_intent_signals(self) -> Tuple[
        Dict[str, Dict[str, List[List[str]]]],
        Dict[str, Dict[str, Any]]
    ]:
        """
        Load intent signals from YAML file and normalize them.

        YAML structure per intent:
        - signals:
            - any: list of phrases (substring match on normalized sentence)
            - all: list of token lists (all tokens must be present, order-independent)
            - ordered: list of token lists (tokens must appear in order, not necessarily adjacent)
          (if "signals" is absent, top-level any/all/ordered are used for backward compatibility)
        - intent_defining_slots: mapping of slot categories (e.g., any: ["services"])
        - requires_time_constraint: bool
        """
        path = (
            Path(__file__).resolve().parent.parent
            / "store"
            / "normalization"
            / "intent_signals.yaml"
        )
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        intents_cfg = raw.get("intents", raw) if isinstance(raw, dict) else {}

        normalized: Dict[str, Dict[str, List[List[str]]]] = {}
        meta: Dict[str, Dict[str, Any]] = {}
        for intent, cfg in intents_cfg.items():
            if not isinstance(cfg, dict):
                continue

            signals_cfg = cfg.get("signals") or cfg.get(
                "intent_signals") or cfg

            any_phrases = []
            for phrase in signals_cfg.get("any", []) or []:
                if isinstance(phrase, str):
                    norm_phrase = self._normalize_sentence(phrase)
                    if norm_phrase:
                        any_phrases.append(norm_phrase)

            all_token_groups: List[List[str]] = []
            for token_group in signals_cfg.get("all", []) or []:
                tokens = self._normalize_token_group(token_group)
                if tokens:
                    all_token_groups.append(tokens)

            ordered_token_groups: List[List[str]] = []
            for token_group in signals_cfg.get("ordered", []) or []:
                tokens = self._normalize_token_group(token_group)
                if tokens:
                    ordered_token_groups.append(tokens)

            normalized[intent] = {
                "any": any_phrases,
                "all": all_token_groups,
                "ordered": ordered_token_groups,
            }

            meta[intent] = {
                "intent_defining_slots": cfg.get("intent_defining_slots") or cfg.get("intent_defining_slot") or [],
                "required_slots": cfg.get("required_slots") or [],
            }

        return normalized, meta

    def _normalize_sentence(self, sentence: str) -> str:
        """Lowercase and strip punctuation to a whitespace-separated string."""
        if not sentence:
            return ""
        lowered = sentence.lower()
        # Replace punctuation with spaces
        no_punct = re.sub(r"[^\w\s]", " ", lowered)
        collapsed = re.sub(r"\s+", " ", no_punct).strip()
        return collapsed

    def _tokenize(self, normalized_sentence: str) -> Tuple[List[str], Set[str]]:
        """Tokenize normalized sentence into list (ordered) and set (for inclusion)."""
        if not normalized_sentence:
            return [], set()
        tokens_list = normalized_sentence.split()
        return tokens_list, set(tokens_list)

    def _normalize_token_group(self, token_group: Any) -> List[str]:
        """
        Normalize a token group entry (string or list of strings) into a flat token list.
        """
        tokens: List[str] = []
        if isinstance(token_group, str):
            norm_group = self._normalize_sentence(token_group)
            tokens = norm_group.split()
        elif isinstance(token_group, list):
            for t in token_group:
                if isinstance(t, str):
                    norm_token = self._normalize_sentence(t)
                    tokens.extend(norm_token.split())
        # remove empties
        tokens = [t for t in tokens if t]
        return tokens

    def _matches_signals(
        self,
        normalized_sentence: str,
        sentence_tokens_list: List[str],
        sentence_tokens_set: Set[str],
        intent_key: str
    ) -> bool:
        """
        Check if the sentence matches configured signals for a given intent.

        Matching priority: ordered > all > any
        - ordered: tokens must appear in order (not necessarily adjacent)
        - all: all tokens present anywhere (order-independent)
        - any: word-boundary phrase match on normalized sentence (prevents substring matches)
        """
        signals = self.intent_signals.get(intent_key, {})

        # Ordered (strongest)
        for token_group in signals.get("ordered", []):
            if token_group and self._tokens_in_order(sentence_tokens_list, token_group):
                return True

        # Phrase (any) match
        for token_group in signals.get("all", []):
            if token_group and all(token in sentence_tokens_set for token in token_group):
                return True

        # Token-set (any) match - use word boundaries to avoid substring matches
        # Example: "book" should match "book haircut" but NOT "booking status"
        for phrase in signals.get("any", []):
            if phrase:
                # Use word boundaries to ensure whole-word/phrase matching
                # Escape special regex characters in the phrase
                escaped_phrase = re.escape(phrase)
                # Match as whole word(s) - word boundaries on both sides
                pattern = r'\b' + escaped_phrase + r'\b'
                if re.search(pattern, normalized_sentence, re.IGNORECASE):
                    return True

        return False

    def _tokens_in_order(self, sentence_tokens_list: List[str], token_group: List[str]) -> bool:
        """
        Check if all tokens in token_group appear in order within sentence_tokens_list.
        Tokens do not need to be adjacent.
        """
        if not token_group:
            return False

        pos = 0
        for token in token_group:
            try:
                idx = sentence_tokens_list.index(token, pos)
            except ValueError:
                return False
            pos = idx + 1
        return True

    def _slot_present(self, slot_name: str, entities: Dict[str, Any]) -> bool:
        """
        Map semantic intent slots to extracted entity presence.
        Currently supports:
        - services: business_categories or service_families present
        - service_id: same as services (proxy)
        - date: concrete date anchor
        - time: time or time window/duration
        - booking_id: extracted booking_id (if present in entities)
        """
        if slot_name == "services":
            return bool(entities.get("business_categories") or entities.get("service_families"))
        if slot_name == "service_id":
            return bool(entities.get("business_categories") or entities.get("service_families"))
        if slot_name == "date":
            return self._has_date_anchor(entities)
        if slot_name == "start_date":
            return self._has_date_anchor(entities)
        if slot_name == "end_date":
            return self._has_second_date_anchor(entities)
        if slot_name == "time":
            return bool(entities.get("times") or entities.get("time_windows") or entities.get("durations"))
        if slot_name == "booking_id":
            return bool(entities.get("booking_id"))
        return False

    def _has_date_anchor(self, entities: Dict[str, Any]) -> bool:
        """
        Determine if a concrete date anchor exists.
        Anchors:
        - Any absolute date
        - A single relative date that maps to a specific day (e.g., "tomorrow", "this friday")
        Non-anchors:
        - Multiple relative dates
        - Vague relative terms
        """
        if entities.get("dates_absolute"):
            return True
        dates = entities.get("dates") or []
        if not dates:
            return False
        if len(dates) != 1:
            return False
        date_text = str(dates[0].get("text", "")).strip().lower()
        if not date_text:
            return False
        vague_terms = {"next week", "sometime", "later", "soon"}
        if date_text in vague_terms:
            return False
        return True

    def _has_second_date_anchor(self, entities: Dict[str, Any]) -> bool:
        """
        Determine if a distinct end-date anchor exists.
        Rules:
        - Prefer absolute dates; require at least two absolute date entries.
        - If only relative dates are present, require at least two.
        - Otherwise, no end-date anchor.
        """
        dates_abs = entities.get("dates_absolute") or []
        if len(dates_abs) >= 2:
            return True
        dates = entities.get("dates") or []
        if len(dates) >= 2:
            return True
        return False

    def _build_response(self, intent: str, confidence: float, entities: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build structured response with clarification metadata.
        """
        meta = self.intent_meta.get(intent, {}) or {}
        intent_defining_slots = meta.get("intent_defining_slots") or []
        required_slots = meta.get("required_slots") or []

        missing_slots: List[str] = []

        # Intent confirmation: if defining slots exist, require at least one
        if intent_defining_slots:
            has_defining = any(self._slot_present(slot, entities)
                               for slot in intent_defining_slots)
            if not has_defining:
                missing_slots.extend(intent_defining_slots)

        # Required slots for execution
        for slot in required_slots:
            if not self._slot_present(slot, entities) and slot not in missing_slots:
                missing_slots.append(slot)

        status = "ready"
        if missing_slots:
            status = "needs_clarification"

        return {
            "intent": intent,
            "confidence": confidence,
            "status": status,
            "missing_slots": missing_slots
        }


def resolve_intent(
    osentence: str,
    entities: Dict[str, Any]
) -> Tuple[str, float]:
    """
    Convenience function for resolving intent.

    Creates a resolver instance and calls resolve_intent().

    Args:
        osentence: Original user sentence (lowercased)
        entities: Extraction output with service_families, dates, times, etc.

    Returns:
        Tuple of (intent, confidence_score)
    """
    resolver = ReservationIntentResolver()
    return resolver.resolve_intent(osentence, entities)
