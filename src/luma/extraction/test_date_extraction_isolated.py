#!/usr/bin/env python3
"""
Isolated test script for date extraction (relative and absolute dates).

Tests date extraction independently without requiring full luma package dependencies.
Directly uses entity_loading and entity_processing modules.
"""

import sys
import json
from pathlib import Path
from typing import Dict, Any, List

# Add src directory to path
script_dir = Path(__file__).parent.resolve()  # extraction/
luma_dir = script_dir.parent  # luma/
src_dir = luma_dir.parent  # src/

src_path = str(src_dir)
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Mock luma.config.debug_print before importing modules that need it
import sys
from unittest.mock import MagicMock

# Create a mock luma.config module
mock_config = MagicMock()
mock_config.debug_print = lambda *args, **kwargs: None  # No-op function
sys.modules['luma'] = MagicMock()
sys.modules['luma.config'] = mock_config

# Import directly from files to avoid luma package init
import importlib.util

# Load normalization module first (it's imported by entity_processing)
normalization_path = script_dir / "normalization.py"
spec_norm = importlib.util.spec_from_file_location("luma.extraction.normalization", normalization_path)
normalization = importlib.util.module_from_spec(spec_norm)
normalization.__package__ = "luma.extraction"
normalization.__name__ = "luma.extraction.normalization"
sys.modules["luma.extraction.normalization"] = normalization
spec_norm.loader.exec_module(normalization)

# Load entity_loading module directly
entity_loading_path = script_dir / "entity_loading.py"
spec_loading = importlib.util.spec_from_file_location("luma.extraction.entity_loading", entity_loading_path)
entity_loading = importlib.util.module_from_spec(spec_loading)
entity_loading.__package__ = "luma.extraction"
entity_loading.__name__ = "luma.extraction.entity_loading"
sys.modules["luma.extraction.entity_loading"] = entity_loading
spec_loading.loader.exec_module(entity_loading)

# Load entity_processing module directly
entity_processing_path = script_dir / "entity_processing.py"
spec_processing = importlib.util.spec_from_file_location("luma.extraction.entity_processing", entity_processing_path)
entity_processing = importlib.util.module_from_spec(spec_processing)
entity_processing.__package__ = "luma.extraction"
entity_processing.__name__ = "luma.extraction.entity_processing"
sys.modules["luma.extraction.entity_processing"] = entity_processing
spec_processing.loader.exec_module(entity_processing)

# Get the functions we need
init_nlp_with_service_families = entity_loading.init_nlp_with_service_families
build_date_patterns = entity_loading.build_date_patterns
build_absolute_date_patterns = entity_loading.build_absolute_date_patterns
extract_entities_from_doc = entity_processing.extract_entities_from_doc


def print_date_result(doc, result: Dict[str, List], test_input: str):
    """Pretty print date extraction result."""
    print("\n" + "=" * 70)
    print(f"Input: {test_input}")
    print("=" * 70)
    
    # Show tokenization
    tokens = [t.text for t in doc]
    print(f"\n🔤 Tokens: {tokens}")
    
    # Relative dates
    dates = result.get("dates", [])
    if dates:
        print(f"\n📅 Relative Dates ({len(dates)}):")
        for i, date in enumerate(dates, 1):
            text = date.get("text", "N/A")
            position = date.get("position", "N/A")
            length = date.get("length", "N/A")
            start = position
            end = position + length
            span_tokens = tokens[start:end] if isinstance(start, int) else []
            print(f"   {i}. '{text}' [pos: {position}, len: {length}, span: {start}-{end}]")
            print(f"      Tokens: {span_tokens}")
    else:
        print(f"\n📅 Relative Dates: (none)")
    
    # Absolute dates
    dates_absolute = result.get("dates_absolute", [])
    if dates_absolute:
        print(f"\n📆 Absolute Dates ({len(dates_absolute)}):")
        for i, date in enumerate(dates_absolute, 1):
            text = date.get("text", "N/A")
            position = date.get("position", "N/A")
            length = date.get("length", "N/A")
            start = position
            end = position + length
            span_tokens = tokens[start:end] if isinstance(start, int) else []
            print(f"   {i}. '{text}' [pos: {position}, len: {length}, span: {start}-{end}]")
            print(f"      Tokens: {span_tokens}")
    else:
        print(f"\n📆 Absolute Dates: (none)")
    
    # Show all entities found
    print(f"\n🔍 All Entities Found:")
    for ent in doc.ents:
        print(f"   - {ent.label_}: '{ent.text}' [span: {ent.start}-{ent.end}]")
    
    print()


def run_test_case(nlp, test_input: str, description: str = ""):
    """Run a single test case and print results."""
    if description:
        print(f"\n{'='*70}")
        print(f"TEST: {description}")
        print('='*70)
    
    try:
        result, doc = extract_entities_from_doc(nlp, test_input)
        print_date_result(doc, result, test_input)
        return result, doc
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def main():
    """Run isolated date extraction tests."""
    print("=" * 70)
    print("ISOLATED DATE EXTRACTION TEST SUITE")
    print("=" * 70)
    
    # Find global JSON - try config/data first, fallback to store/normalization
    config_data_dir = src_dir / "luma" / "config" / "data"
    store_dir = src_dir / "luma" / "store" / "normalization"
    base_dir = config_data_dir if config_data_dir.exists() else store_dir
    global_json_path = base_dir / "global.v1.json"
    
    if not global_json_path.exists():
        print(f"ERROR: Global JSON not found at: {global_json_path}")
        return
    
    print(f"\nUsing config: {global_json_path}")
    
    # Initialize spaCy with date patterns only
    print("\nInitializing spaCy with date patterns...")
    try:
        nlp, _ = init_nlp_with_service_families(global_json_path)
        print("spaCy initialized successfully\n")
    except ImportError as e:
        print(f"ERROR: Missing dependency - {e}")
        print("\nTo run this test, install spaCy:")
        print("  pip install spacy")
        print("  python -m spacy download en_core_web_sm")
        return
    except Exception as e:
        print(f"ERROR: Failed to initialize spaCy: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Test cases
    test_cases = [
        # Relative dates (should still work)
        ("book me for haircut today", "Relative date: today"),
        ("schedule appointment tomorrow", "Relative date: tomorrow"),
        ("book next week", "Relative date: next week"),
        ("tonight please", "Relative date: tonight"),
        
        # Absolute dates - Day + Month format
        ("book me for haircut on 15th dec", "Absolute date: day+month (15th dec)"),
        ("schedule on 15 dec", "Absolute date: day+month (15 dec)"),
        ("appointment on 15 december", "Absolute date: day+month (15 december)"),
        ("book 15th december 2025", "Absolute date: day+month+year (15th december 2025)"),
        ("on 5 jan", "Absolute date: day+month (5 jan)"),
        ("on 25th feb", "Absolute date: day+month (25th feb)"),
        
        # Absolute dates - Month + Day format
        ("book dec 15", "Absolute date: month+day (dec 15)"),
        ("schedule dec 15th", "Absolute date: month+day (dec 15th)"),
        ("appointment december 15", "Absolute date: month+day (december 15)"),
        ("book december 15th 2025", "Absolute date: month+day+year (december 15th 2025)"),
        ("on jan 5", "Absolute date: month+day (jan 5)"),
        ("on feb 25th", "Absolute date: month+day (feb 25th)"),
        
        # Absolute dates - Numeric format
        ("book on 15/12", "Absolute date: numeric (15/12)"),
        ("schedule 15/12/2025", "Absolute date: numeric (15/12/2025)"),
        ("appointment 15-12-2025", "Absolute date: numeric (15-12-2025)"),
        ("book 5/1", "Absolute date: numeric (5/1)"),
        ("on 25-2-2025", "Absolute date: numeric (25-2-2025)"),
        
        # Mixed scenarios
        ("book today or 15th dec", "Mixed: relative OR absolute date"),
        ("appointment tomorrow or dec 15", "Mixed: relative OR absolute date"),
        
        # Edge cases
        ("schedule haircut and beard trim on 15th dec", "Edge: multiple services + absolute date"),
        ("on 15/12/2025 please", "Edge: absolute date with noise"),
        
        # Should NOT match (malformed) - these might still match due to regex, but shouldn't be valid
        ("book on 32nd dec", "Malformed: invalid day (may still match pattern)"),
        ("schedule on 15/13/2025", "Malformed: invalid month (may still match pattern)"),
        ("appointment on dec 32", "Malformed: invalid day (may still match pattern)"),
    ]
    
    print("\n" + "=" * 70)
    print("RUNNING TEST CASES")
    print("=" * 70)
    
    passed = 0
    failed = 0
    
    for test_input, description in test_cases:
        result, doc = run_test_case(nlp, test_input, description)
        if result is not None:
            passed += 1
        else:
            failed += 1
    
    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"📊 Total:  {passed + failed}")
    print("=" * 70)
    
    # Interactive mode
    print("\n" + "=" * 70)
    print("INTERACTIVE MODE")
    print("=" * 70)
    print("Enter your own test cases (type 'quit' to exit):\n")
    
    while True:
        try:
            user_input = input("> ").strip()
            if user_input.lower() in ['quit', 'exit', 'q']:
                break
            if not user_input:
                continue
            
            result, doc = run_test_case(nlp, user_input, "User input")
        except KeyboardInterrupt:
            print("\n\nExiting...")
            break
        except EOFError:
            break


if __name__ == "__main__":
    main()

