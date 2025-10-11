#!/usr/bin/env python3
"""
Test script for the extract_entities pipeline.
Demonstrates how to use the entity extraction functionality.
"""

import sys
import os

# Add the current directory to the path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

from extract_entities import extract_entities, extract_entities_simple, extract_entities_with_confidence


def test_basic_extraction():
    """Test basic entity extraction functionality."""
    
    print("=== Basic Entity Extraction Test ===")
    
    test_sentence = "Add 2 bags of rice and 1 Gucci bag"
    
    # Test the main function
    result = extract_entities(test_sentence, debug=True)
    
    print(f"\nInput: {test_sentence}")
    print(f"Status: {result['status']}")
    print(f"Parameterized: {result['parameterized_sentence']}")
    
    if result['grouped_entities'].get('groups'):
        print("\nExtracted Groups:")
        for i, group in enumerate(result['grouped_entities']['groups']):
            print(f"  Group {i+1}: {group}")
    
    return result


def test_simple_extraction():
    """Test the simplified extraction function."""
    
    print("\n=== Simple Extraction Test ===")
    
    test_sentences = [
        "Remove 3 bottles of Coca Cola",
        "Add 5 packs of Indomie noodles",
        "Check if you have Dangote sugar"
    ]
    
    for sentence in test_sentences:
        print(f"\nInput: {sentence}")
        entities = extract_entities_simple(sentence)
        
        if entities:
            print("Extracted entities:")
            for i, entity in enumerate(entities):
                print(f"  {i+1}. {entity}")
        else:
            print("No entities extracted")


def test_confidence_extraction():
    """Test extraction with confidence scores."""
    
    print("\n=== Confidence Extraction Test ===")
    
    test_sentence = "I want to buy 1 kg of beans and 2 cartons of milk"
    
    result = extract_entities_with_confidence(test_sentence)
    
    print(f"Input: {test_sentence}")
    print(f"Status: {result['status']}")
    print(f"Summary: {result['summary']}")
    
    if result.get('confidence_scores'):
        print(f"Confidence scores: {result['confidence_scores']}")
    
    if result['grouped_entities'].get('groups'):
        print("\nExtracted Groups with Confidence:")
        for i, group in enumerate(result['grouped_entities']['groups']):
            print(f"  Group {i+1}: {group}")


def interactive_test():
    """Interactive test mode."""
    
    print("\n=== Interactive Test Mode ===")
    print("Enter sentences to test entity extraction (type 'quit' to exit)")
    
    while True:
        try:
            sentence = input("\nEnter sentence: ").strip()
            
            if sentence.lower() in ['quit', 'exit', 'q']:
                break
            
            if not sentence:
                continue
            
            print(f"\nProcessing: {sentence}")
            result = extract_entities(sentence, debug=False)
            
            print(f"Status: {result['status']}")
            print(f"Parameterized: {result['parameterized_sentence']}")
            
            if result['grouped_entities'].get('groups'):
                print("Extracted Groups:")
                for i, group in enumerate(result['grouped_entities']['groups']):
                    print(f"  {i+1}. {group}")
            else:
                print("No groups extracted")
            
            if result['notes']:
                print(f"Notes: {result['notes']}")
                
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except (ValueError, KeyError, ImportError) as e:
            print(f"Error: {e}")


def main():
    """Main test function."""
    
    print("Entity Extraction Pipeline Test")
    print("=" * 50)
    
    try:
        # Run basic tests
        test_basic_extraction()
        test_simple_extraction()
        test_confidence_extraction()
        
        # Ask if user wants interactive mode
        print("\n" + "=" * 50)
        choice = input("Run interactive test mode? (y/n): ").strip().lower()
        
        if choice in ['y', 'yes']:
            interactive_test()
        
    except (ValueError, KeyError, ImportError, FileNotFoundError) as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
