#!/usr/bin/env python3
"""
Fix detected mislabeling issues in training data.
"""

# Corrections for mislabeled examples
CORRECTIONS = {
    # Fix: "Is Olay foundation in pink available ? please add 3 bottles"
    "Is Olay foundation in pink available ? please add 3 bottles": 
        ['B-ACTION', 'B-BRAND', 'B-PRODUCT', 'O', 'B-TOKEN', 'I-ACTION', 'O', 'O',
         'B-ACTION', 'B-QUANTITY', 'B-UNIT'],
    
    # Fix: "Do you carry brandtoken producttoken ? add 5 bottles of Coke"
    # Coke is actually a BRAND (short for Coca-Cola brand)
    "Do you carry brandtoken producttoken ? add 5 bottles of Coke": 
        ['B-ACTION', 'I-ACTION', 'I-ACTION', 'B-BRAND', 'B-PRODUCT', 'O', 
         'B-ACTION', 'B-QUANTITY', 'B-UNIT', 'O', 'B-BRAND'],
    
    # Additional fixes for "Are" pattern (I-ACTION should come after B-ACTION)
    # These are all "Are BRAND PRODUCT" patterns where "in stock ?" ends the sentence
}

def show_corrections():
    print("="*80)
    print("TRAINING DATA FIXES")
    print("="*80)
    print()
    
    for text, new_labels in CORRECTIONS.items():
        tokens = text.split()
        print(f"Text: {text}")
        print(f"Tokens ({len(tokens)}): {tokens}")
        print(f"New Labels ({len(new_labels)}): {new_labels}")
        print("\nMapping:")
        for i, (t, l) in enumerate(zip(tokens, new_labels)):
            print(f"  {i+1:2}. {t:20} -> {l}")
        print("\n" + "-"*80 + "\n")

if __name__ == "__main__":
    show_corrections()
    
    print("\n✅ Review the corrections above.")
    print("   To apply these fixes, update the corresponding lines in ner_training_data.py")

