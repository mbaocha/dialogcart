#!/usr/bin/env python3
"""
Extended test harness for the grouping layer.
"""

from copy import deepcopy
from pprint import pprint
import re
import sys
import os

# Add the current directory to the path to import local modules
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

# Import the functions we need to test
from entity_grouping import group_entities_action_centric
from extract_entities import extract_entities



def run_full_pipeline_test():
    """Test the complete extract_entities pipeline with real sentences."""
    print("\n=== FULL PIPELINE TESTS (using extract_entities) ===\n")
    
    test_sentences = [
        "Add 2 bags of rice and 1 Gucci bag",
        "Remove 3 bottles of Coca Cola and add 5 packs of Indomie noodles",
        "Please check if you have Dangote sugar in stock",
        "I want to buy 1 kg of beans and 2 cartons of milk",
        "Cancel my order of 4 pairs of Nike shoes",
        "Add 5kg of Dangote rice and 2 bottles of Coca Cola",
        "Remove 3 cartons of Indomie noodles",
        "Add 3 cartons of Indomie noodles and 2 bags of Golden Penny pasta",
        "Add 5 bottles of Pepsi, 3 bottles of Fanta, and 2 bottles of Sprite",
        "Remove 10 bags of rice or 5 bags of beans",
        "Check if you have Coke, and if yes remove 2 bottles from cart",
        "Remove that one",
        "Do you sell red blue shirt or orange pant?"
    ]
    
    for i, sentence in enumerate(test_sentences, 1):
        print(f"\n>>> Test {i}: {sentence}")
        print("-" * 60)
        
        try:
            result = extract_entities(sentence)
            
            print(f"Status: {result['status']}")
            print(f"Parameterized: {result['parameterized_sentence']}")
            
            if result.get('indexed_tokens'):
                print(f"Indexed tokens: {result['indexed_tokens']}")
            
            if result['grouped_entities'].get('groups'):
                print("\nExtracted Groups:")
                for j, group in enumerate(result['grouped_entities']['groups']):
                    print(f"  Group {j+1}:")
                    print(f"    Action: {group.get('action', 'None')}")
                    print(f"    Intent: {group.get('intent', 'None')}")
                    print(f"    Products: {group.get('products', [])}")
                    print(f"    Brands: {group.get('brands', [])}")
                    print(f"    Quantities: {group.get('quantities', [])}")
                    print(f"    Units: {group.get('units', [])}")
                    print(f"    Tokens: {group.get('tokens', [])}")
                    print(f"    Status: {group.get('group_status', 'unknown')}")
            else:
                print("No groups extracted")
            
            if result.get('notes'):
                print(f"\nNotes: {result['notes']}")
                
        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()
        
        print("-" * 60)


def run_grouping_test():
    print("\n=== EXTENDED GROUPING TESTS (legacy) ===\n")
    test_cases = [
    # --- Basic parallel actions ---
    {
        "sentence": "Add 5kg of Dangote rice and 2 bottles of Coca Cola",
        "tokens": ["add", "5", "unittoken", "brandtoken", "producttoken", "2", "unittoken", "brandtoken"],
        "labels": ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-BRAND", "B-PRODUCT", "B-QUANTITY", "B-UNIT", "B-BRAND"]
    },
    {
        "sentence": "Add 2 bags of rice and 1 Gucci bag",
        "tokens": ["add", "2", "unittoken", "producttoken", "1", "brandtoken", "producttoken"],
        "labels": ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT", "B-QUANTITY", "B-BRAND", "B-PRODUCT"]
    },
    {
        "sentence": "Remove 3 cartons of Indomie noodles",
        "tokens": ["remove", "3", "unittoken", "brandtoken", "producttoken"],
        "labels": ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-BRAND", "B-PRODUCT"]
    },

    # --- Multi-product / multi-brand ---
    {
        "sentence": "Add 3 cartons of Indomie noodles and 2 bags of Golden Penny pasta",
        "tokens": ["add", "3", "unittoken", "brandtoken", "producttoken", "2", "unittoken", "brandtoken", "producttoken"],
        "labels": ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-BRAND", "B-PRODUCT",
                   "B-QUANTITY", "B-UNIT", "B-BRAND", "B-PRODUCT"]
    },
    {
        "sentence": "Add 5 bottles of Pepsi, 3 bottles of Fanta, and 2 bottles of Sprite",
        "tokens": ["add", "5", "unittoken", "brandtoken", "3", "unittoken", "brandtoken", "2", "unittoken", "brandtoken"],
        "labels": ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-BRAND",
                   "B-QUANTITY", "B-UNIT", "B-BRAND",
                   "B-QUANTITY", "B-UNIT", "B-BRAND"]
    },
    {
        "sentence": "Remove 10 bags of rice or 5 bags of beans",
        "tokens": ["remove", "10", "unittoken", "producttoken", "5", "unittoken", "producttoken"],
        "labels": ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT",
                   "B-QUANTITY", "B-UNIT", "B-PRODUCT"]
    },

    {
        "sentence": "Check if you have Coke, and if yes remove 2 bottles from cart",
        "tokens": ["check", "if", "you", "have", "brandtoken", "and", "if", "yes", "remove", "2", "unittoken", "from", "cart"],
        "labels": ["B-ACTION", "I-ACTION", "I-ACTION", "I-ACTION", "B-BRAND",
                   "O", "O", "O", "B-ACTION", "B-QUANTITY", "B-UNIT", "O", "O"]
    },
    {
        "sentence": "Check if you have Fanta then add 6 bottles to cart",
        "tokens": ["check", "if", "you", "have", "brandtoken", "then", "add", "6", "unittoken", "to", "cart"],
        "labels": ["B-ACTION", "I-ACTION", "I-ACTION", "I-ACTION", "B-BRAND",
                   "O", "B-ACTION", "B-QUANTITY", "B-UNIT", "O", "O"]
    },
    {
        "sentence": "Do you have bread? If yes, remove 1 bag of beans and add 2 bags of rice",
        "tokens": ["do", "you", "have", "producttoken", "if", "yes", "remove", "1", "unittoken", "producttoken",
                   "and", "add", "2", "unittoken", "producttoken"],
        "labels": ["B-ACTION", "I-ACTION", "I-ACTION", "B-PRODUCT", "O", "O",
                   "B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT",
                   "O", "B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT"]
    },

    # --- Multi-intent cross actions ---
    {
        "sentence": "Add 2kg of rice and 3 bottles of Pepsi then check cart",
        "tokens": ["add", "2", "unittoken", "producttoken", "3", "unittoken", "brandtoken", "then", "check", "cart"],
        "labels": ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT",
                   "B-QUANTITY", "B-UNIT", "B-BRAND", "O", "B-ACTION", "O"]
    },
    {
        "sentence": "Add 5kg of Dangote rice then remove 2kg beans",
        "tokens": ["add", "5", "unittoken", "brandtoken", "producttoken", "then", "remove", "2", "unittoken", "producttoken"],
        "labels": ["B-ACTION", "B-QUANTITY", "B-UNIT", "B-BRAND", "B-PRODUCT",
                   "O", "B-ACTION", "B-QUANTITY", "B-UNIT", "B-PRODUCT"]
    }
    ]

    extended_cases = [
        # 1️⃣ Deferred quantities
    {
        "sentence": "Add rice, beans and yam to cart. 5kg, 6kg and 9kg respectively",
        "tokens": ["add", "rice", ",", "beans", "and", "yam", "to", "cart", ".", "5", "kg", ",", "6", "kg", "and", "9", "kg", "respectively"],
        "labels": ["B-ACTION", "B-PRODUCT", "O", "B-PRODUCT", "O", "B-PRODUCT", "O", "O", "O", "B-QUANTITY", "B-UNIT", "O", "B-QUANTITY", "B-UNIT", "O", "B-QUANTITY", "B-UNIT", "O"]
    },

    {
        "sentence": "Add rice, beans and yam. 5kg, 6 and 9kg respectively",
        "tokens": ["add", "rice", ",", "beans", "and", "yam", ".", "5", "kg", ",", "6", "and", "9", "kg", "respectively"],
        "labels": ["B-ACTION", "B-PRODUCT", "O", "B-PRODUCT", "O", "B-PRODUCT", "O", "B-QUANTITY", "B-UNIT", "O", "B-QUANTITY", "O", "B-QUANTITY", "B-UNIT", "O"]
    },

    {
        "sentence": "Add rice, beans and yam. 5kg, 6kg, 9kg",
        "tokens": ["add", "rice", ",", "beans", "and", "yam", ".", "5", "kg", ",", "6", "kg", ",", "9", "kg"],
        "labels": ["B-ACTION", "B-PRODUCT", "O", "B-PRODUCT", "O", "B-PRODUCT", "O", "B-QUANTITY", "B-UNIT", "O", "B-QUANTITY", "B-UNIT", "O", "B-QUANTITY", "B-UNIT"]
    },

    {
        "sentence": "Add Coca Cola, Fanta and Sprite 3 bottles each",
        "tokens": ["add", "coca", "cola", ",", "fanta", "and", "sprite", "3", "bottles", "each"],
        "labels": ["B-ACTION", "B-BRAND", "I-BRAND", "O", "B-BRAND", "O", "B-BRAND", "B-QUANTITY", "B-UNIT", "O"]
    },

    {
        "sentence": "Do you sell rice?",
        "tokens": ["do", "you", "sell", "rice", "?"],
        "labels": ["O", "O", "B-ACTION", "B-PRODUCT", "O"]
    },

    {
        "sentence": "Add it to cart",
        "tokens": ["add", "it", "to", "cart"],
        "labels": ["B-ACTION", "O", "O", "O"]
    },

    {
        "sentence": "Remove that one",
        "tokens": ["remove", "that", "one"],
        "labels": ["B-ACTION", "O", "O"]
    },
    {
    "sentence": "Do you sell red blue shirt or orange pant?",
    "tokens": ["do", "you", "sell", "red", "blue", "shirt", "or", "orange", "pant", "?"],
    "labels": ["B-ACTION", "I-ACTION", "I-ACTION", "B-TOKEN", "I-TOKEN", "B-PRODUCT", "O", "B-TOKEN", "B-PRODUCT", "O"]
    }

    ]

    # Combine everything
    test_cases = test_cases + extended_cases

    for case in test_cases:
        print(f"\n>>> {case['sentence']}")
        print(f"[DEBUG] Tokens: {len(case['tokens'])}, Labels: {len(case['labels'])}")
        if len(case['tokens']) != len(case['labels']):
            print(f"[ERROR] Mismatch: tokens={case['tokens']}, labels={case['labels']}")
            continue
        groups = group_entities_action_centric(case["tokens"], case["labels"])
        pprint(groups)
        print("-" * 60)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--legacy":
        run_grouping_test()
    else:
        run_full_pipeline_test()
