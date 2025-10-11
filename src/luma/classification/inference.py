"""
Stage 2: Token Classification

NER Model for token classification.
Clean, typed implementation ported from semantics/ner_inference.py
Maintains the same logic and behavior but with better structure.
"""
import re
import os
from pathlib import Path
from typing import List, Tuple, Optional, Set
import numpy as np
from transformers import pipeline as hf_pipeline
import torch

from luma.data_types import NERPrediction


# Debug logging
DEBUG_ENABLED = os.environ.get("DEBUG_NLP", "0") == "1"


def _debug_print(*args, **kwargs):
    """Print debug message only if DEBUG_ENABLED is True."""
    if DEBUG_ENABLED:
        print(*args, **kwargs)


class NERModel:
    """
    Named Entity Recognition model for token classification.
    
    Handles:
    - Token classification using trained BERT model
    - Wordpiece merging
    - Brand entity merging
    - Placeholder normalization
    - BIO sequence fixing
    
    Example:
        >>> model = NERModel()
        >>> result = model.predict("add 2 producttoken to cart")
        >>> print(result.tokens, result.labels)
    """
    
    # Placeholder tokens used in parameterization
    # NOTE: Matches semantics/ner_inference.py exactly - no quantitytoken!
    PLACEHOLDER_TOKENS = {"producttoken", "brandtoken", "unittoken", "varianttoken"}
    
    # Enforced labels for placeholder tokens  
    # NOTE: Matches semantics/ner_inference.py exactly - no quantitytoken!
    ENFORCED_LABELS = {
        "producttoken": "B-PRODUCT",
        "brandtoken": "B-BRAND",
        "unittoken": "B-UNIT",
        "varianttoken": "B-TOKEN",
    }
    
    # Separator tokens (not used in current implementation but kept for reference)
    SEP_TOKENS: Set[str] = {
        "and", "or", "plus", "then", "next", "after", "after that",
        "followed", "by", "as well as", "also", "in addition", ","
    }
    
    def __init__(self, model_path: Optional[str] = None):
        """
        Initialize NER model.
        
        Args:
            model_path: Path to trained model directory.
                       If None, uses default luma/store/bert-ner-best
        """
        if model_path is None:
            # Default to luma store
            script_dir = Path(__file__).resolve().parent.parent
            model_path = str(script_dir / "store" / "bert-ner-best")
        
        self.model_path = model_path
        self.pipeline = hf_pipeline(
            "token-classification",
            model=model_path,
            tokenizer=model_path,
            aggregation_strategy="none"
        )
    
    def predict(self, text: str) -> NERPrediction:
        """
        Predict entity labels for input text.
        
        Args:
            text: Input sentence (may contain placeholder tokens)
            
        Returns:
            NERPrediction with tokens, labels, and confidence scores
            
        Example:
            >>> model = NERModel()
            >>> result = model.predict("add 2kg producttoken")
            >>> print(result.tokens)
            ['add', '2', 'kg', 'producttoken']
        """
        # Step 1: Tokenize and get raw predictions
        tokens, labels, scores = self._get_raw_predictions(text)
        
        # Step 2: Merge wordpieces (e.g., 'co', '##ca', '##-cola' → 'coca-cola')
        tokens, labels, scores = self._merge_wordpieces(tokens, labels, scores)
        
        # Step 3: Merge adjacent brand entities
        tokens, labels, scores = self._merge_adjacent_brands(tokens, labels, scores)
        
        # Step 4: Normalize merged placeholders
        tokens, labels, scores = self._normalize_placeholders(tokens, labels, scores)
        
        # Step 5: Enforce correct labels for placeholders
        labels = self._enforce_placeholder_labels(tokens, labels)
        
        # Step 6: Fix invalid BIO sequences
        labels = self._fix_bio_sequence(tokens, labels)
        
        # Step 7: Debug output
        self._debug_output(tokens, labels, scores)
        
        return NERPrediction(tokens=tokens, labels=labels, scores=scores)
    
    def _get_raw_predictions(self, text: str) -> Tuple[List[str], List[str], List[float]]:
        """
        Get raw token predictions from model.
        
        Args:
            text: Input text
            
        Returns:
            Tuple of (tokens, labels, scores)
        """
        tokenizer = self.pipeline.tokenizer
        model = self.pipeline.model
        
        # Tokenize
        inputs = tokenizer(text, return_tensors="pt", truncation=False)
        
        # Run model
        with torch.no_grad():
            outputs = model(**inputs)
        
        predictions = torch.argmax(outputs.logits, dim=-1)[0]
        scores_tensor = torch.softmax(outputs.logits, dim=-1)[0]
        
        # Convert to lists
        token_ids = inputs["input_ids"][0]
        tokens, labels, token_scores = [], [], []
        
        # Skip [CLS] and [SEP] tokens
        for i in range(1, len(token_ids) - 1):
            word = tokenizer.decode([token_ids[i]]).strip()
            if not word:
                continue
            
            label_id = predictions[i].item()
            label = model.config.id2label[label_id]
            score = scores_tensor[i][label_id].item()
            
            tokens.append(word)
            labels.append(label)
            token_scores.append(score)
            
            _debug_print(f"[DEBUG] Word: {word}")
            _debug_print(f"[DEBUG] Label: {label}")
        
        return tokens, labels, token_scores
    
    def _merge_wordpieces(
        self,
        tokens: List[str],
        labels: List[str],
        scores: List[float]
    ) -> Tuple[List[str], List[str], List[float]]:
        """
        Merge subword tokens (e.g., 'co', '##ca', '##-cola' → 'coca-cola').
        
        Args:
            tokens: List of token strings
            labels: List of label strings
            scores: List of confidence scores
            
        Returns:
            Tuple of merged (tokens, labels, scores)
        """
        merged_tokens, merged_labels, merged_scores = [], [], []
        current_token, current_label, current_scores = "", None, []
        
        for tok, lab, score in zip(tokens, labels, scores):
            if tok.startswith("##"):
                current_token += tok[2:]
                current_scores.append(score)
            else:
                if current_token:
                    merged_tokens.append(current_token)
                    merged_labels.append(current_label)
                    merged_scores.append(float(np.mean(current_scores)))
                current_token = tok
                current_label = lab
                current_scores = [score]
        
        if current_token:
            merged_tokens.append(current_token)
            merged_labels.append(current_label)
            merged_scores.append(float(np.mean(current_scores)))
        
        return merged_tokens, merged_labels, merged_scores
    
    def _merge_adjacent_brands(
        self,
        tokens: List[str],
        labels: List[str],
        scores: List[float]
    ) -> Tuple[List[str], List[str], List[float]]:
        """
        Merge adjacent brand entities into single token.
        
        Example: ['coca', 'cola'] with ['B-BRAND', 'I-BRAND'] → ['coca cola']
        
        Args:
            tokens: List of token strings
            labels: List of label strings
            scores: List of confidence scores
            
        Returns:
            Tuple of merged (tokens, labels, scores)
        """
        if not tokens:
            return tokens, labels, scores
        
        merged_tokens, merged_labels, merged_scores = [], [], []
        
        i = 0
        while i < len(tokens):
            if labels[i] == "B-BRAND":
                # Collect all subsequent brand tokens
                brand_tokens = [tokens[i]]
                brand_scores = [float(scores[i])]
                j = i + 1
                
                while j < len(tokens) and labels[j] in ["I-BRAND", "B-BRAND"]:
                    brand_tokens.append(tokens[j])
                    brand_scores.append(float(scores[j]))
                    j += 1
                
                # Merge into single token
                merged_tokens.append(" ".join(brand_tokens))
                merged_labels.append("B-BRAND")
                merged_scores.append(sum(brand_scores) / len(brand_scores))
                i = j
            else:
                merged_tokens.append(tokens[i])
                merged_labels.append(labels[i])
                merged_scores.append(scores[i])
                i += 1
        
        return merged_tokens, merged_labels, merged_scores
    
    def _normalize_placeholders(
        self,
        tokens: List[str],
        labels: List[str],
        scores: List[float]
    ) -> Tuple[List[str], List[str], List[float]]:
        """
        Split merged placeholders safely.
        
        Handles:
        - Multiple placeholders merged (e.g., 'unittoken brandtoken')
        - Placeholder + word merges (e.g., 'brandtoken-nice')
        
        Args:
            tokens: List of token strings
            labels: List of label strings
            scores: List of confidence scores
            
        Returns:
            Tuple of normalized (tokens, labels, scores)
        """
        fixed_toks, fixed_labs, fixed_scores = [], [], []
        # NOTE: Matches semantics/ner_inference.py line 175 exactly - no quantitytoken!
        placeholder_pattern = re.compile(
            r"\b(producttoken|brandtoken|unittoken|varianttoken)\b"
        )
        
        for tok, lab, sc in zip(tokens, labels, scores):
            parts = tok.split()
            
            # Case 1: Multiple space-separated parts
            if len(parts) > 1:
                for part in parts:
                    fixed_toks.append(part)
                    fixed_labs.append(lab)
                    fixed_scores.append(sc)
                continue
            
            # Case 2: Placeholder merged with other chars
            match = placeholder_pattern.search(tok.lower())
            if match and tok.lower() != match.group(1):
                # Extract before + after segments
                base = match.group(1)
                before = tok[:match.start()].strip()
                after = tok[match.end():].strip()
                
                if before:
                    fixed_toks.append(before)
                    fixed_labs.append("O")
                    fixed_scores.append(sc)
                
                fixed_toks.append(base)
                fixed_labs.append(f"B-{base.replace('token', '').upper()}")
                fixed_scores.append(sc)
                
                if after:
                    fixed_toks.append(after)
                    fixed_labs.append("O")
                    fixed_scores.append(sc)
            else:
                fixed_toks.append(tok)
                fixed_labs.append(lab)
                fixed_scores.append(sc)
        
        return fixed_toks, fixed_labs, fixed_scores
    
    def _enforce_placeholder_labels(
        self,
        tokens: List[str],
        labels: List[str]
    ) -> List[str]:
        """
        Enforce correct labels for placeholder tokens.
        
        Args:
            tokens: List of token strings
            labels: List of label strings
            
        Returns:
            Corrected labels
        """
        corrected_labels = labels.copy()
        
        for i, tok in enumerate(tokens):
            low_tok = tok.lower()
            if low_tok in self.ENFORCED_LABELS:
                corrected_labels[i] = self.ENFORCED_LABELS[low_tok]
        
        return corrected_labels
    
    def _fix_bio_sequence(
        self,
        tokens: List[str],
        labels: List[str]
    ) -> List[str]:
        """
        Fix invalid BIO transitions (B-PRODUCT B-PRODUCT).
        
        Only fixes when neither token is a placeholder.
        
        CRITICAL: Matches semantics/ner_inference.py line 101 exactly!
        Uses substring check "token" in string, not exact placeholder match.
        
        Args:
            tokens: List of token strings
            labels: List of label strings
            
        Returns:
            Fixed labels
        """
        fixed = labels.copy()
        
        for i in range(1, len(tokens)):
            prev_label, curr_label = fixed[i - 1], fixed[i]
            prev_tok, curr_tok = tokens[i - 1].lower(), tokens[i].lower()
            
            # Only fix consecutive B-PRODUCT → B-PRODUCT
            if prev_label == "B-PRODUCT" and curr_label == "B-PRODUCT":
                # Skip merges involving placeholders
                # CRITICAL: Check if "token" substring exists (matches original line 101)
                if "token" in prev_tok or "token" in curr_tok:
                    continue
                
                # Merge only when both are natural tokens
                fixed[i] = "I-PRODUCT"
        
        return fixed
    
    def _debug_output(
        self,
        tokens: List[str],
        labels: List[str],
        scores: List[float]
    ):
        """Print debug output if DEBUG_ENABLED."""
        if not DEBUG_ENABLED:
            return
        
        _debug_print("\n[DEBUG] HR Inference Output (flat labels only):")
        for t, l, s in zip(tokens, labels, scores):
            _debug_print(f"  {t:<15} -> {l:<12} ({s:.3f})")

