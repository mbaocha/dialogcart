"""
Syntactic Date-Time Pairing Detection

Detects explicit grammatical binding between dates and times in text.
Only emits date_time_pairs[] when grammar is explicit (e.g., "on March 3rd at 3pm").

Rules:
- Emit date_time_pairs[] ONLY when grammar is explicit
- Otherwise, emit dates[] and times[] independently
- Do NOT infer ownership, resolve ambiguity, or promote readiness
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def detect_date_time_pairs(
    text: str, entities: Dict[str, List], doc: Optional[Any] = None
) -> List[Dict[str, str]]:
    """
    Detect explicit syntactic date-time pairs in text.

    Only pairs dates and times when grammar is explicit:
    - "on March 3rd at 3pm" (preposition + "at")
    - "March 3rd, 3pm" (comma-separated)
    - "March 3rd at 3pm" ("at" between date and time)
    - "tomorrow at 3pm" (relative date + "at")
    - "on tomorrow at 3pm" (preposition + relative date + "at")

    Does NOT infer pairing when dates and times are mentioned separately
    without explicit grammatical binding (e.g., "March 3rd and April 8th at 14:00").

    Args:
        text: Original input text
        entities: Extracted entities dict with dates, dates_absolute, times
        doc: Optional spaCy doc (not currently used, reserved for future enhancement)

    Returns:
        List of date_time_pair dicts with "date" and "time" keys, or empty list if no explicit pairs
    """
    if not text or not entities:
        return []

    text_lower = text.lower()

    # Get all date and time entities with their positions
    date_entities = []
    time_entities = []

    # Collect relative dates
    for date_ent in entities.get("dates", []):
        if isinstance(date_ent, dict):
            date_text = date_ent.get("text", "")
            position = date_ent.get("position", 0)
            date_entities.append(
                {
                    "text": date_text,
                    "position": position,
                    "normalized": None,  # Will be filled later
                }
            )

    # Collect absolute dates
    for date_ent in entities.get("dates_absolute", []):
        if isinstance(date_ent, dict):
            date_text = date_ent.get("text", "")
            position = date_ent.get("position", 0)
            date_entities.append(
                {
                    "text": date_text,
                    "position": position,
                    "normalized": None,  # Will be filled later
                }
            )

    # Collect times
    for time_ent in entities.get("times", []):
        if isinstance(time_ent, dict):
            time_text = time_ent.get("text", "")
            position = time_ent.get("position", 0)
            time_entities.append(
                {
                    "text": time_text,
                    "position": position,
                    "normalized": None,  # Will be filled later
                }
            )

    if not date_entities or not time_entities:
        # No dates or no times - cannot form pairs
        return []

    # Pattern 1: "on <date> at <time>" or "on <date>, <time>"
    # Pattern 2: "<date> at <time>"
    # Pattern 3: "<date>, <time>"
    # Pattern 4: "<date> <time>" (adjacent, only if very explicit like "march 3 3pm")

    pairs = []
    used_date_indices = set()
    used_time_indices = set()

    # Pattern 1: "on <date> at <time>" or "on <date>, <time>"
    on_at_pattern = re.compile(
        r"\b(?:on\s+)?(.+?)\s+(?:at|,)\s+(.+?)(?:\s|$|,|\.)", re.IGNORECASE
    )

    for match in on_at_pattern.finditer(text_lower):
        date_part = match.group(1).strip()
        time_part = match.group(2).strip()

        # Find matching date and time entities
        for date_idx, date_ent in enumerate(date_entities):
            if date_idx in used_date_indices:
                continue
            date_text_lower = date_ent["text"].lower()
            # Check if date_part contains or matches date_text
            if date_text_lower in date_part or date_part in date_text_lower:
                for time_idx, time_ent in enumerate(time_entities):
                    if time_idx in used_time_indices:
                        continue
                    time_text_lower = time_ent["text"].lower()
                    # Check if time_part contains or matches time_text
                    if time_text_lower in time_part or time_part in time_text_lower:
                        pairs.append(
                            {
                                "date": date_ent["text"],
                                "time": time_ent["text"],
                                "date_index": date_idx,
                                "time_index": time_idx,
                            }
                        )
                        used_date_indices.add(date_idx)
                        used_time_indices.add(time_idx)
                        break
                if date_idx in used_date_indices:
                    break

    # Pattern 2: "<date> at <time>" (without "on")
    # This catches cases like "March 3rd at 3pm" or "tomorrow at 3pm"
    date_at_time_pattern = re.compile(
        r"\b([a-z]+(?:\s+\d+(?:st|nd|rd|th)?)?|tomorrow|today|next\s+\w+)\s+at\s+(\d+(?::\d+)?\s*(?:am|pm)|morning|afternoon|evening)",
        re.IGNORECASE,
    )

    for match in date_at_time_pattern.finditer(text_lower):
        date_part = match.group(1).strip()
        time_part = match.group(2).strip()

        # Find matching date and time entities
        for date_idx, date_ent in enumerate(date_entities):
            if date_idx in used_date_indices:
                continue
            date_text_lower = date_ent["text"].lower()
            if date_text_lower in date_part or date_part in date_text_lower:
                for time_idx, time_ent in enumerate(time_entities):
                    if time_idx in used_time_indices:
                        continue
                    time_text_lower = time_ent["text"].lower()
                    if time_text_lower in time_part or time_part in time_text_lower:
                        pairs.append(
                            {
                                "date": date_ent["text"],
                                "time": time_ent["text"],
                                "date_index": date_idx,
                                "time_index": time_idx,
                            }
                        )
                        used_date_indices.add(date_idx)
                        used_time_indices.add(time_idx)
                        break
                if date_idx in used_date_indices:
                    break

    # Pattern 3: "<date>, <time>" (comma-separated, e.g., "March 3rd, 3pm")
    # Only match if comma directly connects date and time without other words
    date_comma_time_pattern = re.compile(
        r"\b([a-z]+(?:\s+\d+(?:st|nd|rd|th)?)?|tomorrow|today|next\s+\w+)\s*,\s*(\d+(?::\d+)?\s*(?:am|pm))",
        re.IGNORECASE,
    )

    for match in date_comma_time_pattern.finditer(text_lower):
        date_part = match.group(1).strip()
        time_part = match.group(2).strip()

        # Find matching date and time entities
        for date_idx, date_ent in enumerate(date_entities):
            if date_idx in used_date_indices:
                continue
            date_text_lower = date_ent["text"].lower()
            if date_text_lower in date_part or date_part in date_text_lower:
                for time_idx, time_ent in enumerate(time_entities):
                    if time_idx in used_time_indices:
                        continue
                    time_text_lower = time_ent["text"].lower()
                    if time_text_lower in time_part or time_part in time_text_lower:
                        pairs.append(
                            {
                                "date": date_ent["text"],
                                "time": time_ent["text"],
                                "date_index": date_idx,
                                "time_index": time_idx,
                            }
                        )
                        used_date_indices.add(date_idx)
                        used_time_indices.add(time_idx)
                        break
                if date_idx in used_date_indices:
                    break

    logger.debug(
        f"[DATE_TIME_PAIRING] Detected {len(pairs)} explicit pairs in text: {text}",
        extra={"text": text, "pairs": pairs},
    )

    return pairs


def normalize_date_time_pairs(
    pairs: List[Dict[str, str]],
    date_normalizer: Optional[Any] = None,
    time_normalizer: Optional[Any] = None,
    now: Optional[Any] = None,
    tz: Optional[Any] = None,
) -> List[Dict[str, str]]:
    """
    Normalize date-time pairs by converting raw date/time strings to ISO format.

    Args:
        pairs: List of date_time_pair dicts with "date" and "time" keys (raw strings)
        date_normalizer: Function to normalize date strings (e.g., _bind_single_date)
        time_normalizer: Function to normalize time strings (e.g., bind_times)
        now: Current datetime for relative date resolution
        tz: Timezone object

    Returns:
        List of normalized date_time_pair dicts with "date" (YYYY-MM-DD) and "time" (HH:MM)
    """
    if not pairs:
        return []

    normalized_pairs = []

    for pair in pairs:
        date_str = pair.get("date", "")
        time_str = pair.get("time", "")

        normalized_date = None
        normalized_time = None

        # Normalize date if normalizer provided
        if date_normalizer and date_str and now and tz:
            try:
                bound_date = date_normalizer(date_str, now, tz)
                if bound_date:
                    normalized_date = bound_date.strftime("%Y-%m-%d")
            except Exception as e:
                logger.debug(
                    f"[DATE_TIME_PAIRING] Date normalization failed: {e}",
                    extra={"date_str": date_str, "error": str(e)},
                )

        # Normalize time if normalizer provided
        if time_normalizer and time_str and now and tz:
            try:
                # time_normalizer signature depends on implementation
                # Try common patterns: (time_refs, time_mode, now, tz) or (time_refs, time_mode, now, tz, **kwargs)
                if callable(time_normalizer):
                    # Check if it accepts keyword arguments
                    import inspect

                    sig = inspect.signature(time_normalizer)
                    params = list(sig.parameters.keys())
                    if len(params) >= 4:
                        # Try calling with positional args first
                        time_result = time_normalizer([time_str], "exact", now, tz)
                        if time_result and isinstance(time_result, dict):
                            normalized_time = time_result.get("start_time")
                            # Extract HH:MM if full time string
                            if normalized_time and ":" in normalized_time:
                                normalized_time = ":".join(
                                    normalized_time.split(":")[:2]
                                )
            except Exception as e:
                logger.debug(
                    f"[DATE_TIME_PAIRING] Time normalization failed: {e}",
                    extra={"time_str": time_str, "error": str(e)},
                )

        if normalized_date or normalized_time:
            normalized_pairs.append(
                {
                    "date": normalized_date or date_str,
                    "time": normalized_time or time_str,
                }
            )

    return normalized_pairs
