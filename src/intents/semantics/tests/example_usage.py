#!/usr/bin/env python3
"""
Example usage of the extract_entities pipeline.
Shows how to integrate the entity extraction into your application.
"""

import sys
import os

# Add the current directory to the path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

from extract_entities import extract_entities, extract_entities_simple


def example_basic_usage():
    """Basic usage example."""
    
    print("=== Basic Usage Example ===")
    
    # Example sentence
    sentence = "Add 2 bags of rice and remove 1 bottle of Coca Cola"
    
    # Extract entities
    result = extract_entities(sentence)
    
    print(f"Input: {sentence}")
    print(f"Status: {result['status']}")
    print(f"Parameterized: {result['parameterized_sentence']}")
    
    # Process the results
    if result['status'] == 'success':
        groups = result['grouped_entities']['groups']
        print(f"\nFound {len(groups)} entity groups:")
        
        for i, group in enumerate(groups):
            print(f"\nGroup {i+1}:")
            print(f"  Action: {group.get('action', 'None')}")
            print(f"  Intent: {group.get('intent', 'None')}")
            print(f"  Products: {group.get('products', [])}")
            print(f"  Brands: {group.get('brands', [])}")
            print(f"  Quantities: {group.get('quantities', [])}")
            print(f"  Units: {group.get('units', [])}")
            print(f"  Status: {group.get('group_status', 'unknown')}")
    else:
        print(f"Extraction failed: {result['notes']}")


def example_simple_usage():
    """Simple usage example for quick entity extraction."""
    
    print("\n=== Simple Usage Example ===")
    
    sentences = [
        "Add 3 bottles of Pepsi",
        "Remove 1 kg of sugar",
        "Check if you have Nike shoes"
    ]
    
    for sentence in sentences:
        print(f"\nInput: {sentence}")
        
        # Simple extraction - returns just the groups
        groups = extract_entities_simple(sentence)
        
        if groups:
            print("Extracted entities:")
            for group in groups:
                action = group.get('action', 'None')
                products = group.get('products', [])
                brands = group.get('brands', [])
                quantities = group.get('quantities', [])
                units = group.get('units', [])
                
                print(f"  - {action}: {quantities} {units} of {brands} {products}")
        else:
            print("No entities extracted")


def example_error_handling():
    """Example of error handling."""
    
    print("\n=== Error Handling Example ===")
    
    # Test with empty sentence
    result = extract_entities("")
    print(f"Empty sentence result: {result['status']}")
    
    # Test with very short sentence
    result = extract_entities("hi")
    print(f"Short sentence result: {result['status']}")
    
    # Test with complex sentence
    result = extract_entities("Add 2 bags of rice and 1 Gucci bag and remove 3 bottles of Coca Cola and check if you have Nike shoes")
    print(f"Complex sentence result: {result['status']}")
    print(f"Groups found: {len(result['grouped_entities'].get('groups', []))}")


def example_integration():
    """Example of how to integrate into a larger application."""
    
    print("\n=== Integration Example ===")
    
    def process_user_command(user_input):
        """Process a user command and return structured data."""
        
        # Extract entities
        result = extract_entities(user_input)
        
        if result['status'] != 'success':
            return {
                'success': False,
                'error': result['notes'][0] if result['notes'] else 'Unknown error',
                'entities': []
            }
        
        # Convert to application-specific format
        entities = []
        for group in result['grouped_entities']['groups']:
            entity = {
                'action': group.get('action'),
                'intent': group.get('intent'),
                'products': group.get('products', []),
                'brands': group.get('brands', []),
                'quantities': group.get('quantities', []),
                'units': group.get('units', []),
                'confidence': group.get('intent_confidence', 0.0)
            }
            entities.append(entity)
        
        return {
            'success': True,
            'entities': entities,
            'original_sentence': result['original_sentence'],
            'parameterized_sentence': result['parameterized_sentence']
        }
    
    # Test the integration
    test_commands = [
        "Add 2 bags of rice",
        "Remove 1 bottle of Coca Cola",
        "Check if you have Nike shoes in stock"
    ]
    
    for command in test_commands:
        print(f"\nProcessing: {command}")
        result = process_user_command(command)
        
        if result['success']:
            print(f"Success! Found {len(result['entities'])} entity groups:")
            for entity in result['entities']:
                print(f"  - {entity['action']}: {entity['quantities']} {entity['units']} of {entity['brands']} {entity['products']}")
        else:
            print(f"Failed: {result['error']}")


def main():
    """Run all examples."""
    
    print("Entity Extraction Pipeline - Usage Examples")
    print("=" * 60)
    
    try:
        example_basic_usage()
        example_simple_usage()
        example_error_handling()
        example_integration()
        
        print("\n" + "=" * 60)
        print("All examples completed successfully!")
        
    except (ValueError, KeyError, ImportError, FileNotFoundError) as e:
        print(f"Example failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
