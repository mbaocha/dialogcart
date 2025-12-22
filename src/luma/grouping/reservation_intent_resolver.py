"""
Reservation Intent Resolver

Rule-based intent resolution for service/appointment/reservation booking.
Replaces legacy ML-based intent mapping with deterministic, explainable rules.

Determines user intent from:
- Extracted entities (services, dates, times, durations)
- Lexical cues (cancel, reschedule, etc.)

NO ML. NO embeddings. NO NER dependency.
"""
import re
from typing import Tuple, Dict, Any, List


# Canonical intents (locked - 10 production intents)
DISCOVERY = "DISCOVERY"
DETAILS = "DETAILS"
AVAILABILITY = "AVAILABILITY"
QUOTE = "QUOTE"
RECOMMENDATION = "RECOMMENDATION"
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
        """Initialize intent resolver with rule patterns."""
        # Payment patterns (destructive - check first)
        self.payment_patterns = [
            r"\bpay\b",
            r"\bpayment\b",
            r"\bdeposit\b",
            r"\bbalance\b",
            r"\brefund\b",
            r"\bpay for\b",
            r"\bpay my\b",
            r"\bhow much do i owe\b",
            r"\boutstanding\b"
        ]

        # Cancel booking patterns (destructive - check early)
        self.cancel_booking_patterns = [
            r"\bcancel\b",
            r"\bcancel\b.*\bbooking\b",
            r"\bcancel\b.*\bappointment\b",
            r"\bcancel my\b",
            r"\bdelete\b.*\bbooking\b",
            r"\bdelete\b.*\bappointment\b",
            r"\bcan't make it\b",
            r"\bcannot make it\b",
            r"\bwon't make it\b",
            r"\bwill not make it\b",
            r"\bcall off\b"
        ]

        # Modify booking patterns (destructive - check early)
        self.modify_booking_patterns = [
            r"\breschedule\b",
            r"\bmove\b.*\bbooking\b",
            r"\bmove\b.*\bappointment\b",
            r"\bchange time\b",
            r"\bchange date\b",
            r"\bpostpone\b",
            r"\bchange\b.*\bappointment\b",
            r"\breschedule my\b",
            r"\bupdate\b.*\bbooking\b",
            r"\bmodify\b.*\bbooking\b",
            r"\bupdate\b.*\bappointment\b",
            r"\bmodify\b.*\bappointment\b"
        ]

        # Booking inquiry patterns (read-only questions about existing booking)
        self.booking_inquiry_patterns = [
            r"\bmy appointment\b",
            r"\bmy booking\b",
            r"\bshow appointment\b",
            r"\bview appointment\b",
            r"\bcheck appointment\b",
            r"\bwhen is my\b",
            r"\bwhat time is my\b",
            r"\bstatus of my\b",
            r"\bconfirmation\b"
        ]

        # Availability patterns (more specific to avoid matching discovery)
        self.availability_patterns = [
            r"\bavailable\b",
            r"\bavailability\b",
            r"\bany slots\b",
            r"\bare you free\b",
            r"\bdo you have.*available\b",
            r"\bis there.*available\b",
            r"\bare there.*available\b",
            r"\bcan i get.*available\b",
            r"\bwhen can.*book\b",
            r"\bwhen can.*schedule\b",
            r"\bwhat times.*available\b",
            r"\bwhat.*slots\b"
        ]

        # Details patterns (asking about attributes: price, duration, inclusions, policies)
        self.details_patterns = [
            r"\bdoes.*include\b",
            r"\bwhat.*include\b",
            r"\bhow long\b",
            r"\bduration\b",
            r"\bpolicy\b",
            r"\bpolicies\b",
            r"\bcancellation\b",
            r"\bwhat.*come with\b",
            r"\bwhat.*included\b",
            r"\baddress\b",
            r"\blocation\b",
            r"\bhours\b",
            r"\bopen\b",
            r"\bclose\b",
            r"\bcontact\b",
            r"\bphone\b",
            r"\bemail\b"
        ]

        # Quote patterns (price/cost + service/room, contextual pricing)
        self.quote_patterns = [
            r"\bhow much\b",
            r"\bwhat.*price\b",
            r"\bwhat.*cost\b",
            r"\bprice for\b",
            r"\bcost for\b",
            r"\bhow much.*cost\b",
            r"\bquote\b",
            r"\bestimate\b"
        ]

        # Recommendation patterns (suggestions, guidance)
        self.recommendation_patterns = [
            r"\brecommend\b",
            r"\bsuggest\b",
            r"\bbest option\b",
            r"\bhelp me choose\b",
            r"\bwhat.*recommend\b",
            r"\bwhat.*suggest\b",
            r"\bwhich.*better\b",
            r"\bwhat.*best\b"
        ]

        # Booking verbs (for CREATE_BOOKING)
        self.booking_verbs = {
            "book", "schedule", "reserve", "appointment", "appoint",
            "set", "arrange", "plan", "make"
        }

        # Discovery question words (what do you offer, what services)
        self.discovery_patterns = [
            r"\bwhat.*offer\b",
            r"\bwhat.*services\b",
            r"\bwhat.*rooms\b",
            r"\bwhat.*packages\b",
            r"\bwhat do you have\b",
            r"\bwhat.*available\b"
        ]

    def resolve_intent(
        self,
        osentence: str,
        entities: Dict[str, Any]
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
            return UNKNOWN, LOW_CONFIDENCE

        osentence_lower = osentence.lower().strip()

        # Extract entity counts (entity-driven rules first)
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

        # ============================================================
        # DESTRUCTIVE/SENSITIVE INTENTS (check first for safety)
        # ============================================================

        # Rule 1: PAYMENT (destructive - check first)
        if self._matches_patterns(osentence_lower, self.payment_patterns):
            return PAYMENT, HIGH_CONFIDENCE

        # Rule 2: CANCEL_BOOKING (destructive - check early)
        if self._matches_patterns(osentence_lower, self.cancel_booking_patterns):
            return CANCEL_BOOKING, HIGH_CONFIDENCE

        # Rule 3: MODIFY_BOOKING (destructive - check early)
        if self._matches_patterns(osentence_lower, self.modify_booking_patterns):
            return MODIFY_BOOKING, HIGH_CONFIDENCE

        # ============================================================
        # BOOKING-RELATED INTENTS
        # ============================================================

        # Rule 4: CREATE_BOOKING (entity-driven: services + time constraints)
        if has_services and has_time_constraints:
            # If services + time constraints exist, it's a booking request
            # Even if it's a question like "Can I get...", it's still CREATE_BOOKING
            # Only exclude if it's clearly an availability question
            is_availability_question = self._matches_patterns(
                osentence_lower, self.availability_patterns)
            if not is_availability_question:
                return CREATE_BOOKING, HIGH_CONFIDENCE

        # Fallback rule: booking verbs + at least one of date/time/duration
        # SAFETY: Only permit fallback if at least some booking-relevant info exists (conservative, explainable)
        has_booking_verb = any(
            verb in osentence_lower for verb in self.booking_verbs)
        if not has_services and has_booking_verb and (has_dates or has_times or has_durations):
            # This fallback enables CREATE_BOOKING intent for utterances like:
            # 'I want to book a full body massage this Friday at 4pm',
            # even if 'full body massage' fails extraction, since booking context is clear.
            return CREATE_BOOKING, MEDIUM_CONFIDENCE

        # Rule 5: BOOKING_INQUIRY (questions about existing booking - check before availability)
        if self._matches_patterns(osentence_lower, self.booking_inquiry_patterns):
            return BOOKING_INQUIRY, HIGH_CONFIDENCE

        # Rule 6: AVAILABILITY (availability language + date/time OR just availability language)
        if self._matches_patterns(osentence_lower, self.availability_patterns):
            if has_time_constraints:
                return AVAILABILITY, HIGH_CONFIDENCE
            elif has_services:
                return AVAILABILITY, MEDIUM_CONFIDENCE
            else:
                # Pure availability check without entities
                return AVAILABILITY, MEDIUM_CONFIDENCE

        # ============================================================
        # INFORMATIONAL INTENTS
        # ============================================================

        # Rule 7: DETAILS (questions about service attributes)
        if self._matches_patterns(osentence_lower, self.details_patterns):
            if has_services:
                return DETAILS, HIGH_CONFIDENCE
            else:
                return DETAILS, MEDIUM_CONFIDENCE

        # Rule 8: QUOTE (price/cost + service)
        if self._matches_patterns(osentence_lower, self.quote_patterns):
            if has_services:
                return QUOTE, HIGH_CONFIDENCE
            else:
                return QUOTE, MEDIUM_CONFIDENCE

        # Rule 9: DISCOVERY (what services/rooms exist - services but no time constraints)
        if has_services and not has_time_constraints:
            # Check if it's a discovery question
            if self._matches_patterns(osentence_lower, self.discovery_patterns):
                return DISCOVERY, HIGH_CONFIDENCE
            # Or if no booking verbs, likely discovery
            has_booking_verb = any(
                verb in osentence_lower for verb in self.booking_verbs
            )
            if not has_booking_verb:
                return DISCOVERY, MEDIUM_CONFIDENCE

        # Rule 10: RECOMMENDATION (suggestions, guidance)
        if self._matches_patterns(osentence_lower, self.recommendation_patterns):
            return RECOMMENDATION, MEDIUM_CONFIDENCE

        # ============================================================
        # FALLBACK
        # ============================================================

        # Rule 11: UNKNOWN
        return UNKNOWN, LOW_CONFIDENCE

    def _matches_patterns(self, sentence: str, patterns: List[str]) -> bool:
        """Check if sentence matches any of the given regex patterns."""
        for pattern in patterns:
            if re.search(pattern, sentence, re.IGNORECASE):
                return True
        return False

    def _is_question(self, sentence: str) -> bool:
        """Check if sentence is a question (ends with ? or contains question words)."""
        if sentence.strip().endswith("?"):
            return True
        question_words = {"what", "when",
                          "where", "who", "why", "how", "which"}
        return any(word in sentence.lower() for word in question_words)


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
