"""
Fuzzy Entity Recovery

Recovers entities missed by EntityRuler using fuzzy string matching.
Useful for handling typos, misspellings, and variant phrasings.

Examples:
    - "air force ones" → matches "air force 1" (85% similarity)
    - "cocacola" → matches "coca-cola" (90% similarity)
    - "nigerian beans" → matches "brown beans" (synonym match)

Ported from semantics/fuzzy_search.py with enhancements.
"""
from typing import List, Dict, Set, Any
import re

# Optional dependency
try:
    from rapidfuzz import process, fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    process = None
    fuzz = None


class FuzzyEntityMatcher:
    """
    Fuzzy matcher for recovering entities missed by rule-based extraction.
    
    Uses rapidfuzz for fuzzy string matching to handle:
    - Typos and misspellings
    - Variant phrasings
    - Missing spaces or hyphens
    
    Performance:
    - Caches catalog maps at initialization
    - Configurable threshold (default 88%)
    - Skips meaningless spans (stopwords, punctuation)
    
    Example:
        >>> matcher = FuzzyEntityMatcher(entities, threshold=85)
        >>> results = matcher.recover_entities(doc, debug=True)
        >>> # [{"type": "product", "text": "air force 1", "span": (2, 5), "score": 92}]
    """
    
    # Stopwords to skip during n-gram generation
    STOPWORDS = {"and", "or", "to", "of", "for", "in", "the", "a"}
    
    def __init__(self, entities: List[Dict], threshold: int = 88):
        """
        Initialize fuzzy matcher with entity catalog.
        
        Args:
            entities: List of entity dicts from global catalog
            threshold: Minimum similarity score (0-100) for matches
        
        Raises:
            ImportError: If rapidfuzz is not installed
        """
        if not RAPIDFUZZ_AVAILABLE:
            raise ImportError(
                "rapidfuzz is required for fuzzy matching. "
                "Install with: pip install rapidfuzz"
            )
        
        self.entities = entities
        self.threshold = threshold
        
        # Build catalog maps (cached for performance)
        self.catalog_maps = self._build_catalog_maps()
    
    def _build_catalog_maps(self) -> Dict[str, Dict[str, str]]:
        """
        Build fuzzy lookup maps for each entity type.
        
        Returns:
            Dict mapping entity types to {term: canonical} lookups
        """
        catalog_maps = {
            "brand": {},
            "product": {},
            "variant": {},
            "unit": {}
        }
        
        for ent in self.entities:
            canonical = ent["canonical"].lower()
            types = ent.get("type", [])
            
            # Handle both list and string types
            if isinstance(types, str):
                types = [types]
            
            synonyms = [s.lower() for s in ent.get("synonyms", [])]
            all_terms = {canonical, *synonyms}
            
            for t in types:
                if t in catalog_maps:
                    for term in all_terms:
                        catalog_maps[t][term] = canonical
        
        return catalog_maps
    
    def recover_entities(
        self, 
        doc: Any,  # spaCy Doc object
        debug: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Recover entities from non-entity spans using fuzzy matching.
        
        Args:
            doc: spaCy Doc object (already processed)
            debug: Enable debug logging
        
        Returns:
            List of recovered entities with format:
            [{"type": str, "text": str, "span": (start, end), "score": int, "source": "fuzzy"}]
        """
        # === Step 1: Collect tokens not already marked as entities ===
        tokens = [t.text.lower() for t in doc]
        ent_spans = {(ent.start, ent.end) for ent in doc.ents}
        
        # Build index of token positions that belong to an entity
        occupied = set()
        for start, end in ent_spans:
            occupied.update(range(start, end))
        
        # === Step 2: Generate multi-word spans (n-grams) for non-entity tokens ===
        ngrams = self._generate_ngrams(tokens, occupied, debug)
        
        # === Step 3: Run fuzzy match against catalog ===
        results = self._fuzzy_match_ngrams(ngrams, debug)
        
        return results
    
    def _generate_ngrams(
        self, 
        tokens: List[str], 
        occupied: Set[int],
        debug: bool = False
    ) -> List[tuple]:
        """
        Generate n-gram candidates from non-entity tokens.
        
        Args:
            tokens: List of token strings
            occupied: Set of token indices already covered by entities
            debug: Enable debug logging
        
        Returns:
            List of (phrase, start_idx, end_idx) tuples
        """
        ngrams = []
        
        for n in range(2, 5):  # 2–4 word phrases only
            for i in range(len(tokens) - n + 1):
                # Skip if any token in span is already an entity
                if any((i + j) in occupied for j in range(n)):
                    continue
                
                span_tokens = tokens[i:i+n]
                
                # Skip punctuation-only or numeric-only phrases
                if all(re.fullmatch(r"[\W\d]+", t) for t in span_tokens):
                    continue
                
                phrase = " ".join(span_tokens)
                
                # Skip spans with internal stopwords (e.g., "rice and beans")
                if any(tok in self.STOPWORDS for tok in span_tokens[1:-1]):
                    continue
                
                # Skip if phrase is entirely stopwords
                if all(tok in self.STOPWORDS for tok in span_tokens):
                    continue
                
                ngrams.append((phrase, i, i+n))
        
        if debug:
            print(f"[FUZZY] Candidate n-grams: {len(ngrams)}")
        
        return ngrams
    
    def _fuzzy_match_ngrams(
        self, 
        ngrams: List[tuple],
        debug: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Fuzzy match n-grams against catalog.
        
        Args:
            ngrams: List of (phrase, start, end) tuples
            debug: Enable debug logging
        
        Returns:
            List of matched entities
        """
        results = []
        
        for phrase, start, end in ngrams:
            for label, cmap in self.catalog_maps.items():
                if not cmap:
                    continue
                
                # Ignore stopwords during matching for better accuracy
                cleaned_phrase = re.sub(
                    r"\b(and|or|to|of|for|in|the|a)\b", 
                    "", 
                    phrase
                ).strip()
                
                # Run fuzzy matching
                best_match = process.extractOne(
                    cleaned_phrase,
                    cmap.keys(),
                    scorer=fuzz.token_sort_ratio
                )
                
                if not best_match:
                    continue
                
                matched_text, score, _ = best_match
                
                if score >= self.threshold:
                    canonical = cmap[matched_text]
                    results.append({
                        "type": label,
                        "text": canonical,
                        "span": (start, end),
                        "score": score,
                        "source": "fuzzy"
                    })
                    
                    if debug:
                        print(f"[FUZZY] '{phrase}' → '{canonical}' ({score}%) [{label}]")
        
        return results


def fuzzy_recover_multiword_entities(
    doc: Any,
    entities: List[Dict],
    threshold: int = 88,
    debug: bool = False
) -> List[Dict[str, Any]]:
    """
    Fuzzy-recover multiword entities (products, brands, variants, units)
    that were not matched by the EntityRuler.
    
    Standalone function for backward compatibility.
    For better performance, use FuzzyEntityMatcher class directly.
    
    Args:
        doc: spaCy Doc object
        entities: List of entity dicts from global catalog
        threshold: Minimum similarity score (default 88)
        debug: Enable debug logging
    
    Returns:
        List of recovered entities
        
    NOTE: Matches semantics/fuzzy_search.py lines 4-106 exactly
    """
    matcher = FuzzyEntityMatcher(entities, threshold)
    return matcher.recover_entities(doc, debug)

