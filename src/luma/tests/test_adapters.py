"""
Unit tests for luma.adapters module.

Tests bidirectional conversion between legacy dict format and new typed format.
"""
import pytest
from luma.adapters import (
    from_legacy_result,
    to_legacy_result,
    from_legacy_nlp_result,
    from_legacy_ner_result,
    validate_conversion,
)
from luma.data_types import (
    ProcessingStatus,
    ExtractionResult,
    EntityGroup,
    NLPExtraction,
    NERPrediction,
)


class TestFromLegacyResult:
    """Tests for converting legacy dict to typed format."""
    
    def test_basic_conversion(self):
        """Test converting a basic legacy result."""
        legacy = {
            "status": "success",
            "original_sentence": "add 2kg rice",
            "parameterized_sentence": "add 2 unittoken producttoken",
            "grouped_entities": {
                "status": "ok",
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
        
        result = from_legacy_result(legacy)
        
        assert isinstance(result, ExtractionResult)
        assert result.status == ProcessingStatus.SUCCESS
        assert result.original_sentence == "add 2kg rice"
        assert len(result.groups) == 1
        assert result.groups[0].products == ["rice"]
        assert result.groups[0].quantities == ["2"]
    
    def test_status_mapping(self):
        """Test all status values are correctly mapped."""
        test_cases = [
            ("success", ProcessingStatus.SUCCESS),
            ("error", ProcessingStatus.ERROR),
            ("needs_llm_fix", ProcessingStatus.NEEDS_LLM),
            ("no_entities_found", ProcessingStatus.NO_ENTITIES),
        ]
        
        for legacy_status, expected_enum in test_cases:
            legacy = {
                "status": legacy_status,
                "original_sentence": "test",
                "parameterized_sentence": "test",
                "grouped_entities": {"groups": []}
            }
            result = from_legacy_result(legacy)
            assert result.status == expected_enum
    
    def test_multiple_groups(self):
        """Test converting multiple entity groups."""
        legacy = {
            "status": "success",
            "original_sentence": "add rice and remove milk",
            "parameterized_sentence": "add producttoken and remove producttoken",
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
                    },
                    {
                        "action": "remove",
                        "products": ["milk"],
                        "brands": [],
                        "quantities": [],
                        "units": [],
                        "variants": [],
                        "intent": None,
                        "intent_confidence": None
                    }
                ]
            }
        }
        
        result = from_legacy_result(legacy)
        assert len(result.groups) == 2
        assert result.groups[0].action == "add"
        assert result.groups[1].action == "remove"
    
    def test_with_nlp_entities(self):
        """Test converting with NLP entities included."""
        legacy = {
            "status": "success",
            "original_sentence": "add rice",
            "parameterized_sentence": "add producttoken",
            "grouped_entities": {"groups": []},
            "nlp_entities": {
                "products": ["rice"],
                "brands": [],
                "units": [],
                "quantities": [],
                "variants": [],
                "likely_products": [],
                "likely_brands": [],
                "likely_variants": [],
                "productbrands": [],
                "osentence": "add rice",
                "psentence": "add producttoken"
            }
        }
        
        result = from_legacy_result(legacy)
        assert result.nlp_extraction is not None
        assert result.nlp_extraction.products == ["rice"]


class TestToLegacyResult:
    """Tests for converting typed format to legacy dict."""
    
    def test_basic_conversion(self):
        """Test converting a basic typed result to dict."""
        group = EntityGroup(
            action="add",
            products=["rice"],
            quantities=["2"],
            units=["kg"],
            brands=[],
            variants=[]
        )
        result = ExtractionResult(
            status=ProcessingStatus.SUCCESS,
            original_sentence="add 2kg rice",
            parameterized_sentence="add 2 unittoken producttoken",
            groups=[group]
        )
        
        legacy = to_legacy_result(result)
        
        assert isinstance(legacy, dict)
        assert legacy["status"] == "success"
        assert legacy["original_sentence"] == "add 2kg rice"
        assert len(legacy["grouped_entities"]["groups"]) == 1
        assert legacy["grouped_entities"]["groups"][0]["products"] == ["rice"]
    
    def test_status_conversion(self):
        """Test status enum is converted to string."""
        result = ExtractionResult(
            status=ProcessingStatus.NEEDS_LLM,
            original_sentence="test",
            parameterized_sentence="test"
        )
        
        legacy = to_legacy_result(result)
        assert legacy["status"] == "needs_llm_fix"
    
    def test_with_nlp_extraction(self):
        """Test converting with NLP extraction data."""
        nlp = NLPExtraction(
            products=["rice"],
            original_sentence="add rice",
            parameterized_sentence="add producttoken"
        )
        result = ExtractionResult(
            status=ProcessingStatus.SUCCESS,
            original_sentence="add rice",
            parameterized_sentence="add producttoken",
            nlp_extraction=nlp
        )
        
        legacy = to_legacy_result(result)
        assert legacy["nlp_entities"] is not None
        assert legacy["nlp_entities"]["products"] == ["rice"]


class TestRoundTripConversion:
    """Tests for round-trip conversion (legacy -> typed -> legacy)."""
    
    def test_simple_round_trip(self):
        """Test data survives round-trip conversion."""
        original = {
            "status": "success",
            "original_sentence": "add 2kg rice",
            "parameterized_sentence": "add 2 unittoken producttoken",
            "grouped_entities": {
                "status": "ok",
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
        
        # Convert to typed
        typed = from_legacy_result(original)
        
        # Convert back to legacy
        converted = to_legacy_result(typed)
        
        # Check key fields match
        assert converted["status"] == original["status"]
        assert converted["original_sentence"] == original["original_sentence"]
        assert len(converted["grouped_entities"]["groups"]) == len(original["grouped_entities"]["groups"])
        
        orig_group = original["grouped_entities"]["groups"][0]
        conv_group = converted["grouped_entities"]["groups"][0]
        assert conv_group["action"] == orig_group["action"]
        assert conv_group["products"] == orig_group["products"]
        assert conv_group["quantities"] == orig_group["quantities"]


class TestFromLegacyNLPResult:
    """Tests for converting legacy NLP result."""
    
    def test_convert_nlp_result(self):
        """Test converting NLP result dict."""
        legacy_nlp = {
            "products": ["rice", "beans"],
            "brands": ["Nike"],
            "units": ["kg"],
            "quantities": ["2"],
            "variants": ["white"],
            "likely_products": [],
            "likely_brands": [],
            "likely_variants": [],
            "productbrands": [],
            "osentence": "add 2kg rice",
            "psentence": "add 2 unittoken producttoken"
        }
        
        nlp = from_legacy_nlp_result(legacy_nlp)
        
        assert isinstance(nlp, NLPExtraction)
        assert nlp.products == ["rice", "beans"]
        assert nlp.brands == ["Nike"]
        assert nlp.original_sentence == "add 2kg rice"


class TestFromLegacyNERResult:
    """Tests for converting legacy NER result."""
    
    def test_convert_ner_result(self):
        """Test converting NER result dict."""
        legacy_ner = {
            "tokens": ["add", "2", "kg", "rice"],
            "labels": ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT"],
            "scores": [0.99, 0.98, 0.97, 0.96]
        }
        
        ner = from_legacy_ner_result(legacy_ner)
        
        assert isinstance(ner, NERPrediction)
        assert ner.tokens == ["add", "2", "kg", "rice"]
        assert ner.labels == ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT"]
        assert len(ner.scores) == 4


class TestValidateConversion:
    """Tests for validate_conversion function."""
    
    def test_valid_conversion(self):
        """Test validation passes for correct conversion."""
        legacy = {
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
            }
        }
        
        converted = from_legacy_result(legacy)
        assert validate_conversion(legacy, converted) is True
    
    def test_invalid_sentence(self):
        """Test validation fails when sentence doesn't match."""
        legacy = {
            "status": "success",
            "original_sentence": "add rice",
            "parameterized_sentence": "add producttoken",
            "grouped_entities": {"groups": []}
        }
        
        converted = from_legacy_result(legacy)
        converted.original_sentence = "different sentence"  # Modify
        
        assert validate_conversion(legacy, converted) is False
    
    def test_invalid_group_count(self):
        """Test validation fails when group counts don't match."""
        legacy = {
            "status": "success",
            "original_sentence": "test",
            "parameterized_sentence": "test",
            "grouped_entities": {
                "groups": [
                    {"action": "add", "products": ["rice"], "brands": [], 
                     "quantities": [], "units": [], "variants": [],
                     "intent": None, "intent_confidence": None}
                ]
            }
        }
        
        converted = from_legacy_result(legacy)
        converted.groups = []  # Remove groups
        
        assert validate_conversion(legacy, converted) is False

