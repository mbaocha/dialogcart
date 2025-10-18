#!/usr/bin/env python3
"""
Interactive CLI for Luma Entity Extraction

A REPL (Read-Eval-Print Loop) for testing the entity extraction pipeline interactively.
Useful for manual testing, debugging, and demonstration.

Features:
- Warm startup (preloads models)
- Pretty-printed results
- Real-time extraction testing
- Support for both legacy and typed output formats

Usage:
    python -m luma.cli.interactive
    
    or
    
    cd src
    python luma/cli/interactive.py

Ported from semantics/entity_extraction_pipeline.py lines 415-470 with enhancements.
"""
import sys
from pathlib import Path

# Add src/ to path if running directly
if __name__ == "__main__":
    src_path = Path(__file__).parent.parent.parent
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

from luma.core.pipeline import EntityExtractionPipeline  # noqa: E402


def print_banner():
    """Print welcome banner."""
    print("\n" + "=" * 60)
    print("🎯 Luma Entity Extraction - Interactive Mode")
    print("=" * 60)
    print("\nCommands:")
    print("  - Type a sentence to extract entities")
    print("  - Type 'quit' or 'exit' to quit")
    print("  - Press Ctrl+C to exit")
    print("=" * 60)


def print_result_legacy(result):
    """
    Print extraction result in legacy dict format.
    
    Args:
        result: Dict-based extraction result
    """
    print(f"\n{'Status:':<20} {result.get('status', 'unknown')}")
    print(f"{'Parameterized:':<20} {result.get('parameterized_sentence', '')}")
    
    if result.get('indexed_tokens'):
        print(f"{'Indexed tokens:':<20} {result['indexed_tokens']}")
    
    grouped = result.get('grouped_entities', {})
    groups = grouped.get('groups', [])
    
    if groups:
        print(f"\n{'Extracted Groups:':<20} {len(groups)} group(s)\n")
        for i, group in enumerate(groups, 1):
            print(f"  📦 Group {i}:")
            print(f"     Action:      {group.get('action', 'None')}")
            print(f"     Intent:      {group.get('intent', 'None')}")
            print(f"     Products:    {group.get('products', [])}")
            print(f"     Brands:      {group.get('brands', [])}")
            print(f"     Quantities:  {group.get('quantities', [])}")
            print(f"     Units:       {group.get('units', [])}")
            print(f"     Variants:    {group.get('variants', [])}")
            
            if group.get('ordinal_ref'):
                print(f"     Ordinal:     {group.get('ordinal_ref')}")
    else:
        print("\n⚠️  No groups extracted")
    
    if result.get('notes'):
        print(f"\n{'Notes:':<20} {result['notes']}")


def print_result_typed(result):
    """
    Print extraction result in typed ExtractionResult format.
    
    Args:
        result: ExtractionResult object
    """
    print(f"\n{'Status:':<20} {result.status.value}")
    print(f"{'Parameterized:':<20} {result.parameterized_sentence}")
    
    if result.groups:
        print(f"\n{'Extracted Groups:':<20} {len(result.groups)} group(s)\n")
        for i, group in enumerate(result.groups, 1):
            print(f"  📦 Group {i}:")
            print(f"     Action:      {group.action or 'None'}")
            print(f"     Intent:      {group.intent or 'None'}")
            print(f"     Products:    {group.products}")
            print(f"     Brands:      {group.brands}")
            print(f"     Quantities:  {group.quantities}")
            print(f"     Units:       {group.units}")
            print(f"     Variants:    {group.variants}")
            
            if group.ordinal_ref:
                print(f"     Ordinal:     {group.ordinal_ref}")
    else:
        print("\n⚠️  No groups extracted")
    
    if hasattr(result, 'notes') and result.notes:
        print(f"\n{'Notes:':<20} {result.notes}")


def interactive_main(use_typed: bool = False):
    """
    Interactive mode for testing entity extraction.
    
    Args:
        use_typed: If True, use typed ExtractionResult format
    
    NOTE: Matches semantics/entity_extraction_pipeline.py lines 415-470 exactly
    """
    print_banner()
    
    # Initialize and warm up pipeline
    print("\n⏳ Initializing pipeline (this may take a few seconds)...")
    try:
        pipeline = EntityExtractionPipeline(
            use_luma=True,
            enable_llm_fallback=False
        )
        print("✅ Pipeline ready!\n")
    except Exception as e:  # noqa: BLE001
        print(f"❌ Failed to initialize pipeline: {e}")
        return
    
    # Main REPL loop
    while True:
        try:
            # Get user input
            sentence = input("\n💬 Enter sentence: ").strip()
            
            # Check for exit commands
            if not sentence or sentence.lower() in ['quit', 'exit', 'q']:
                print("\n👋 Goodbye!")
                break
            
            # Process sentence
            print(f"\n⚙️  Processing: {sentence}")
            print("-" * 60)
            
            try:
                if use_typed:
                    # Get typed result
                    result = pipeline.extract(sentence)
                    print_result_typed(result)
                else:
                    # Get dict result
                    result_dict = pipeline.extract_dict(sentence)
                    print_result_legacy(result_dict)
            
            except Exception as e:  # noqa: BLE001
                print(f"❌ Error during extraction: {e}")
                import traceback
                traceback.print_exc()
            
            print("\n" + "=" * 60)
        
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        
        except Exception as e:  # noqa: BLE001
            print(f"\n❌ Error: {e}")
            print("Please try again.\n")


def main():
    """Entry point for the interactive CLI."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Luma Entity Extraction - Interactive Mode",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--typed',
        action='store_true',
        help='Use typed ExtractionResult format (default: legacy dict)'
    )
    
    args = parser.parse_args()
    
    try:
        interactive_main(use_typed=args.typed)
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
    except Exception as e:  # noqa: BLE001
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

