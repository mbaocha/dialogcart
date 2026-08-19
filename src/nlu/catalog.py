"""
Catalog resolver: resolve a user's service_term to an alias key.

LLM job  → extract the raw service phrase ("premiun haircut", "premium", "haircut")
Code job → fuzzy-match that phrase against the alias catalog

Public API:
    resolve_service(
        service_term, aliases,
        prior_text=None, awaiting_service_id=False, candidate_keys=None,
    )
        → {"service_id": str | None, "service_candidates": list[str]}
"""
import difflib
import re
from typing import Dict, List, Optional, Sequence

# Date/time tokens to strip from service terms before scoring.
# Copied from pipeline._DATE_TIME_TOKENS to avoid a circular import.
_WEEKDAYS = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
})
_MONTHS = frozenset({
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
})
_NOISE_TOKENS = _WEEKDAYS | _MONTHS | frozenset({
    "today", "tomorrow", "yesterday", "next", "this", "last", "weekend",
    "morning", "afternoon", "evening", "night", "noon", "midnight", "am", "pm",
})

# Minimum character-level similarity to treat two tokens as a match.
_FUZZY_THRESHOLD = 0.80

_CATALOG_PREFIX_NOISE = _NOISE_TOKENS | frozenset({
    "a", "an", "the", "to", "for", "me", "my", "i", "we", "you",
    "yes", "yeah", "yep", "no", "want", "need", "please", "another",
    "new", "book", "booking", "appointment", "appointments", "schedule",
    "reserve", "service", "services",
})
_MIN_NON_EXACT_PREFIX_CHARACTERS = 3


def _tokenize(s: str) -> List[str]:
    """Lowercase, strip punctuation, return tokens."""
    return re.sub(r"[^\w\s]", " ", s.lower()).split()


def eligible_catalog_reference(
    service_term: str, candidate_keys: Sequence[str]
) -> bool:
    """Whether a phrase may enter deterministic catalogue matching.

    Exact aliases remain eligible regardless of length. Non-exact references
    require at least one non-noise token and three normalized characters.
    """
    tokens = _tokenize(service_term or "")
    if not tokens:
        return False
    normalized = " ".join(tokens)
    if any(" ".join(_tokenize(key)) == normalized for key in candidate_keys):
        return True
    meaningful = [token for token in tokens if token not in _CATALOG_PREFIX_NOISE]
    return bool(meaningful) and sum(len(token) for token in meaningful) >= (
        _MIN_NON_EXACT_PREFIX_CHARACTERS
    )


def _term_coverage(service_tokens: List[str], alias_tokens: List[str]) -> float:
    """
    Fraction of service_tokens that are 'explained' by alias_tokens.

    A service token is explained when at least one alias token has
    character-level similarity ≥ _FUZZY_THRESHOLD.

    Term-side coverage prevents shorter aliases from scoring higher than
    longer ones for partial terms.  Bare "haircut" covers 100 % of its
    tokens against both "premium haircut" and "flexi haircut + pruning",
    correctly producing a tie rather than favouring the shorter alias.
    """
    if not service_tokens or not alias_tokens:
        return 0.0
    explained = sum(
        1 for svc_tok in service_tokens
        if any(
            difflib.SequenceMatcher(None, svc_tok, alias_tok).ratio() >= _FUZZY_THRESHOLD
            for alias_tok in alias_tokens
        )
    )
    return explained / len(service_tokens)


def _try_pick_from_candidate_list(
    service_term: str, candidate_keys: List[str]
) -> Optional[str]:
    """Resolve a disambiguation reply against the active candidate list only.

    Handles short replies like "premium" when the bot offered
    "premium haircut" vs "flexi haircut + pruning".
    """
    if not service_term or not candidate_keys:
        return None

    term_norm = " ".join(_tokenize(service_term))
    if not term_norm:
        return None

    # Exact normalized key match
    for key in candidate_keys:
        if " ".join(_tokenize(key)) == term_norm:
            return key

    if not eligible_catalog_reference(service_term, candidate_keys):
        return None

    # Unique prefix: "premium" → "premium haircut"
    prefix_hits = [k for k in candidate_keys if k.lower().startswith(term_norm)]
    if len(prefix_hits) == 1:
        return prefix_hits[0]

    # Single-token reply contained in exactly one candidate ("flexi", "premium", …)
    term_tokens = _tokenize(service_term)
    if len(term_tokens) == 1:
        tok = term_tokens[0]
        containing = [k for k in candidate_keys if tok in _tokenize(k)]
        if len(containing) == 1:
            return containing[0]

    return None


def unique_prefix_catalog_pick(
    service_term: str, candidate_keys: List[str]
) -> Optional[str]:
    """Return a catalog key when a spoken phrase uniquely prefixes one alias.

    Used for informal replies such as ``premium``, ``premium trim``, or
    ``flexi haircut``. Contained generic tokens (``room`` in ``King Room``)
    are not enough — that is the unsafe ``spaceship room`` case.
    """
    if not service_term or not candidate_keys:
        return None
    tokens = _tokenize(service_term)
    if not tokens:
        return None
    normalized = " ".join(tokens)
    for key in candidate_keys:
        if " ".join(_tokenize(key)) == normalized:
            return key
    if not eligible_catalog_reference(service_term, candidate_keys):
        return None
    phrases: List[str] = []
    for width in range(len(tokens), 0, -1):
        for index in range(len(tokens) - width + 1):
            phrases.append(" ".join(tokens[index : index + width]))
    found: List[str] = []
    for phrase in phrases:
        hits = [
            key
            for key in candidate_keys
            if " ".join(_tokenize(key)).startswith(phrase)
        ]
        if len(hits) == 1:
            found.append(hits[0])
    unique = list(dict.fromkeys(found))
    if len(unique) == 1:
        return unique[0]
    return None


_NEGATION_PREFIX_TOKENS = frozenset({"not", "no", "never", "without", "except"})
_QUESTION_MARK = "?"


def spoken_unique_catalog_mention(
    text: str, catalog_phrases: Sequence[str]
) -> Optional[str]:
    """Return the spoken token window that uniquely prefixes one catalog phrase.

    Recovers informal mentions such as ``flexi haircut`` or ``premium`` only when
    the current utterance uniquely identifies one catalog item. Negated windows
    (``not premium``) and interrogatives are left unassigned. The returned value
    is the spoken subset, never an unspoken catalog label.
    """
    utterance = (text or "").strip()
    phrases = [str(phrase) for phrase in catalog_phrases if str(phrase).strip()]
    if not utterance or not phrases or _QUESTION_MARK in utterance:
        return None
    picked = unique_prefix_catalog_pick(utterance, phrases)
    if not picked:
        return None
    tokens = _tokenize(utterance)
    if not tokens:
        return None
    picked_norm = " ".join(_tokenize(picked))
    for width in range(len(tokens), 0, -1):
        for index in range(len(tokens) - width + 1):
            if index > 0 and tokens[index - 1] in _NEGATION_PREFIX_TOKENS:
                continue
            spoken = " ".join(tokens[index : index + width])
            if not picked_norm.startswith(spoken):
                continue
            other_hits = [
                key
                for key in phrases
                if " ".join(_tokenize(key)) != picked_norm
                and " ".join(_tokenize(key)).startswith(spoken)
            ]
            if other_hits:
                continue
            return spoken
    return None


def infer_service_term_from_utterance(
    text: str,
    aliases: Dict[str, str],
) -> Optional[str]:
    """Recover an explicit catalog service mention from the current utterance.

    Used when Stage 2 omits ``service_term`` so sticky ``resolved_service_id``
    does not replace an utterance-evidenced service (e.g. prior Premium,
    current \"show availability for flexi\").
    """
    if not text or not aliases:
        return None

    keys = list(aliases.keys())
    tokens = _tokenize(text)
    if not tokens:
        return None

    # Prefer longer windows so \"flexi haircut\" beats a lone ambiguous token.
    found: List[str] = []
    for width in range(min(len(tokens), 4), 0, -1):
        for i in range(len(tokens) - width + 1):
            phrase = " ".join(tokens[i : i + width])
            picked = _try_pick_from_candidate_list(phrase, keys)
            if picked:
                found.append(picked)
        if found:
            break

    unique = list(dict.fromkeys(found))
    if len(unique) == 1:
        return unique[0]
    return None


def resolve_service(
    service_term: Optional[str],
    aliases: Dict[str, str],
    prior_text: Optional[str] = None,
    awaiting_service_id: bool = False,
    candidate_keys: Optional[List[str]] = None,
    resolved_service_id: Optional[str] = None,
) -> Dict:
    """
    Resolve service_term to a catalog service_id or a candidates list.

    Args:
        service_term:        Raw LLM-extracted phrase ("premiun haircut", "premium",
                             "haircut").  None → no service mentioned.
        aliases:             Alias key → service ID map from tenant_context.
        prior_text:          Concatenated prior-turn user text.  Used only when
                             awaiting_service_id is True (slot-fill continuation).
        awaiting_service_id: True when session missing_slots contains "service_id" —
                             prior text may help narrow an ambiguous short term.
        candidate_keys: When set, user is picking from a narrowed bot list — score
                        only these aliases and skip full-thread prior_text merging.
        resolved_service_id: Service already chosen in the active booking session.
                        When service_term is null (date/time-only follow-up), reuse this
                        instead of returning the full catalog as candidates.

    Returns:
        {"service_id": str | None, "service_candidates": list[str]}
    """
    if not aliases:
        return {"service_id": None, "service_candidates": []}

    active_keys = (
        [k for k in candidate_keys if k in aliases]
        if candidate_keys
        else list(aliases.keys())
    )
    if candidate_keys and not active_keys:
        active_keys = list(candidate_keys)

    if not service_term:
        if resolved_service_id:
            return {"service_id": resolved_service_id, "service_candidates": []}
        return {
            "service_id": None,
            "service_candidates": active_keys or list(aliases.keys()),
        }

    if not eligible_catalog_reference(service_term, active_keys):
        return {"service_id": None, "service_candidates": []}

    if candidate_keys:
        picked = _try_pick_from_candidate_list(service_term, active_keys)
        if picked and picked in aliases:
            return {"service_id": picked, "service_candidates": []}

    # When slot-filling without an active candidate list, prepend prior turns so
    # "premium" after "haircut" resolves to "premium haircut". Skip when candidate_keys
    # is set — prior thread noise (e.g. stale "flexi") must not affect list picks.
    use_prior = awaiting_service_id and prior_text and not candidate_keys
    combined = f"{prior_text} {service_term}" if use_prior else service_term

    tokens = [
        t for t in _tokenize(combined)
        if t not in _NOISE_TOKENS
    ]

    if not tokens:
        return {
            "service_id": None,
            "service_candidates": active_keys or list(aliases.keys()),
        }

    # Fast path: exact normalized key match (handles "premium haircut" → "premium haircut"
    # without going through fuzzy scoring, and avoids ties with aliases that contain
    # the same tokens as a substring, e.g. "massage" vs "swedish massage").
    joined = " ".join(tokens)
    for alias_key in active_keys:
        if alias_key in aliases and " ".join(_tokenize(alias_key)) == joined:
            return {"service_id": alias_key, "service_candidates": []}

    # Unique prefix pick: "premium", "premium trim", "flexi haircut".
    picked = unique_prefix_catalog_pick(
        service_term, [key for key in active_keys if key in aliases]
    )
    if picked and picked in aliases:
        return {"service_id": picked, "service_candidates": []}

    # Score each alias by what fraction of the combined service tokens it explains.
    score_keys = [k for k in active_keys if k in aliases]
    scores = {
        key: _term_coverage(tokens, _tokenize(key))
        for key in score_keys
    }

    if not scores:
        return {"service_id": None, "service_candidates": []}

    max_score = max(scores.values())

    if max_score == 0.0:
        # No alias tokens fuzzy-matched anything → service not in catalog
        return {"service_id": None, "service_candidates": []}

    top = [k for k, s in scores.items() if s == max_score]

    if len(top) == 1:
        return {"service_id": top[0], "service_candidates": []}

    # Multiple aliases at the same score → ambiguous → return candidates
    return {"service_id": None, "service_candidates": top}
