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

from ..config.core import STATUS_READY, STATUS_NEEDS_CLARIFICATION


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

        Enforces booking_mode as the sole determinant for CREATE_APPOINTMENT vs CREATE_RESERVATION.

        Rules (in order):
        1. Determine booking_mode first (from service taxonomy / tenant config)
           - Possible values: "service", "reservation"
        2. Lock booking intent by booking_mode:
           - booking_mode = "service" → intent = CREATE_APPOINTMENT
           - booking_mode = "reservation" → intent = CREATE_RESERVATION
           - This decision is final and cannot be overridden
        3. Apply intent_signals only for non-booking intents:
           - QUOTE, DISCOVERY, DETAILS, MODIFY, CANCEL, etc.
           - Signals may not override booking_mode
        4. Readiness determination:
           - Use required_slots only to decide: ready vs needs_clarification
           - Appointment → requires time
           - Reservation → requires date_range (end_date)

        Args:
            osentence: Original user sentence (lowercased)
            entities: Extraction output with service_families, dates, times, etc.
            booking_mode: "service" or "reservation" (sole determinant for CREATE_APPOINTMENT vs CREATE_RESERVATION)

        Returns:
            Tuple of (intent, confidence_score)
            intent: One of the canonical intents or UNKNOWN
            confidence: Heuristic confidence score (0.75-0.95)
        """
        if not osentence:
            resp = self._build_response(UNKNOWN, LOW_CONFIDENCE, entities)
            return resp["intent"], resp["confidence"]

        normalized_sentence = self._normalize_sentence(osentence)
        sentence_tokens_list, sentence_tokens_set = self._tokenize(normalized_sentence)

        # Extract entity counts
        service_families = entities.get("business_categories") or entities.get("service_families", [])
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

        # Normalize booking_mode
        booking_mode_normalized = "reservation" if booking_mode == "reservation" else "service"

        # ============================================================
        # STEP 1: CHECK NON-BOOKING INTENTS VIA SIGNALS
        # ============================================================
        # Evaluate intent_signals for non-booking intents only
        # (CREATE_APPOINTMENT and CREATE_RESERVATION are excluded from signal matching)
        non_booking_intents = [
            PAYMENT, CANCEL_BOOKING, MODIFY_BOOKING, BOOKING_INQUIRY,
            AVAILABILITY, DETAILS, QUOTE, DISCOVERY, RECOMMENDATION
        ]
        
        signal_matching_intents = []
        for intent_key in non_booking_intents:
            if self._matches_signals(normalized_sentence, sentence_tokens_list, sentence_tokens_set, intent_key):
                signal_matching_intents.append(intent_key)

        # If signals match for non-booking intents, select the best one
        if signal_matching_intents:
            selected_intent = self._select_best_intent(
                signal_matching_intents, normalized_sentence, sentence_tokens_list,
                sentence_tokens_set, entities, has_services, has_time_constraints,
                booking_mode_normalized
            )
            resp = self._build_response(selected_intent[0], selected_intent[1], entities)
            return resp["intent"], resp["confidence"]

        # ============================================================
        # STEP 2: DETERMINE CREATE INTENT BY BOOKING_MODE
        # ============================================================
        # Lock booking intent by booking_mode (final decision, cannot be overridden)
        if booking_mode_normalized == "service":
            create_intent = CREATE_APPOINTMENT
        else:
            create_intent = CREATE_RESERVATION

        confidence = HIGH_CONFIDENCE if has_services and has_time_constraints else MEDIUM_CONFIDENCE
        resp = self._build_response(create_intent, confidence, entities)
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
                "is_booking": cfg.get("is_booking", False),
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

    def _select_best_intent(
        self,
        signal_matching_intents: List[str],
        normalized_sentence: str,
        sentence_tokens_list: List[str],
        sentence_tokens_set: Set[str],
        entities: Dict[str, Any],
        has_services: bool,
        has_time_constraints: bool,
        booking_mode: str
    ) -> Tuple[str, float]:
        """
        Select the best intent from signal-matching intents.
        Uses priority ordering: destructive/sensitive → booking → informational
        
        Note: CREATE_APPOINTMENT and CREATE_RESERVATION are excluded from signal matching.
        They are determined directly by booking_mode.
        """
        # Priority 1: Destructive/sensitive (check first)
        for intent in [PAYMENT, CANCEL_BOOKING, MODIFY_BOOKING]:
            if intent in signal_matching_intents:
                return (intent, HIGH_CONFIDENCE)

        # Priority 2: Booking-related
        # BOOKING_INQUIRY
        if BOOKING_INQUIRY in signal_matching_intents:
            return (BOOKING_INQUIRY, HIGH_CONFIDENCE)

        # AVAILABILITY
        if AVAILABILITY in signal_matching_intents:
            if has_time_constraints:
                return (AVAILABILITY, HIGH_CONFIDENCE)
            elif has_services:
                return (AVAILABILITY, MEDIUM_CONFIDENCE)
            else:
                return (AVAILABILITY, MEDIUM_CONFIDENCE)

        # Priority 3: Informational
        # DETAILS
        if DETAILS in signal_matching_intents:
            confidence = HIGH_CONFIDENCE if has_services else MEDIUM_CONFIDENCE
            return (DETAILS, confidence)

        # QUOTE
        if QUOTE in signal_matching_intents:
            confidence = HIGH_CONFIDENCE if has_services else MEDIUM_CONFIDENCE
            return (QUOTE, confidence)

        # DISCOVERY
        if DISCOVERY in signal_matching_intents:
            confidence = HIGH_CONFIDENCE if has_services else MEDIUM_CONFIDENCE
            return (DISCOVERY, confidence)

        # RECOMMENDATION
        if RECOMMENDATION in signal_matching_intents:
            return (RECOMMENDATION, MEDIUM_CONFIDENCE)

        # Fallback: return first matching intent (should not happen, but safe)
        return (signal_matching_intents[0], MEDIUM_CONFIDENCE)

    def _is_intent_compatible(self, intent_key: str, booking_mode: str) -> bool:
        """
        Check if intent is compatible with booking_mode.
        - CREATE_APPOINTMENT is compatible with "service" mode
        - CREATE_RESERVATION is compatible with "reservation" mode
        - Other booking intents are compatible with both modes
        """
        if intent_key == CREATE_APPOINTMENT:
            return booking_mode == "service"
        elif intent_key == CREATE_RESERVATION:
            return booking_mode == "reservation"
        else:
            # Other booking intents (CANCEL_BOOKING, MODIFY_BOOKING, BOOKING_INQUIRY) are compatible with both
            return True

    def _all_slots_present(self, slots: List[str], entities: Dict[str, Any]) -> bool:
        """
        Check if all slots in the list are present in entities.
        Returns True if slots list is empty (no requirement).
        """
        if not slots:
            return True
        return all(self._slot_present(slot, entities) for slot in slots)

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
        
        Readiness determination: After intent selection, evaluate required_slots.
        - If all present → status = ready
        - If any missing → status = needs_clarification
        required_slots must NOT block intent selection (only affects readiness).
        """
        meta = self.intent_meta.get(intent, {}) or {}
        required_slots = meta.get("required_slots") or []

        missing_slots: List[str] = []

        # Required slots for execution (readiness determination only)
        for slot in required_slots:
            if not self._slot_present(slot, entities):
                missing_slots.append(slot)

        status = STATUS_READY
        if missing_slots:
            status = STATUS_NEEDS_CLARIFICATION

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
