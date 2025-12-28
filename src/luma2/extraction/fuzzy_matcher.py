"""
Tenant-scoped fuzzy recovery for services and reservations.

Used ONLY as a fallback when EntityRuler misses a grounded entity.
Matches multi-word phrases (2–4 tokens) using fuzzy string matching.
"""

from typing import List, Dict, Any, Set, Tuple
import re

try:
    from rapidfuzz import process, fuzz
except ImportError:
    process = None
    fuzz = None


# Stopwords allowed ONLY inside phrases (never at boundaries)
STOPWORDS = {"and", "or", "to", "of", "for", "in", "the", "a"}


class TenantFuzzyMatcher:
    """
    Fuzzy matcher for tenant services / reservations.

    Matches:
    - "hair kut" → "haircut"
    - "double rom" → "double room"
    - "airport pick up" → "airport pickup"

    Does NOT match:
    - "hair" → "haircut"
    - "airport" → "airport pickup"
    """

    def __init__(
        self,
        entity_map: Dict[str, List[str]],
        threshold: int = 88
    ):
        """
        Args:
            entity_map:
                {
                    "service": ["haircut", "beard trim"],
                    "room_type": ["double room", "suite"],
                    "amenity": ["airport pickup", "breakfast"]
                }
            threshold: fuzzy score cutoff
        """
        if process is None or fuzz is None:
            raise ImportError("rapidfuzz required: pip install rapidfuzz")

        self.entity_map = {
            k: [v.lower() for v in values]
            for k, values in entity_map.items()
        }
        self.threshold = threshold

        # Precompute single-token entities only (safe for typo recovery)
        self.single_token_entities = {
            entity_type: [v for v in values if " " not in v]
            for entity_type, values in self.entity_map.items()
        }

    # -----------------------------------------------------
    # N-gram generation (STRICT)
    # -----------------------------------------------------

    def _generate_ngrams(
        self,
        tokens: List[str],
        occupied: Set[int]
    ) -> List[Tuple[int, int, str]]:
        """
        Generate ONLY multi-word spans (2–4 tokens).

        Skips:
        - occupied tokens
        - punctuation / numeric spans
        - stopwords at boundaries
        """
        ngrams = []
        n_tokens = len(tokens)

        for n in range(2, 5):  # 🔒 2–4 tokens ONLY
            for start in range(n_tokens - n + 1):
                end = start + n

                if any(i in occupied for i in range(start, end)):
                    continue

                span_tokens = tokens[start:end]

                # Skip junk
                if all(re.fullmatch(r"[\W\d]+", t) for t in span_tokens):
                    continue

                # No stopwords at boundaries
                if span_tokens[0] in STOPWORDS or span_tokens[-1] in STOPWORDS:
                    continue

                # Internal stopwords allowed
                phrase = " ".join(span_tokens)
                ngrams.append((start, end, phrase))

        return ngrams

    # -----------------------------------------------------
    # Fuzzy recovery
    # -----------------------------------------------------

    def recover(
        self,
        tokens: List[str],
        occupied_positions: Set[int],
        debug: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Recover entities missed by EntityRuler.

        Returns:
            [
              {
                "type": "service",
                "text": "haircut",
                "start": 2,
                "end": 4,
                "score": 92,
                "source": "fuzzy"
              }
            ]
        """
        recovered: List[Dict[str, Any]] = []
        matched_spans: Set[Tuple[int, int]] = set()

        ngrams = self._generate_ngrams(tokens, occupied_positions)

        # 🔑 Longest span first
        ngrams.sort(key=lambda x: x[1] - x[0], reverse=True)

        for start, end, phrase in ngrams:
            if any(
                not (end <= s or start >= e)
                for s, e in matched_spans
            ):
                continue

            best = None

            for entity_type, values in self.entity_map.items():
                match = process.extractOne(
                    phrase,
                    values,
                    scorer=fuzz.token_sort_ratio
                )

                if not match:
                    continue

                text, score, _ = match

                if score >= self.threshold:
                    if best is None or score > best["score"]:
                        best = {
                            "type": entity_type,
                            "text": text,
                            "start": start,
                            "end": end,
                            "score": int(score),
                            "source": "fuzzy"
                        }

            if best:
                recovered.append(best)
                matched_spans.add((start, end))

                if debug:
                    span_text = " ".join(tokens[start:end])
                    print(
                        f"[FUZZY] '{span_text}' → {best['text']} ({best['score']}%)"
                    )

        # --------------------------------------------------
        # Pass 2: single-token typo recovery (SAFE MODE)
        # --------------------------------------------------
        for i, token in enumerate(tokens):
            if i in occupied_positions:
                continue

            if not token.isalpha() or len(token) < 4:
                continue

            # Skip stopwords (prevents matching "want", "the", etc.)
            if token.lower() in STOPWORDS:
                continue

            # Skip if already matched by phrase recovery
            if any(start <= i < end for start, end in matched_spans):
                continue

            best_score = 0
            best_entity = None
            best_type = None

            for entity_type, values in self.single_token_entities.items():
                if not values:
                    continue

                # Use ratio for single-token matching (better for single-word typos)
                match = process.extractOne(
                    token.lower(),
                    values,
                    scorer=fuzz.ratio
                )

                if not match:
                    continue

                text, score, _ = match
                if score > best_score:
                    best_score = score
                    best_entity = text
                    best_type = entity_type

            if debug and best_score > 0:
                print(
                    f"[FUZZY-SINGLE] '{token}' → {best_entity} (score: {best_score:.1f}%, threshold: 85)")

            # HIGH threshold to avoid semantic drift (85 for single-token typos)
            # Single-character typos like "hairkut" → "haircut" typically score 85-92%
            if best_score >= 85:
                recovered.append({
                    "type": best_type,
                    "text": best_entity,
                    "start": i,
                    "end": i + 1,
                    "score": int(best_score),
                    "source": "fuzzy_single"
                })

                matched_spans.add((i, i + 1))

                if debug:
                    print(
                        f"[FUZZY-SINGLE] ACCEPTED '{token}' → {best_entity} ({best_score}%)")

        return recovered


if __name__ == "__main__":
    import sys

    if process is None or fuzz is None:
        print("Error: rapidfuzz not installed. Install with: pip install rapidfuzz")
        sys.exit(1)

    # -------------------------------------------------
    # Test entity map (natural language only)
    # -------------------------------------------------
    entity_map = {
        "service": [
            "haircut",
            "hair trim",
            "beard trim"
        ],
        "room_type": [
            "double room",
            "suite"
        ],
        "amenity": [
            "airport pickup",
            "breakfast"
        ]
    }

    matcher = TenantFuzzyMatcher(entity_map, threshold=88)

    print("=" * 60)
    print("TenantFuzzyMatcher – Interactive Test")
    print("=" * 60)
    print("Type a sentence and press Enter.")
    print("Type 'quit' to exit.")
    print()

    while True:
        try:
            sentence = input("> ").strip().lower()
        except KeyboardInterrupt:
            print("\nExiting.")
            break

        if not sentence:
            continue

        if sentence in {"quit", "exit"}:
            break

        # -------------------------------------------------
        # Light tokenization (matches your pipeline style)
        # -------------------------------------------------
        tokens = sentence.split()

        # Simulate EntityRuler finding nothing
        occupied_positions = set()

        print("\nInput sentence:")
        print(f"  {sentence}")

        print("\nRecovered entities:")
        results = matcher.recover(tokens, occupied_positions, debug=True)

        if not results:
            print("  (none)")
        else:
            for r in results:
                span_text = " ".join(tokens[r["start"]:r["end"]])
                print(
                    f"  [{r['start']}:{r['end']}] "
                    f"'{span_text}' → {r['type']} = '{r['text']}' "
                    f"({r['score']}%)"
                )

        print("-" * 60)
