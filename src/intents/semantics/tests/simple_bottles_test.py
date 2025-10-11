#!/usr/bin/env python3
"""
Simple test to debug the bottles issue.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nlp_processor import init_nlp_with_entities

def simple_bottles_test():
    print("=== SIMPLE BOTTLES TEST ===\n")
    
    # Initialize NLP and entities
    nlp, entities = init_nlp_with_entities()
    
    # Test text
    test_text = "Add 10 Peak milk bottles"
    print(f"Test text: '{test_text}'")
    
    # Process with spaCy
    doc = nlp(test_text)
    
    print(f"\nTokens: {[t.text for t in doc]}")
    print(f"Entities found: {len(doc.ents)}")
    
    for ent in doc.ents:
        print(f"  {ent.text} → {ent.label_} (pos {ent.start}-{ent.end})")
        if "bottles" in ent.text.lower():
            print(f"    *** BOTTLES FOUND: '{ent.text}' → {ent.label_} ***")
        if "milk" in ent.text.lower():
            print(f"    *** MILK FOUND: '{ent.text}' → {ent.label_} ***")

if __name__ == "__main__":
    simple_bottles_test()
