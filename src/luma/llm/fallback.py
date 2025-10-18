"""
LLM-based entity extraction fallback.

Uses OpenAI GPT models to extract entities when rule-based methods fail
or produce ambiguous results. Handles spelling corrections and complex patterns.
"""
import json
import time
from typing import Optional

from luma.data_types import ExtractionResult, EntityGroup, ProcessingStatus, GroupingResult

# Lazy import for optional dependency
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    OpenAI = None


class LLMExtractor:
    """
    LLM-based entity extractor using OpenAI.
    
    Provides fallback extraction when rule-based methods fail.
    Handles:
    - Spelling corrections
    - Ambiguous entity resolution
    - Complex multi-intent sentences
    - Out-of-vocabulary products
    """
    
    def __init__(self, model: str = "gpt-4o-mini", api_key: Optional[str] = None):
        """
        Initialize LLM extractor.
        
        Args:
            model: OpenAI model to use (default: gpt-4o-mini)
            api_key: Optional API key (uses OPENAI_API_KEY env var if not provided)
        """
        if not OPENAI_AVAILABLE:
            raise ImportError(
                "OpenAI package required for LLM extraction. "
                "Install with: pip install openai"
            )
        
        self.model = model
        self.client = OpenAI(api_key=api_key) if api_key else OpenAI()
    
    def create_prompt(self, sentence: str) -> str:
        """
        Create extraction prompt for the LLM.
        
        Args:
            sentence: User input sentence
            
        Returns:
            Formatted prompt string
        """
        return f"""You extract e-commerce intents and entities from user messages related to
shopping, cart actions, or product availability.

USER: "{sentence}"

ENTITY TYPES:
- products: item names
- brands: brand names
- quantities: numbers only (2, 5, 10)
- units: measurement or count words (kg, g, bottles, packs, pieces, etc.)
- variants: attributes (color, size, shade, style)
- ordinals: positional references (first, second, item 1, number 2, last)

BRAND RULE:
If a brand name appears alone ("Do you sell Coca-Cola?"), treat it as a product.
If it modifies a noun ("Coca-Cola soda", "Nike shoes"), tag brand separately and noun as product.

CART & PRODUCT INTENTS:
- add → adding or buying new items or increasing quantity
- remove → taking items out or decreasing quantity
- set → changing a product's quantity or variant
- clear → emptying the entire cart
- get → viewing or checking cart contents
- check_product_existence → asking whether a product is available or sold
- none → message unrelated to these intents (e.g., checkout, delivery, greetings, support)

OUTPUT FORMAT:
{{
  "status": "success|error|no_entities_found",
  "reason": "",
  "groups": [
    {{
      "intent": "add|remove|set|clear|get|check_product_existence|none",
      "action": "user verb or phrase (e.g., 'add', 'check out')",
      "products": ["..."],
      "quantities": ["..."],
      "units": ["..."],
      "brands": ["..."],
      "variants": ["..."],
      "ordinal_ref": "first|1|last|null"
    }}
  ],
  "notes": []
}}

RULES:
- Always include at least one group.
- Quantities = numbers only; units = measurement words.
- Lowercase all values.
- If multiple intents appear, create multiple groups.
- If out of scope, use intent = "none" and fill 'action' with the literal phrase.
- Do not resolve pronouns ("it", "them") - leave products empty.
- For ordinal references ("add the first one", "add item 1"), set ordinal_ref and leave products empty.

SPELLING CORRECTION:
- If a product or brand name appears misspelled but the intended word is clear (e.g., "cheicken" → "chicken"), fix it.
- If you are not highly confident of the correction, leave the original spelling unchanged.
- Always record any corrections you make in the "notes" field (e.g., "corrected 'cheicken' → 'chicken'").
"""
    
    def extract(self, sentence: str, debug: bool = False) -> ExtractionResult:
        """
        Extract entities using LLM.
        
        Args:
            sentence: Input sentence
            debug: Enable debug logging
            
        Returns:
            ExtractionResult with extracted entities
        """
        prompt = self.create_prompt(sentence)
        start_time = time.time()
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a structured JSON generator. Always return valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=500,
            )
            
            elapsed = time.time() - start_time
            text_output = response.choices[0].message.content.strip()
            
            if debug:
                print(f"[LLM] Model: {self.model}")
                print(f"[LLM] Response time: {elapsed:.2f}s")
                print(f"[LLM] Raw output: {text_output}")
            
            # Parse JSON response
            try:
                parsed = json.loads(text_output)
            except json.JSONDecodeError as e:
                return ExtractionResult(
                    status=ProcessingStatus.ERROR,
                    original_sentence=sentence,
                    parameterized_sentence="",
                    notes=f"LLM returned invalid JSON: {str(e)}"
                )
            
            # Convert to typed ExtractionResult
            groups = []
            for g in parsed.get("groups", []):
                group = EntityGroup(
                    action=g.get("action", ""),
                    intent=g.get("intent"),
                    products=g.get("products", []),
                    brands=g.get("brands", []),
                    quantities=g.get("quantities", []),
                    units=g.get("units", []),
                    variants=g.get("variants", []),
                    ordinal_ref=g.get("ordinal_ref")
                )
                groups.append(group)
            
            # Map status
            status_map = {
                "success": ProcessingStatus.SUCCESS,
                "error": ProcessingStatus.ERROR,
                "no_entities_found": ProcessingStatus.NO_ENTITIES,
            }
            status = status_map.get(parsed.get("status", "error"), ProcessingStatus.ERROR)
            
            # Build notes
            notes = parsed.get("reason", "")
            if parsed.get("notes"):
                notes_list = parsed["notes"] if isinstance(parsed["notes"], list) else [parsed["notes"]]
                notes += " | " + ", ".join(notes_list)
            notes += f" | LLM extraction ({elapsed:.2f}s)"
            
            return ExtractionResult(
                status=status,
                original_sentence=sentence,
                parameterized_sentence=sentence,  # LLM doesn't parameterize
                groups=groups,
                grouping_result=GroupingResult(
                    groups=groups,
                    status=parsed.get("status", "ok"),
                    reason=parsed.get("reason")
                ),
                notes=notes.strip(" | ")
            )
            
        except Exception as e:
            return ExtractionResult(
                status=ProcessingStatus.ERROR,
                original_sentence=sentence,
                parameterized_sentence="",
                notes=f"LLM extraction failed: {str(e)}"
            )


def extract_with_llm(sentence: str, model: str = "gpt-4o-mini", debug: bool = False) -> ExtractionResult:
    """
    Convenience function for LLM extraction.
    
    Args:
        sentence: Input sentence
        model: OpenAI model to use
        debug: Enable debug logging
        
    Returns:
        ExtractionResult
        
    Example:
        >>> result = extract_with_llm("add 2kg chicken")
        >>> print(result.groups[0].products)
        ['chicken']
    """
    extractor = LLMExtractor(model=model)
    return extractor.extract(sentence, debug=debug)

