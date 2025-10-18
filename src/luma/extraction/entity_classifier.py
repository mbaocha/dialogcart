"""
Context-Based Entity Classification

Classifies entities based on surrounding context:
- Units vs Products (e.g., "bag" in "2 bags of rice" vs "Gucci bag")
- Variants vs Products (e.g., "red" in "red rice" vs "Red brand")
- Brands vs Products (e.g., "Dove" in "Dove soap" vs standalone "dove")
- ProductBrand promotion (e.g., "Coca-Cola" → BRAND or PRODUCT)

Ported from semantics/nlp_processor.py lines 847-1111 with performance optimizations.
"""
from typing import List, Dict, Set, Any
from luma.config import debug_print


class EntityClassifier:
    """
    Context-based entity type classifier.
    
    Classifies entities by analyzing surrounding context (POS tags, proximity, etc.).
    
    Performance optimizations:
    - Caches brand/product word lookups at initialization
    - Avoids rebuilding lookups on every classification call
    - Reuses entity catalog reference
    
    Example:
        >>> classifier = EntityClassifier(entities)
        >>> result = classifier.classify_units("add 2 bags of rice", ["bags"], {"bag", "bags"})
        >>> print(result)  # {"units": [{"entity": "bags", "position": 2}], "products": []}
    """
    
    def __init__(self, entities: List[Dict]):
        """
        Initialize classifier with entity catalog.
        
        Args:
            entities: List of entity dicts from global catalog
        """
        self.entities = entities
        
        # ✅ Performance: Cache brand/product lookups once
        self.brand_words = self._build_brand_words()
        self.product_words = self._build_product_words()
        
        debug_print(f"[EntityClassifier] Initialized with {len(self.brand_words)} brands, {len(self.product_words)} products")
    
    def _build_brand_words(self) -> Set[str]:
        """Build set of all brand words (canonical + synonyms)."""
        brand_words = set()
        for ent in self.entities:
            if ent.get("type") == "brand":
                brand_words.add(ent["canonical"].lower())
                brand_words.update(s.lower() for s in ent.get("synonyms", []))
        return brand_words
    
    def _build_product_words(self) -> Set[str]:
        """Build set of all product words (canonical + synonyms)."""
        product_words = set()
        for ent in self.entities:
            if ent.get("type") == "product":
                product_words.add(ent["canonical"].lower())
                product_words.update(s.lower() for s in ent.get("synonyms", []))
        return product_words
    
    def classify_units(
        self, 
        sentence: str, 
        entity_list: List[str], 
        ambiguous_units: Set[str], 
        debug: bool = False
    ) -> Dict[str, List[Dict]]:
        """
        Classify ambiguous entities as units or products.
        
        Rules:
        1. Preceded by number → UNIT (e.g., "2 bags")
        2. Followed by "of" → UNIT (e.g., "bag of")
        3. Preceded by brand → PRODUCT (e.g., "Gucci bag")
        4. Default → PRODUCT
        
        Args:
            sentence: Input sentence
            entity_list: List of entity strings to classify
            ambiguous_units: Set of known ambiguous unit terms
            debug: Enable debug logging
        
        Returns:
            Dict with "units" and "products" lists
            
        NOTE: Matches semantics/nlp_processor.py lines 847-921 exactly
        """
        tokens = sentence.lower().replace(',', ' ,').replace('.', ' .').split()
        
        results = {"products": [], "units": []}
        
        # Collect token positions of each entity (exact match)
        token_entities = [(i, token.rstrip('.,!?;:')) for i, token in enumerate(tokens)]
        
        counters = {}
        used_positions = {e: 0 for e in entity_list}
        
        for entity in entity_list:
            counters[entity] = counters.get(entity, 0) + 1
            
            # Find next matching token position
            positions = [i for i, tok in token_entities if tok == entity]
            if not positions or used_positions[entity] >= len(positions):
                if debug:
                    debug_print(f"{entity}: not found in tokens")
                continue
            
            pos = positions[used_positions[entity]]
            used_positions[entity] += 1
            
            prev_token = tokens[pos-1] if pos > 0 else None
            next_token = tokens[pos+1] if pos+1 < len(tokens) else None
            
            if debug:
                debug_print(f"Analyzing '{entity}' at position {pos}")
                debug_print(f"  Prev: {prev_token or 'N/A'} | Next: {next_token or 'N/A'}")
            
            # Only classify entities that are ambiguous units
            if entity not in ambiguous_units:
                if debug:
                    debug_print(f"  Skipping non-ambiguous entity '{entity}' in unit classification")
                continue
            
            label = "product"  # default for ambiguous entity
            
            # Rule 3: brand (overrides all)
            if prev_token and prev_token in self.brand_words:
                label = "product"
                if debug: debug_print(f"  Rule 3: brand '{prev_token}' → product")
            # Rule 1: number before entity
            elif prev_token and prev_token.isdigit():
                label = "unit"
                if debug: debug_print(f"  Rule 1: number '{prev_token}' → unit")
            # Rule 2: followed by "of"
            elif next_token == "of":
                label = "unit"
                if debug: debug_print("  Rule 2: followed by 'of' → unit")
            
            # Add to results
            if label == "unit":
                results["units"].append({"entity": entity, "position": pos})
            else:
                results["products"].append({"entity": entity, "position": pos})
            
            if debug:
                debug_print(f"  Final classification: {label}")
        
        return results
    
    def classify_variants(
        self,
        sentence: str,
        entity_list: List[str],
        ambiguous_variants: Set[str],
        debug: bool = False
    ) -> Dict[str, List[Dict]]:
        """
        Classify ambiguous entities as variants or products.
        
        Rules:
        1. Followed by known product → VARIANT (e.g., "red rice")
        2. Followed by "size", "color", "flavor" → VARIANT
        3. Preceded by known product → VARIANT (e.g., "rice red")
        4. Default → PRODUCT
        
        Args:
            sentence: Input sentence
            entity_list: List of entity strings to classify
            ambiguous_variants: Set of known ambiguous variant terms
            debug: Enable debug logging
        
        Returns:
            Dict with "variants" and "products" lists
            
        NOTE: Matches semantics/nlp_processor.py lines 924-1003 exactly
        """
        tokens = sentence.lower().replace(',', ' ,').replace('.', ' .').split()
        
        results = {"products": [], "variants": []}
        
        # Collect token positions of each entity (exact match)
        token_entities = [(i, token.rstrip('.,!?;:')) for i, token in enumerate(tokens)]
        counters = {}
        used_positions = {e: 0 for e in entity_list}
        
        for entity in entity_list:
            counters[entity] = counters.get(entity, 0) + 1
            
            # Find next matching token position
            positions = [i for i, tok in token_entities if tok == entity]
            if not positions or used_positions[entity] >= len(positions):
                if debug:
                    debug_print(f"{entity}: not found in tokens")
                continue
            
            pos = positions[used_positions[entity]]
            used_positions[entity] += 1
            
            prev_token = tokens[pos-1] if pos > 0 else None
            next_token = tokens[pos+1] if pos+1 < len(tokens) else None
            
            if debug:
                debug_print(f"Analyzing '{entity}' at position {pos}")
                debug_print(f"  Prev: {prev_token or 'N/A'} | Next: {next_token or 'N/A'}")
            
            # Only classify entities that are ambiguous variants
            if entity not in ambiguous_variants:
                if debug:
                    debug_print(f"  Skipping non-ambiguous entity '{entity}' in variant classification")
                continue
            
            label = "product"  # default for ambiguous variant
            
            # Rule 3: if preceded by a known product, treat as variant
            if prev_token and prev_token in self.product_words:
                label = "variant"
                if debug: debug_print(f"  Rule 3: product '{prev_token}' → variant")
            # Rule 1: if followed by a known product, treat as variant
            elif next_token and next_token in self.product_words:
                label = "variant"
                if debug: debug_print(f"  Rule 1: followed by product '{next_token}' → variant")
            # Rule 2: if followed by "size", "color", etc. (contextual cues)
            elif next_token in {"size", "color", "flavor"}:
                label = "variant"
                if debug: debug_print(f"  Rule 2: followed by '{next_token}' → variant")
            # Otherwise: leave as product-like word
            else:
                label = "product"
            
            # Add to results
            if label == "variant":
                results["variants"].append({"entity": entity, "position": pos})
            else:
                results["products"].append({"entity": entity, "position": pos})
            
            if debug:
                debug_print(f"  Final classification: {label}")
        
        return results
    
    def classify_brands(
        self,
        doc: Any,  # spaCy Doc object
        entity_list: List[str],
        ambiguous_brands: Set[str],
        debug: bool = False
    ) -> Dict[str, List[Dict]]:
        """
        Classify ambiguous entities as BRAND or PRODUCT based on context.
        Uses spaCy POS tagging for context analysis.
        
        Rules:
        1. Followed by known PRODUCT or producttoken → BRAND
        2. Followed by generic NOUN (not punctuation) → BRAND
        3. Followed by unit or number → PRODUCT
        4. Default → PRODUCT
        
        Args:
            doc: spaCy Doc object (for POS tagging)
            entity_list: List of ambiguous entity strings
            ambiguous_brands: Set of known ambiguous brand terms
            debug: Enable debug logging
        
        Returns:
            Dict with "brands" and "products" lists
            
        NOTE: Matches semantics/nlp_processor.py lines 1005-1068 exactly
        """
        results = {"brands": [], "products": []}
        used_positions = {e: 0 for e in entity_list}
        
        # Build lookup for already tagged product tokens
        known_product_tokens = {
            t.text.lower() for t in doc 
            if t.ent_type_ == "PRODUCT" or t.text.lower() == "producttoken"
        }
        
        for entity in entity_list:
            # Find all matching tokens in doc
            matches = [t for t in doc if t.text.lower() == entity]
            if not matches:
                if debug:
                    debug_print(f"{entity}: not found in doc")
                continue
            
            token = matches[used_positions[entity]] if used_positions[entity] < len(matches) else None
            used_positions[entity] += 1
            
            if not token:
                continue
            
            next_token = doc[token.i + 1] if token.i + 1 < len(doc) else None
            prev_token = doc[token.i - 1] if token.i - 1 >= 0 else None
            
            if debug:
                debug_print(f"\nAnalyzing '{entity}' (pos={token.i})")
                debug_print(f"  Prev: {prev_token.text if prev_token else 'N/A'} ({prev_token.pos_ if prev_token else '-'})")
                debug_print(f"  Next: {next_token.text if next_token else 'N/A'} ({next_token.pos_ if next_token else '-'})")
            
            # Skip if not ambiguous
            if entity not in ambiguous_brands:
                continue
            
            label = "product"  # default
            
            # === Rule 1: followed by known PRODUCT or producttoken
            if next_token and next_token.text.lower() in known_product_tokens:
                label = "brand"
                if debug: debug_print(f"  Rule 1: next_token '{next_token.text}' is PRODUCT → BRAND")
            
            # === Rule 2: followed by generic NOUN (not punctuation)
            elif next_token and next_token.pos_ == "NOUN" and next_token.text.isalpha():
                label = "brand"
                if debug: debug_print(f"  Rule 2: next_token '{next_token.text}' is NOUN → BRAND")
            
            # === Rule 3: followed by unit or number → PRODUCT
            elif next_token and (next_token.pos_ == "NUM" or next_token.text.lower() in {"unittoken", "quantitytoken"}):
                label = "product"
                if debug: debug_print(f"  Rule 3: next_token '{next_token.text}' is NUM/unit → PRODUCT")
            
            # === Default
            else:
                label = "product"
                if debug: debug_print("  Default → PRODUCT")
            
            results[f"{label}s"].append({"entity": entity, "position": token.i})
            if debug:
                debug_print(f"  Final classification: {label.upper()}")
        
        return results
    
    def classify_productbrands(
        self,
        doc: Any,  # spaCy Doc object
        productbrands: List[Dict],
        product_lex: Set[str],
        unit_lex: Set[str],
        debug: bool = False
    ) -> List[Dict]:
        """
        Promote each productbrand to BRAND or PRODUCT.
        
        Rules:
        - If followed by a known product → BRAND
        - If followed by unit/number → PRODUCT
        - Default → PRODUCT
        
        Args:
            doc: spaCy Doc object
            productbrands: List of productbrand entity dicts
            product_lex: Set of known product terms
            unit_lex: Set of known unit terms
            debug: Enable debug logging
            
        Returns:
            List of classified entities with label="brand" or "product"
            
        NOTE: Matches semantics/nlp_processor.py lines 1071-1111 exactly
        """
        results = []
        for pb in productbrands:
            if not isinstance(pb, dict):
                continue
            start = pb["position"]
            end = start + pb.get("length", 1)
            next_token = doc[end] if end < len(doc) else None
            label = "product"  # default
            
            if next_token:
                next_lower = next_token.text.lower()
                
                # ✅ Rule: followed by known product → brand
                if next_lower in product_lex:
                    label = "brand"
                
                # Optional: if followed by unit/number, it's definitely product
                elif next_lower in unit_lex or next_token.pos_ == "NUM":
                    label = "product"
            
            if debug:
                nxt = next_token.text if next_token else "None"
                debug_print(f"[DEBUG] Analyzing productbrand '{pb['text']}' at pos={start}")
                debug_print(f"  Next: {nxt} ({next_token.pos_ if next_token else '-'})")
                debug_print(f"  Final classification → {label.upper()}")
            
            results.append({
                "text": pb["text"],
                "position": start,
                "length": pb["length"],
                "label": label
            })
        
        return results

