"""
Unit tests for luma.models.ner_inference.

Tests the NER inference implementation to ensure it produces results
compatible with semantics/ner_inference.py.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import torch
import numpy as np

from luma.classification.inference import NERModel
from luma.data_types import NERPrediction


class TestNERModelInit:
    """Tests for NERModel initialization."""
    
    @patch('luma.classification.inference.hf_pipeline')
    def test_init_with_default_path(self, mock_pipeline):
        """Test initialization with default model path."""
        model = NERModel()
        
        # Should use default path
        assert model.model_path.endswith("bert-ner-best")
        mock_pipeline.assert_called_once()
    
    @patch('luma.classification.inference.hf_pipeline')
    def test_init_with_custom_path(self, mock_pipeline):
        """Test initialization with custom model path."""
        custom_path = "/custom/model/path"
        model = NERModel(model_path=custom_path)
        
        assert model.model_path == custom_path
        mock_pipeline.assert_called_once_with(
            "token-classification",
            model=custom_path,
            tokenizer=custom_path,
            aggregation_strategy="none"
        )


class TestNERModelMergeWordpieces:
    """Tests for wordpiece merging."""
    
    def test_merge_simple_wordpieces(self):
        """Test merging simple wordpiece tokens."""
        model = NERModel.__new__(NERModel)  # Create without __init__
        
        tokens = ["coca", "##-", "##cola"]
        labels = ["B-BRAND", "I-BRAND", "I-BRAND"]
        scores = [0.9, 0.85, 0.88]
        
        merged_tokens, merged_labels, merged_scores = model._merge_wordpieces(
            tokens, labels, scores
        )
        
        assert merged_tokens == ["coca-cola"]
        assert merged_labels == ["B-BRAND"]
        assert len(merged_scores) == 1
        assert 0.87 < merged_scores[0] < 0.89  # Average of scores
    
    def test_merge_no_wordpieces(self):
        """Test when no wordpieces need merging."""
        model = NERModel.__new__(NERModel)
        
        tokens = ["add", "rice"]
        labels = ["B-ACTION", "B-PRODUCT"]
        scores = [0.99, 0.95]
        
        merged_tokens, merged_labels, merged_scores = model._merge_wordpieces(
            tokens, labels, scores
        )
        
        assert merged_tokens == tokens
        assert merged_labels == labels
        assert merged_scores == scores


class TestNERModelMergeAdjacentBrands:
    """Tests for adjacent brand merging."""
    
    def test_merge_two_brands(self):
        """Test merging two adjacent brand tokens."""
        model = NERModel.__new__(NERModel)
        
        tokens = ["coca", "cola"]
        labels = ["B-BRAND", "I-BRAND"]
        scores = [0.9, 0.85]
        
        merged_tokens, merged_labels, merged_scores = model._merge_adjacent_brands(
            tokens, labels, scores
        )
        
        assert merged_tokens == ["coca cola"]
        assert merged_labels == ["B-BRAND"]
        assert len(merged_scores) == 1
    
    def test_merge_multiple_brands(self):
        """Test merging multiple brand tokens."""
        model = NERModel.__new__(NERModel)
        
        tokens = ["urban", "decay", "beauty"]
        labels = ["B-BRAND", "I-BRAND", "I-BRAND"]
        scores = [0.9, 0.88, 0.85]
        
        merged_tokens, merged_labels, merged_scores = model._merge_adjacent_brands(
            tokens, labels, scores
        )
        
        assert merged_tokens == ["urban decay beauty"]
        assert merged_labels == ["B-BRAND"]
    
    def test_no_adjacent_brands(self):
        """Test when no brands need merging."""
        model = NERModel.__new__(NERModel)
        
        tokens = ["add", "2", "rice"]
        labels = ["B-ACTION", "B-QUANTITY", "B-PRODUCT"]
        scores = [0.99, 0.95, 0.92]
        
        merged_tokens, merged_labels, merged_scores = model._merge_adjacent_brands(
            tokens, labels, scores
        )
        
        assert merged_tokens == tokens
        assert merged_labels == labels


class TestNERModelNormalizePlaceholders:
    """Tests for placeholder normalization."""
    
    def test_split_multiple_placeholders(self):
        """Test splitting space-separated placeholders."""
        model = NERModel.__new__(NERModel)
        
        tokens = ["unittoken brandtoken"]
        labels = ["B-UNIT"]
        scores = [0.9]
        
        fixed_tokens, fixed_labels, fixed_scores = model._normalize_placeholders(
            tokens, labels, scores
        )
        
        assert fixed_tokens == ["unittoken", "brandtoken"]
        assert fixed_labels == ["B-UNIT", "B-UNIT"]  # Both get same label
    
    def test_split_placeholder_with_suffix(self):
        """Test splitting placeholder merged with other text."""
        model = NERModel.__new__(NERModel)
        
        tokens = ["brandtoken-nice"]
        labels = ["B-BRAND"]
        scores = [0.9]
        
        fixed_tokens, fixed_labels, fixed_scores = model._normalize_placeholders(
            tokens, labels, scores
        )
        
        assert fixed_tokens == ["brandtoken", "-nice"]
        assert fixed_labels == ["B-BRAND", "O"]
    
    def test_no_normalization_needed(self):
        """Test when no normalization is needed."""
        model = NERModel.__new__(NERModel)
        
        tokens = ["producttoken", "brandtoken"]
        labels = ["B-PRODUCT", "B-BRAND"]
        scores = [0.95, 0.92]
        
        fixed_tokens, fixed_labels, fixed_scores = model._normalize_placeholders(
            tokens, labels, scores
        )
        
        assert fixed_tokens == tokens
        assert fixed_labels == labels


class TestNERModelEnforcePlaceholderLabels:
    """Tests for placeholder label enforcement."""
    
    def test_enforce_producttoken(self):
        """Test enforcing correct label for producttoken."""
        model = NERModel.__new__(NERModel)
        
        tokens = ["add", "producttoken"]
        labels = ["B-ACTION", "O"]  # Wrong label
        
        corrected = model._enforce_placeholder_labels(tokens, labels)
        
        assert corrected == ["B-ACTION", "B-PRODUCT"]
    
    def test_enforce_all_placeholders(self):
        """Test enforcing labels for all placeholder types."""
        model = NERModel.__new__(NERModel)
        
        tokens = ["producttoken", "brandtoken", "unittoken", "varianttoken"]
        labels = ["O", "O", "O", "O"]
        
        corrected = model._enforce_placeholder_labels(tokens, labels)
        
        assert corrected == ["B-PRODUCT", "B-BRAND", "B-UNIT", "B-TOKEN"]
    
    def test_preserve_non_placeholders(self):
        """Test that non-placeholder labels are preserved."""
        model = NERModel.__new__(NERModel)
        
        tokens = ["add", "rice", "producttoken"]
        labels = ["B-ACTION", "B-PRODUCT", "O"]
        
        corrected = model._enforce_placeholder_labels(tokens, labels)
        
        assert corrected == ["B-ACTION", "B-PRODUCT", "B-PRODUCT"]


class TestNERModelFixBIOSequence:
    """Tests for BIO sequence fixing."""
    
    def test_fix_consecutive_b_product(self):
        """Test fixing consecutive B-PRODUCT labels."""
        model = NERModel.__new__(NERModel)
        model.PLACEHOLDER_TOKENS = {"producttoken", "brandtoken", "unittoken"}
        
        tokens = ["rice", "beans"]
        labels = ["B-PRODUCT", "B-PRODUCT"]
        
        fixed = model._fix_bio_sequence(tokens, labels)
        
        assert fixed == ["B-PRODUCT", "I-PRODUCT"]
    
    def test_preserve_placeholder_b_product(self):
        """Test that placeholder B-PRODUCT labels are preserved."""
        model = NERModel.__new__(NERModel)
        
        tokens = ["producttoken", "producttoken"]
        labels = ["B-PRODUCT", "B-PRODUCT"]
        
        fixed = model._fix_bio_sequence(tokens, labels)
        
        # Should NOT fix when "token" substring exists (matches semantics line 101)
        assert fixed == ["B-PRODUCT", "B-PRODUCT"]
    
    def test_preserve_any_token_substring(self):
        """Test that any token with 'token' substring is preserved."""
        model = NERModel.__new__(NERModel)
        
        # Even custom tokens with "token" in the name should be skipped
        tokens = ["mytoken", "customtoken"]
        labels = ["B-PRODUCT", "B-PRODUCT"]
        
        fixed = model._fix_bio_sequence(tokens, labels)
        
        # Should NOT fix because "token" substring exists
        assert fixed == ["B-PRODUCT", "B-PRODUCT"]
    
    def test_preserve_other_labels(self):
        """Test that other labels are not affected."""
        model = NERModel.__new__(NERModel)
        
        tokens = ["add", "2", "rice"]
        labels = ["B-ACTION", "B-QUANTITY", "B-PRODUCT"]
        
        fixed = model._fix_bio_sequence(tokens, labels)
        
        assert fixed == labels


class TestNERModelPredict:
    """Integration tests for complete prediction flow."""
    
    @patch('luma.classification.inference.hf_pipeline')
    @patch('luma.classification.inference.torch')
    def test_predict_returns_ner_prediction(self, mock_torch, mock_pipeline):
        """Test that predict returns NERPrediction."""
        # Setup mocks
        mock_tokenizer = MagicMock()
        mock_model = MagicMock()
        mock_pipeline_instance = MagicMock()
        mock_pipeline_instance.tokenizer = mock_tokenizer
        mock_pipeline_instance.model = mock_model
        mock_pipeline.return_value = mock_pipeline_instance
        
        # Mock tokenization
        mock_tokenizer.return_value = {
            "input_ids": torch.tensor([[101, 1000, 2000, 102]])  # [CLS] add rice [SEP]
        }
        mock_tokenizer.decode.side_effect = lambda x: {
            1000: "add",
            2000: "rice"
        }.get(x[0], "")
        
        # Mock model output
        mock_outputs = MagicMock()
        mock_outputs.logits = torch.tensor([[[0.1, 0.9], [0.2, 0.8], [0.3, 0.7], [0.1, 0.9]]])
        mock_model.return_value = mock_outputs
        mock_model.config.id2label = {0: "O", 1: "B-ACTION"}
        
        # Mock torch operations
        mock_torch.no_grad.return_value.__enter__ = Mock()
        mock_torch.no_grad.return_value.__exit__ = Mock()
        mock_torch.argmax.return_value = torch.tensor([[1, 1, 1, 1]])
        mock_torch.softmax.return_value = torch.tensor([[[0.1, 0.9], [0.2, 0.8], [0.3, 0.7], [0.1, 0.9]]])
        
        # Create model and predict
        model = NERModel()
        result = model.predict("add rice")
        
        # Verify result type
        assert isinstance(result, NERPrediction)
        assert isinstance(result.tokens, list)
        assert isinstance(result.labels, list)
        assert isinstance(result.scores, list)


class TestNERModelConstants:
    """Tests for model constants."""
    
    def test_placeholder_tokens_defined(self):
        """Test placeholder tokens are defined (matches semantics exactly)."""
        assert "producttoken" in NERModel.PLACEHOLDER_TOKENS
        assert "brandtoken" in NERModel.PLACEHOLDER_TOKENS
        assert "unittoken" in NERModel.PLACEHOLDER_TOKENS
        assert "varianttoken" in NERModel.PLACEHOLDER_TOKENS
        # NOTE: quantitytoken is NOT in the original semantics/ner_inference.py
        assert "quantitytoken" not in NERModel.PLACEHOLDER_TOKENS
    
    def test_enforced_labels_defined(self):
        """Test enforced labels are defined (matches semantics exactly)."""
        assert NERModel.ENFORCED_LABELS["producttoken"] == "B-PRODUCT"
        assert NERModel.ENFORCED_LABELS["brandtoken"] == "B-BRAND"
        assert NERModel.ENFORCED_LABELS["unittoken"] == "B-UNIT"
        assert NERModel.ENFORCED_LABELS["varianttoken"] == "B-TOKEN"
        # NOTE: quantitytoken is NOT in the original semantics/ner_inference.py
        assert "quantitytoken" not in NERModel.ENFORCED_LABELS
