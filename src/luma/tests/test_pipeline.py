"""
Unit tests for luma.core.pipeline module.

Tests the pipeline wrapper that delegates to semantics/ in Phase 2.
"""
import pytest
from unittest.mock import patch, MagicMock
from luma.core.pipeline import (
    EntityExtractionPipeline,
    extract_entities,
    extract_entities_legacy,
)
from luma.data_types import (
    ProcessingStatus,
    ExtractionResult,
    EntityGroup,
)


class TestEntityExtractionPipeline:
    """Tests for EntityExtractionPipeline class."""
    
    def test_init_default_uses_legacy(self):
        """Test pipeline defaults to legacy mode."""
        with patch('luma.core.pipeline._import_legacy_extract') as mock_import:
            mock_import.return_value = MagicMock()
            pipeline = EntityExtractionPipeline()
            assert pipeline.use_luma is False
            mock_import.assert_called_once()
    
    def test_init_with_luma_raises_not_implemented(self):
        """Test pipeline raises NotImplementedError for luma mode."""
        with pytest.raises(NotImplementedError, match="not ready yet"):
            EntityExtractionPipeline(use_luma=True)
    
    @patch('luma.core.pipeline._import_legacy_extract')
    def test_extract_calls_legacy(self, mock_import):
        """Test extract method delegates to legacy extractor."""
        # Mock legacy extractor
        mock_extractor = MagicMock()
        mock_extractor.return_value = {
            "status": "success",
            "original_sentence": "add rice",
            "parameterized_sentence": "add producttoken",
            "grouped_entities": {
                "groups": [
                    {
                        "action": "add",
                        "products": ["rice"],
                        "brands": [],
                        "quantities": [],
                        "units": [],
                        "variants": [],
                        "intent": None,
                        "intent_confidence": None
                    }
                ]
            },
            "notes": "",
            "index_map": {}
        }
        mock_import.return_value = mock_extractor
        
        # Create pipeline and extract
        pipeline = EntityExtractionPipeline()
        result = pipeline.extract("add rice")
        
        # Verify
        mock_extractor.assert_called_once_with("add rice", debug=False)
        assert isinstance(result, ExtractionResult)
        assert result.status == ProcessingStatus.SUCCESS
        assert result.original_sentence == "add rice"
    
    @patch('luma.core.pipeline._import_legacy_extract')
    def test_extract_handles_legacy_error(self, mock_import):
        """Test extract handles errors from legacy gracefully."""
        # Mock legacy extractor that raises error
        mock_extractor = MagicMock()
        mock_extractor.side_effect = Exception("Legacy extraction failed")
        mock_import.return_value = mock_extractor
        
        # Create pipeline and extract
        pipeline = EntityExtractionPipeline()
        result = pipeline.extract("test sentence")
        
        # Should return error result, not raise
        assert result.status == ProcessingStatus.ERROR
        assert "Legacy extraction failed" in result.notes


class TestExtractEntitiesFunction:
    """Tests for extract_entities main API function."""
    
    @patch('luma.core.pipeline._import_legacy_extract')
    def test_extract_entities_returns_typed_result(self, mock_import):
        """Test extract_entities returns ExtractionResult."""
        # Mock legacy extractor
        mock_extractor = MagicMock()
        mock_extractor.return_value = {
            "status": "success",
            "original_sentence": "add 2kg rice",
            "parameterized_sentence": "add 2 unittoken producttoken",
            "grouped_entities": {
                "groups": [
                    {
                        "action": "add",
                        "products": ["rice"],
                        "quantities": ["2"],
                        "units": ["kg"],
                        "brands": [],
                        "variants": [],
                        "intent": "add_to_cart",
                        "intent_confidence": 0.95
                    }
                ]
            },
            "notes": "",
            "index_map": {}
        }
        mock_import.return_value = mock_extractor
        
        # Call function
        result = extract_entities("add 2kg rice")
        
        # Verify typed result
        assert isinstance(result, ExtractionResult)
        assert result.is_successful()
        assert len(result.groups) == 1
        assert result.groups[0].products == ["rice"]
        assert result.groups[0].quantities == ["2"]


class TestExtractEntitiesLegacyFunction:
    """Tests for extract_entities_legacy backward compatibility function."""
    
    @patch('luma.core.pipeline._import_legacy_extract')
    def test_extract_entities_legacy_returns_dict(self, mock_import):
        """Test extract_entities_legacy returns dict format."""
        # Mock legacy extractor
        mock_extractor = MagicMock()
        mock_extractor.return_value = {
            "status": "success",
            "original_sentence": "add rice",
            "parameterized_sentence": "add producttoken",
            "grouped_entities": {
                "groups": [
                    {
                        "action": "add",
                        "products": ["rice"],
                        "brands": [],
                        "quantities": [],
                        "units": [],
                        "variants": [],
                        "intent": None,
                        "intent_confidence": None
                    }
                ]
            },
            "notes": "",
            "index_map": {}
        }
        mock_import.return_value = mock_extractor
        
        # Call function
        result = extract_entities_legacy("add rice")
        
        # Verify dict format
        assert isinstance(result, dict)
        assert result["status"] == "success"
        assert "grouped_entities" in result
        assert result["grouped_entities"]["groups"][0]["products"] == ["rice"]


class TestIntegration:
    """Integration tests using mocked legacy extractor."""
    
    @patch('luma.core.pipeline._import_legacy_extract')
    def test_full_workflow_typed_api(self, mock_import):
        """Test complete workflow with typed API."""
        # Mock a realistic legacy response
        mock_extractor = MagicMock()
        mock_extractor.return_value = {
            "status": "success",
            "original_sentence": "add 2kg white rice and 3 bottles of milk",
            "parameterized_sentence": "add 2 unittoken varianttoken producttoken and 3 unittoken of producttoken",
            "grouped_entities": {
                "groups": [
                    {
                        "action": "add",
                        "products": ["rice"],
                        "quantities": ["2"],
                        "units": ["kg"],
                        "brands": [],
                        "variants": ["white"],
                        "intent": "add_to_cart",
                        "intent_confidence": 0.96
                    },
                    {
                        "action": "add",
                        "products": ["milk"],
                        "quantities": ["3"],
                        "units": ["bottles"],
                        "brands": [],
                        "variants": [],
                        "intent": "add_to_cart",
                        "intent_confidence": 0.96
                    }
                ]
            },
            "notes": "",
            "index_map": {}
        }
        mock_import.return_value = mock_extractor
        
        # Use typed API
        result = extract_entities("add 2kg white rice and 3 bottles of milk")
        
        # Verify result structure
        assert result.is_successful()
        assert len(result.groups) == 2
        
        # Check first group
        assert result.groups[0].action == "add"
        assert result.groups[0].products == ["rice"]
        assert result.groups[0].quantities == ["2"]
        assert result.groups[0].units == ["kg"]
        assert result.groups[0].variants == ["white"]
        
        # Check second group
        assert result.groups[1].products == ["milk"]
        assert result.groups[1].quantities == ["3"]
        assert result.groups[1].units == ["bottles"]
    
    @patch('luma.core.pipeline._import_legacy_extract')
    def test_full_workflow_legacy_api(self, mock_import):
        """Test complete workflow with legacy dict API."""
        # Mock legacy response
        mock_extractor = MagicMock()
        mock_extractor.return_value = {
            "status": "success",
            "original_sentence": "remove 1 Nike shoes",
            "parameterized_sentence": "remove 1 brandtoken producttoken",
            "grouped_entities": {
                "groups": [
                    {
                        "action": "remove",
                        "products": ["shoes"],
                        "quantities": ["1"],
                        "units": [],
                        "brands": ["Nike"],
                        "variants": [],
                        "intent": "remove_from_cart",
                        "intent_confidence": 0.94
                    }
                ]
            },
            "notes": "",
            "index_map": {}
        }
        mock_import.return_value = mock_extractor
        
        # Use legacy API
        result = extract_entities_legacy("remove 1 Nike shoes")
        
        # Verify dict structure
        assert isinstance(result, dict)
        assert result["status"] == "success"
        assert len(result["grouped_entities"]["groups"]) == 1
        assert result["grouped_entities"]["groups"][0]["products"] == ["shoes"]
        assert result["grouped_entities"]["groups"][0]["brands"] == ["Nike"]

