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
import threading
from pathlib import Path
from typing import Tuple, Dict, Any, List, Set, Optional

import yaml

from ..config.core import STATUS_READY, STATUS_NEEDS_CLARIFICATION


# Canonical intents
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
CONFIRM_BOOKING = "CONFIRM_BOOKING"
PAYMENT_STATUS = "PAYMENT_STATUS"
REJECT_OR_CHANGE = "REJECT_OR_CHANGE"
UNKNOWN = "UNKNOWN"

# Confidence scores (heuristic)
HIGH_CONFIDENCE = 0.95
MEDIUM_CONFIDENCE = 0.85
LOW_CONFIDENCE = 0.75

# Module-level cache for intent signals (loaded once per process)
_intent_signals_cache: Optional[Dict[str, Dict[str, List[List[str]]]]] = None
_intent_meta_cache: Optional[Dict[str, Dict[str, Any]]] = None
_cache_lock = threading.Lock()


def _load_intent_signals_cached() -> Tuple[
    Dict[str, Dict[str, List[List[str]]]],
    Dict[str, Dict[str, Any]]
]:
    """
    Load intent signals from YAML file (cached at module level).
    
    Thread-safe lazy loading: loads once on first access, reuses cached data
    for subsequent calls. Zero YAML I/O on request path after initial load.
    """
    global _intent_signals_cache, _intent_meta_cache
    
    # Fast path: return cached data if already loaded
    if _intent_signals_cache is not None and _intent_meta_cache is not None:
        return _intent_signals_cache, _intent_meta_cache
    
    # Slow path: load and cache (thread-safe)
    with _cache_lock:
        # Double-check after acquiring lock (another thread may have loaded it)
        if _intent_signals_cache is not None and _intent_meta_cache is not None:
            return _intent_signals_cache, _intent_meta_cache
        
        # Load YAML file
        # Try config/data first, fallback to store/normalization for backward compatibility
        config_dir = Path(__file__).resolve().parent.parent / "config"
        config_data_path = config_dir / "data" / "intent_signals.yaml"
        store_path = config_dir.parent / "store" / "normalization" / "intent_signals.yaml"
        
        path = config_data_path if config_data_path.exists() else store_path
        
        if not path.exists():
            raise FileNotFoundError(
                f"intent_signals.yaml not found. Tried:\n"
                f"  - {config_data_path}\n"
                f"  - {store_path}\n"
                f"Please ensure intent_signals.yaml exists in one of these locations."
            )
        
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        
        intents_cfg = raw.get("intents", raw) if isinstance(raw, dict) else {}
        
        # Normalize signals and meta
        normalized: Dict[str, Dict[str, List[List[str]]]] = {}
        meta: Dict[str, Dict[str, Any]] = {}
        
        for intent, cfg in intents_cfg.items():
            if not isinstance(cfg, dict):
                continue
            
            signals_cfg = cfg.get("signals") or cfg.get("intent_signals") or cfg
            
            # Normalize "any" phrases
            any_phrases = []
            for phrase in signals_cfg.get("any", []) or []:
                if isinstance(phrase, str):
                    norm_phrase = _normalize_sentence_static(phrase)
                    if norm_phrase:
                        any_phrases.append(norm_phrase)
            
            # Normalize "all" token groups
            all_token_groups: List[List[str]] = []
            for token_group in signals_cfg.get("all", []) or []:
                tokens = _normalize_token_group_static(token_group)
                if tokens:
                    all_token_groups.append(tokens)
            
            # Normalize "ordered" token groups
            ordered_token_groups: List[List[str]] = []
            for token_group in signals_cfg.get("ordered", []) or []:
                tokens = _normalize_token_group_static(token_group)
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
        
        # Cache the results
        _intent_signals_cache = normalized
        _intent_meta_cache = meta
        
        return normalized, meta


def _normalize_sentence_static(sentence: str) -> str:
    """Static version of _normalize_sentence for module-level use."""
    if not sentence:
        return ""
    lowered = sentence.lower()
    no_punct = re.sub(r"[^\w\s]", " ", lowered)
    collapsed = re.sub(r"\s+", " ", no_punct).strip()
    return collapsed


def _normalize_token_group_static(token_group: Any) -> List[str]:
    """Static version of _normalize_token_group for module-level use."""
    tokens: List[str] = []
    if isinstance(token_group, str):
        norm_group = _normalize_sentence_static(token_group)
        tokens = norm_group.split()
    elif isinstance(token_group, list):
        for t in token_group:
            if isinstance(t, str):
                norm_token = _normalize_sentence_static(t)
                tokens.extend(norm_token.split())
    tokens = [t for t in tokens if t]
    return tokens


class ReservationIntentResolver:
    """
    Rule-based intent resolver for appointment/reservation booking.

    Uses ordered rules (first match wins) to determine user intent.
    Deterministic, explainable, and fast.
    """

    def __init__(self):
        """Initialize intent resolver with configuration-driven signals."""
        # Use cached intent signals (loaded once per process)
        self.intent_signals, self.intent_meta = _load_intent_signals_cached()

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
        booking_mode: str = "service",
        memory_state: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, float]:
        """
        Resolve intent from original user sentence and extracted entities.

        Intent resolution order (strict priority):
        0. MODIFY/CANCEL absolute override (terminal intent check)
           - If MODIFY signal detected → MODIFY_BOOKING (bypasses ALL other logic)
           - If CANCEL signal detected → CANCEL_BOOKING (bypasses ALL other logic)
           - NO service requirement, NO CREATE evaluation, NO BOOKING_INQUIRY fallback
        1. Lock booking intent by booking_mode (final, cannot be overridden)
           - booking_mode="service" → CREATE_APPOINTMENT
           - booking_mode="reservation" → CREATE_RESERVATION
        2. Apply intent_signals for non-CREATE intents (QUOTE, DISCOVERY, etc.)
        3. Slot-driven fallback: Consider locked booking intent if eligible
        4. Readiness determined by required_slots (ready vs needs_clarification)

        Args:
            osentence: Original user sentence (lowercased)
            entities: Extraction output with service_families, dates, times, etc.
            booking_mode: "service" (appointments) or "reservation" (reservations)
            memory_state: Optional memory state (not used for MODIFY/CANCEL overrides)

        Returns:
            Tuple of (intent, confidence_score)
            intent: One of the canonical intents or UNKNOWN
            confidence: Heuristic confidence score (0.75-0.95)
        """
        if not osentence:
            resp = self._build_response(UNKNOWN, LOW_CONFIDENCE, entities)
            return resp["intent"], resp["confidence"]

        normalized_sentence = self._normalize_sentence(osentence)
        sentence_tokens_list, sentence_tokens_set = self._tokenize(
            normalized_sentence)

        import logging
        logger = logging.getLogger(__name__)

        # ============================================================
        # STEP 0: MODIFY/CANCEL ABSOLUTE OVERRIDE (terminal intent check)
        # ============================================================
        # MODIFY_BOOKING and CANCEL_BOOKING are absolute overrides that bypass ALL other logic:
        # - MUST be checked FIRST, before service checks, slot validation, CREATE scoring
        # - DO NOT require service slot
        # - DO NOT fallback to BOOKING_INQUIRY
        # - DO NOT evaluate CREATE_APPOINTMENT / CREATE_RESERVATION
        # - Terminal intent classes: if signal detected, intent is determined immediately
        
        modify_signal_present = self._matches_signals(
            normalized_sentence, sentence_tokens_list, sentence_tokens_set, MODIFY_BOOKING
        )
        cancel_signal_present = self._matches_signals(
            normalized_sentence, sentence_tokens_list, sentence_tokens_set, CANCEL_BOOKING
        )
        
        # Enhanced MODIFY detection: check for MODIFY verbs with booking nouns (even without booking_id)
        # This handles cases like:
        # - "move booking" (should match even without booking_id - Case 87, 98)
        # - "modify reservtion RST012" (typo with booking_id - Case 95)
        if not modify_signal_present:
            booking_id_present = bool(entities.get("booking_id"))
            # Check for MODIFY verbs
            has_modify_verb = ("modify" in sentence_tokens_set or 
                              "update" in sentence_tokens_set or 
                              "change" in sentence_tokens_set or
                              "move" in sentence_tokens_set or
                              "reschedule" in sentence_tokens_set)
            # Check for booking nouns
            has_booking_noun = ("booking" in sentence_tokens_set or 
                               "reservation" in sentence_tokens_set or 
                               "appointment" in sentence_tokens_set)
            
            # If MODIFY verb + booking noun present, treat as MODIFY signal (NOT dependent on booking_id)
            # This ensures "move booking" matches even when booking_id is missing
            if has_modify_verb and has_booking_noun:
                modify_signal_present = True
                logger.info(
                    f"[INTENT_RESOLVER] MODIFY signal detected via verb + booking noun (booking_id-independent): "
                    f"tokens={sentence_tokens_set}, booking_id={entities.get('booking_id')}"
                )
            # Fallback: also check if modify token + booking_id exists (for typos like "modify reservtion RST012")
            elif has_modify_verb and booking_id_present:
                modify_signal_present = True
                logger.info(
                    f"[INTENT_RESOLVER] MODIFY signal detected via token + booking_id context: "
                    f"tokens={sentence_tokens_set}, booking_id={entities.get('booking_id')}"
                )
        
        # If MODIFY signal is detected, intent MUST be MODIFY_BOOKING (absolute override)
        if modify_signal_present:
            logger.info(
                f"[INTENT_RESOLVER] MODIFY signal detected - absolute override to MODIFY_BOOKING "
                f"(bypassing all CREATE logic, service checks, and slot validation)"
            )
            resp = self._build_response(MODIFY_BOOKING, HIGH_CONFIDENCE, entities)
            return resp["intent"], resp["confidence"]
        
        # If CANCEL signal is detected, intent MUST be CANCEL_BOOKING (absolute override)
        if cancel_signal_present:
            logger.info(
                f"[INTENT_RESOLVER] CANCEL signal detected - absolute override to CANCEL_BOOKING "
                f"(bypassing all CREATE logic, service checks, and slot validation)"
            )
            resp = self._build_response(CANCEL_BOOKING, HIGH_CONFIDENCE, entities)
            return resp["intent"], resp["confidence"]

        # ============================================================
        # STEP 1: DETERMINE LOCKED BOOKING INTENT (from booking_mode)
        # ============================================================
        # booking_mode is the sole determinant - locks the booking intent
        booking_mode_normalized = "reservation" if booking_mode == "reservation" else "service"
        locked_booking_intent = CREATE_APPOINTMENT if booking_mode_normalized == "service" else CREATE_RESERVATION
        
        logger.info(
            f"[INTENT_RESOLVER] resolve_intent called: booking_mode={booking_mode}, "
            f"booking_mode_normalized={booking_mode_normalized}, "
            f"locked_booking_intent={locked_booking_intent}"
        )

        # ============================================================
        # STEP 2: SIGNAL-FIRST SELECTION (excludes CREATE_* booking intents and MODIFY/CANCEL)
        # ============================================================
        # Evaluate intent signals for all intents EXCEPT:
        # - CREATE_APPOINTMENT/CREATE_RESERVATION (locked by booking_mode)
        # - MODIFY_BOOKING/CANCEL_BOOKING (already handled as absolute overrides in STEP 0)
        signal_matching_intents = []
        for intent_key in self.intent_signals.keys():
            # Skip CREATE_APPOINTMENT/CREATE_RESERVATION - locked by booking_mode
            if intent_key in [CREATE_APPOINTMENT, CREATE_RESERVATION]:
                continue
            # Skip MODIFY_BOOKING/CANCEL_BOOKING - already handled as absolute overrides
            if intent_key in [MODIFY_BOOKING, CANCEL_BOOKING]:
                continue
            
            if self._matches_signals(normalized_sentence, sentence_tokens_list, sentence_tokens_set, intent_key):
                signal_matching_intents.append(intent_key)

        # Apply lifecycle-based gating for other intents (MODIFY/CANCEL already handled)
        lifecycle = memory_state.get("booking_lifecycle", "NONE") if memory_state else "NONE"
        
        # Gating rule: CONFIRM_BOOKING only allowed if lifecycle is CREATING
        if CONFIRM_BOOKING in signal_matching_intents:
            if lifecycle != "CREATING":
                # Block CONFIRM_BOOKING - remove from candidates
                signal_matching_intents = [intent for intent in signal_matching_intents if intent != CONFIRM_BOOKING]
                logger.info(
                    f"[INTENT_RESOLVER] CONFIRM_BOOKING blocked: lifecycle={lifecycle} (requires CREATING)"
                )

        # If signals matched, select best matching intent (with priority ordering)
        if signal_matching_intents:
            selected_intent = self._select_best_signal_match(
                signal_matching_intents, normalized_sentence, sentence_tokens_list, sentence_tokens_set, entities
            )
            if selected_intent:
                resp = self._build_response(
                    selected_intent, HIGH_CONFIDENCE if len(signal_matching_intents) == 1 else MEDIUM_CONFIDENCE, entities
                )
                return resp["intent"], resp["confidence"]

        # ============================================================
        # STEP 3: SLOT-DRIVEN FALLBACK (locked booking intent only)
        # ============================================================
        # Consider only the locked booking intent (determined by booking_mode)
        # This step is only reached if no MODIFY/CANCEL signals were present
        meta = self.intent_meta.get(locked_booking_intent, {})
        intent_defining_slots = meta.get("intent_defining_slots", [])
        
        # Gating rule 3: CREATE_* intents only allowed if lifecycle != EXECUTED
        lifecycle = memory_state.get("booking_lifecycle", "NONE") if memory_state else "NONE"
        if lifecycle == "EXECUTED":
            # Block CREATE_* intents when lifecycle is EXECUTED
            logger.info(
                f"[INTENT_RESOLVER] {locked_booking_intent} blocked: lifecycle={lifecycle} (CREATE_* not allowed after EXECUTED)"
            )
            resp = self._build_response(UNKNOWN, LOW_CONFIDENCE, entities)
            return resp["intent"], resp["confidence"]
        
        # Check eligibility: all intent_defining_slots must be present
        if intent_defining_slots:
            all_slots_present = all(
                self._slot_present(slot, entities) for slot in intent_defining_slots
            )
            if all_slots_present:
                # Intent is eligible - select it
                resp = self._build_response(locked_booking_intent, MEDIUM_CONFIDENCE, entities)
                return resp["intent"], resp["confidence"]
        
        # If no defining slots or slots not present, return UNKNOWN
        # (booking intent cannot be activated without required defining slots)
        resp = self._build_response(UNKNOWN, LOW_CONFIDENCE, entities)
        return resp["intent"], resp["confidence"]


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

    def _select_best_signal_match(
        self,
        matching_intents: List[str],
        normalized_sentence: str,
        sentence_tokens_list: List[str],
        sentence_tokens_set: Set[str],
        entities: Dict[str, Any]
    ) -> Optional[str]:
        """
        Select the best matching intent from signal-matching intents.
        
        Uses priority ordering: destructive/sensitive → booking → informational
        """
        # Priority order (higher priority checked first)
        priority_order = [
            PAYMENT,
            CANCEL_BOOKING,
            MODIFY_BOOKING,
            BOOKING_INQUIRY,
            AVAILABILITY,
            CREATE_APPOINTMENT,
            CREATE_RESERVATION,
            QUOTE,
            DETAILS,
            DISCOVERY,
            RECOMMENDATION,
            CONFIRM_BOOKING,
            PAYMENT_STATUS,
            REJECT_OR_CHANGE,
        ]
        
        # Return first matching intent in priority order
        for intent in priority_order:
            if intent in matching_intents:
                return intent
        
        # If no priority match, return first in list
        return matching_intents[0] if matching_intents else None

    def _has_change_delta(self, entities: Dict[str, Any]) -> bool:
        """
        Check if at least one change delta exists in entities for MODIFY_BOOKING.
        
        Valid change deltas (from user requirement):
        - date: concrete date anchor (single date)
        - time: time, time_windows, or durations
        - date_range: date_range object (for reservations)
        - start_date: concrete date anchor
        - end_date: second date anchor
        - service_id: business_categories or service_families
        - duration: durations
        
        Args:
            entities: Extraction output with dates, times, services, etc.
            
        Returns:
            True if at least one change delta exists, False otherwise.
        """
        # Check date_range (may exist as a constructed entity for reservations)
        if entities.get("date_range"):
            return True
        
        # Check date-related deltas (date, start_date, end_date)
        if (self._has_date_anchor(entities) or 
            self._has_second_date_anchor(entities) or
            bool(entities.get("dates_absolute")) or
            bool(entities.get("dates"))):
            return True
        
        # Check time-related deltas (time)
        if (bool(entities.get("times")) or 
            bool(entities.get("time_windows"))):
            return True
        
        # Check duration delta
        if bool(entities.get("durations")):
            return True
        
        # Check service_id delta
        if (bool(entities.get("business_categories")) or 
            bool(entities.get("service_families")) or
            bool(entities.get("service_id"))):
            return True
        
        return False

    def _build_response(self, intent: str, confidence: float, entities: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build structured response with clarification metadata.
        
        Readiness determination: Uses required_slots to decide ready vs needs_clarification.
        Intent eligibility (intent_defining_slots) is already checked before calling this method.
        
        Special handling for MODIFY_BOOKING:
        - booking_id is mandatory
        - at least one change delta (date, time, service_id, duration, etc.) must exist
        - If booking_id missing → missing_slots = ["booking_id"]
        - Else if no change deltas → missing_slots = ["change"]
        - Else → READY
        """
        meta = self.intent_meta.get(intent, {}) or {}
        required_slots = meta.get("required_slots") or []

        missing_slots: List[str] = []

        # Special validation for MODIFY_BOOKING: requires booking_id + at least one change delta
        if intent == MODIFY_BOOKING:
            # Check booking_id first (mandatory)
            booking_id_present = self._slot_present("booking_id", entities)
            if not booking_id_present:
                missing_slots.append("booking_id")
            else:
                # booking_id present - check for at least one change delta
                has_change_delta = self._has_change_delta(entities)
                if not has_change_delta:
                    # booking_id exists but no change delta - need clarification on what to change
                    missing_slots.append("change")
        else:
            # Standard validation for all other intents
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
