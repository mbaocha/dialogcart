"""
Unit tests for luma.data_types module.

Tests all dataclasses for validation, methods, and type safety.
"""
import pytest
from luma.data_types import (
    ProcessingStatus,
    Entity,
    NLPExtraction,
    NERPrediction,
    EntityGroup,
    GroupingResult,
    ExtractionResult,
)


class TestProcessingStatus:
    """Tests for ProcessingStatus enum."""
    
    def test_status_values(self):
        """Test all status enum values."""
        assert ProcessingStatus.SUCCESS.value == "success"
        assert ProcessingStatus.ERROR.value == "error"
        assert ProcessingStatus.NEEDS_LLM.value == "needs_llm_fix"
        assert ProcessingStatus.NO_ENTITIES.value == "no_entities_found"
    
    def test_status_from_string(self):
        """Test creating status from string value."""
        status = ProcessingStatus("success")
        assert status == ProcessingStatus.SUCCESS


class TestEntity:
    """Tests for Entity dataclass."""
    
    def test_create_entity(self):
        """Test creating a basic entity."""
        entity = Entity(text="rice", confidence=0.95, position=3)
        assert entity.text == "rice"
        assert entity.confidence == 0.95
        assert entity.position == 3
    
    def test_entity_defaults(self):
        """Test entity default values."""
        entity = Entity(text="rice")
        assert entity.confidence == 1.0
        assert entity.position is None
    
    def test_entity_validation_confidence(self):
        """Test entity validates confidence range."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            Entity(text="rice", confidence=1.5)
        
        with pytest.raises(ValueError, match="Confidence must be between"):
            Entity(text="rice", confidence=-0.1)
    
    def test_entity_validation_text_type(self):
        """Test entity validates text type."""
        with pytest.raises(TypeError, match="Entity text must be str"):
            Entity(text=123)


class TestNLPExtraction:
    """Tests for NLPExtraction dataclass."""
    
    def test_create_nlp_extraction(self):
        """Test creating NLP extraction result."""
        extraction = NLPExtraction(
            products=["rice", "beans"],
            brands=["Coca Cola"],
            units=["kg"],
            quantities=["2"],
            original_sentence="add 2kg rice",
            parameterized_sentence="add 2 unittoken producttoken"
        )
        assert extraction.products == ["rice", "beans"]
        assert extraction.brands == ["Coca Cola"]
        assert extraction.units == ["kg"]
    
    def test_nlp_extraction_defaults(self):
        """Test NLP extraction default values."""
        extraction = NLPExtraction(original_sentence="test")
        assert extraction.products == []
        assert extraction.brands == []
        assert extraction.likely_products == []
    
    def test_nlp_extraction_requires_sentence(self):
        """Test NLP extraction validates original_sentence."""
        with pytest.raises(ValueError, match="original_sentence is required"):
            NLPExtraction()
    
    def test_has_entities_true(self):
        """Test has_entities returns True when entities exist."""
        extraction = NLPExtraction(
            original_sentence="test",
            products=["rice"]
        )
        assert extraction.has_entities() is True
    
    def test_has_entities_false(self):
        """Test has_entities returns False when no entities."""
        extraction = NLPExtraction(original_sentence="test")
        assert extraction.has_entities() is False
    
    def test_has_entities_with_likely_products(self):
        """Test has_entities counts likely_products."""
        extraction = NLPExtraction(
            original_sentence="test",
            likely_products=["unknown_product"]
        )
        assert extraction.has_entities() is True


class TestNERPrediction:
    """Tests for NERPrediction dataclass."""
    
    def test_create_ner_prediction(self):
        """Test creating NER prediction."""
        prediction = NERPrediction(
            tokens=["add", "2", "kg", "rice"],
            labels=["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT"],
            scores=[0.99, 0.95, 0.98, 0.97]
        )
        assert len(prediction.tokens) == 4
        assert len(prediction.labels) == 4
        assert len(prediction.scores) == 4
    
    def test_ner_prediction_validates_length(self):
        """Test NER prediction validates matching lengths."""
        with pytest.raises(ValueError, match="Length mismatch"):
            NERPrediction(
                tokens=["add", "rice"],
                labels=["B-ACTION"],  # Wrong length
                scores=[0.99, 0.97]
            )
    
    def test_ner_prediction_validates_not_empty(self):
        """Test NER prediction validates not empty."""
        with pytest.raises(ValueError, match="cannot be empty"):
            NERPrediction(tokens=[], labels=[], scores=[])
    
    def test_get_entities_by_label(self):
        """Test extracting entities by label."""
        prediction = NERPrediction(
            tokens=["add", "2", "kg", "rice", "and", "beans"],
            labels=["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT", "O", "B-PRODUCT"],
            scores=[0.99, 0.95, 0.98, 0.97, 0.9, 0.96]
        )
        
        products = prediction.get_entities_by_label("PRODUCT")
        assert products == ["rice", "beans"]
        
        actions = prediction.get_entities_by_label("ACTION")
        assert actions == ["add"]
    
    def test_get_entities_by_label_handles_bi_tags(self):
        """Test get_entities_by_label handles B- and I- prefixes."""
        prediction = NERPrediction(
            tokens=["Coca", "Cola"],
            labels=["B-BRAND", "I-BRAND"],
            scores=[0.95, 0.93]
        )
        
        brands = prediction.get_entities_by_label("BRAND")
        assert brands == ["Coca", "Cola"]


class TestEntityGroup:
    """Tests for EntityGroup dataclass."""
    
    def test_create_entity_group(self):
        """Test creating an entity group."""
        group = EntityGroup(
            action="add",
            intent="add_to_cart",
            intent_confidence=0.95,
            products=["rice"],
            quantities=["2"],
            units=["kg"]
        )
        assert group.action == "add"
        assert group.products == ["rice"]
        assert group.quantities == ["2"]
    
    def test_entity_group_defaults(self):
        """Test entity group default values."""
        group = EntityGroup(action="add")
        assert group.intent is None
        assert group.products == []
        assert group.brands == []
    
    def test_entity_group_requires_action(self):
        """Test entity group validates action is required."""
        with pytest.raises(ValueError, match="must have an action"):
            EntityGroup(action="")
    
    def test_entity_group_validates_confidence(self):
        """Test entity group validates intent_confidence range."""
        with pytest.raises(ValueError, match="must be between 0 and 1"):
            EntityGroup(action="add", intent_confidence=1.5)
    
    def test_is_valid_with_products(self):
        """Test is_valid returns True with products."""
        group = EntityGroup(action="add", products=["rice"])
        assert group.is_valid() is True
    
    def test_is_valid_with_brands(self):
        """Test is_valid returns True with brands."""
        group = EntityGroup(action="add", brands=["Nike"])
        assert group.is_valid() is True
    
    def test_is_valid_with_action_only(self):
        """Test is_valid returns True with just action."""
        group = EntityGroup(action="checkout")
        assert group.is_valid() is True
    
    def test_has_quantity(self):
        """Test has_quantity method."""
        group_with = EntityGroup(action="add", quantities=["2"])
        assert group_with.has_quantity() is True
        
        group_without = EntityGroup(action="add")
        assert group_without.has_quantity() is False
    
    def test_to_dict(self):
        """Test converting entity group to dict."""
        group = EntityGroup(
            action="add",
            products=["rice"],
            quantities=["2"]
        )
        result = group.to_dict()
        
        assert isinstance(result, dict)
        assert result["action"] == "add"
        assert result["products"] == ["rice"]
        assert result["quantities"] == ["2"]


class TestGroupingResult:
    """Tests for GroupingResult dataclass."""
    
    def test_create_grouping_result(self):
        """Test creating grouping result."""
        group = EntityGroup(action="add", products=["rice"])
        result = GroupingResult(
            groups=[group],
            status="ok"
        )
        assert len(result.groups) == 1
        assert result.status == "ok"
    
    def test_grouping_result_defaults(self):
        """Test grouping result defaults."""
        result = GroupingResult()
        assert result.groups == []
        assert result.status == "ok"
        assert result.reason is None
    
    def test_is_successful_true(self):
        """Test is_successful with valid groups."""
        group = EntityGroup(action="add", products=["rice"])
        result = GroupingResult(groups=[group], status="ok")
        assert result.is_successful() is True
    
    def test_is_successful_false_no_groups(self):
        """Test is_successful with no groups."""
        result = GroupingResult(groups=[], status="ok")
        assert result.is_successful() is False
    
    def test_is_successful_false_error_status(self):
        """Test is_successful with error status."""
        group = EntityGroup(action="add", products=["rice"])
        result = GroupingResult(groups=[group], status="error")
        assert result.is_successful() is False
    
    def test_get_all_products(self):
        """Test getting all products from multiple groups."""
        group1 = EntityGroup(action="add", products=["rice", "beans"])
        group2 = EntityGroup(action="remove", products=["milk"])
        result = GroupingResult(groups=[group1, group2])
        
        all_products = result.get_all_products()
        assert all_products == ["rice", "beans", "milk"]


class TestExtractionResult:
    """Tests for ExtractionResult dataclass."""
    
    def test_create_extraction_result(self):
        """Test creating extraction result."""
        group = EntityGroup(action="add", products=["rice"])
        result = ExtractionResult(
            status=ProcessingStatus.SUCCESS,
            original_sentence="add rice",
            parameterized_sentence="add producttoken",
            groups=[group]
        )
        assert result.status == ProcessingStatus.SUCCESS
        assert len(result.groups) == 1
    
    def test_extraction_result_requires_sentence(self):
        """Test extraction result validates original_sentence."""
        with pytest.raises(ValueError, match="original_sentence is required"):
            ExtractionResult(
                status=ProcessingStatus.SUCCESS,
                original_sentence="",
                parameterized_sentence=""
            )
    
    def test_is_successful_true(self):
        """Test is_successful with success status and groups."""
        group = EntityGroup(action="add", products=["rice"])
        result = ExtractionResult(
            status=ProcessingStatus.SUCCESS,
            original_sentence="test",
            parameterized_sentence="test",
            groups=[group]
        )
        assert result.is_successful() is True
    
    def test_is_successful_false_no_groups(self):
        """Test is_successful with no groups."""
        result = ExtractionResult(
            status=ProcessingStatus.SUCCESS,
            original_sentence="test",
            parameterized_sentence="test",
            groups=[]
        )
        assert result.is_successful() is False
    
    def test_has_errors(self):
        """Test has_errors method."""
        result_error = ExtractionResult(
            status=ProcessingStatus.ERROR,
            original_sentence="test",
            parameterized_sentence="test"
        )
        assert result_error.has_errors() is True
        
        result_success = ExtractionResult(
            status=ProcessingStatus.SUCCESS,
            original_sentence="test",
            parameterized_sentence="test"
        )
        assert result_success.has_errors() is False
    
    def test_needs_llm_processing(self):
        """Test needs_llm_processing method."""
        result_llm = ExtractionResult(
            status=ProcessingStatus.NEEDS_LLM,
            original_sentence="test",
            parameterized_sentence="test"
        )
        assert result_llm.needs_llm_processing() is True
        
        result_success = ExtractionResult(
            status=ProcessingStatus.SUCCESS,
            original_sentence="test",
            parameterized_sentence="test"
        )
        assert result_success.needs_llm_processing() is False
    
    def test_get_all_products(self):
        """Test getting all products from result."""
        group1 = EntityGroup(action="add", products=["rice"])
        group2 = EntityGroup(action="add", products=["beans", "milk"])
        result = ExtractionResult(
            status=ProcessingStatus.SUCCESS,
            original_sentence="test",
            parameterized_sentence="test",
            groups=[group1, group2]
        )
        
        all_products = result.get_all_products()
        assert all_products == ["rice", "beans", "milk"]
    
    def test_get_all_brands(self):
        """Test getting all brands from result."""
        group1 = EntityGroup(action="add", brands=["Nike"])
        group2 = EntityGroup(action="add", brands=["Adidas", "Puma"])
        result = ExtractionResult(
            status=ProcessingStatus.SUCCESS,
            original_sentence="test",
            parameterized_sentence="test",
            groups=[group1, group2]
        )
        
        all_brands = result.get_all_brands()
        assert all_brands == ["Nike", "Adidas", "Puma"]
    
    def test_to_dict(self):
        """Test converting extraction result to dict."""
        group = EntityGroup(action="add", products=["rice"])
        grouping_result = GroupingResult(groups=[group], status="ok")
        
        result = ExtractionResult(
            status=ProcessingStatus.SUCCESS,
            original_sentence="add rice",
            parameterized_sentence="add producttoken",
            groups=[group],
            grouping_result=grouping_result
        )
        
        result_dict = result.to_dict()
        
        assert isinstance(result_dict, dict)
        assert result_dict["status"] == "success"
        assert result_dict["original_sentence"] == "add rice"
        assert "grouped_entities" in result_dict
        assert len(result_dict["grouped_entities"]["groups"]) == 1


class TestIntegration:
    """Integration tests combining multiple types."""
    
    def test_full_pipeline_simulation(self):
        """Test simulating a full pipeline flow with all types."""
        # Stage 1: NLP Extraction
        nlp_result = NLPExtraction(
            products=["rice"],
            units=["kg"],
            quantities=["2"],
            original_sentence="add 2kg rice to cart",
            parameterized_sentence="add 2 unittoken producttoken to cart"
        )
        
        # Stage 2: NER Prediction
        ner_result = NERPrediction(
            tokens=["add", "2", "unittoken", "producttoken", "to", "cart"],
            labels=["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT", "O", "O"],
            scores=[0.99, 0.98, 0.97, 0.96, 0.95, 0.94]
        )
        
        # Stage 3: Entity Grouping
        group = EntityGroup(
            action="add",
            intent="add_to_cart",
            intent_confidence=0.95,
            products=["rice"],
            quantities=["2"],
            units=["kg"]
        )
        
        grouping_result = GroupingResult(
            groups=[group],
            status="ok"
        )
        
        # Final Result
        final_result = ExtractionResult(
            status=ProcessingStatus.SUCCESS,
            original_sentence="add 2kg rice to cart",
            parameterized_sentence="add 2 unittoken producttoken to cart",
            groups=[group],
            nlp_extraction=nlp_result,
            ner_prediction=ner_result,
            grouping_result=grouping_result
        )
        
        # Validate complete flow
        assert final_result.is_successful()
        assert len(final_result.groups) == 1
        assert final_result.get_all_products() == ["rice"]
        assert nlp_result.has_entities()
        assert len(ner_result.tokens) == 6
        assert grouping_result.is_successful()

