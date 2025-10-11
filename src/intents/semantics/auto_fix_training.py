#!/usr/bin/env python3
"""
Automatically fix all mislabeled training examples.
"""

import re

def fix_are_in_stock_pattern(text, labels):
    """
    Fix the 'Are ... in stock ?' pattern where 'in stock' is incorrectly
    labeled as I-ACTION I-ACTION.
    
    Correct labeling: 'in' and 'stock' should be 'O'.
    """
    tokens = text.split()
    
    # Check if this is an "Are ... in stock ?" pattern
    if not (text.startswith('Are ') and 'in stock' in text):
        return labels
    
    # Find positions of 'in' and 'stock'
    try:
        # Find 'in stock' sequence
        for i in range(len(tokens) - 1):
            if tokens[i] == 'in' and tokens[i+1] == 'stock':
                # Fix these positions to 'O'
                if i < len(labels) and labels[i] in ['I-ACTION', 'B-ACTION']:
                    labels[i] = 'O'
                if i+1 < len(labels) and labels[i+1] in ['I-ACTION', 'B-ACTION']:
                    labels[i+1] = 'O'
                break
    except:
        pass
    
    return labels

def generate_fixed_file():
    """Generate corrected training data file."""
    from ner_training_data import (
        modify_cart_examples, check_examples, multi_intent_examples,
        modify_cart_placeholder_examples, check_placeholder_examples, 
        multi_intent_placeholder_examples
    )
    
    all_examples = {
        'modify_cart_examples': modify_cart_examples,
        'check_examples': check_examples,
        'multi_intent_examples': multi_intent_examples,
        'modify_cart_placeholder_examples': modify_cart_placeholder_examples,
        'check_placeholder_examples': check_placeholder_examples,
        'multi_intent_placeholder_examples': multi_intent_placeholder_examples
    }
    
    fixes_applied = 0
    
    print("Analyzing and fixing training data...")
    print()
    
    # Specific fixes
    specific_fixes = {
        "Is Olay foundation in pink available ? please add 3 bottles": 
            ['B-ACTION', 'B-BRAND', 'B-PRODUCT', 'O', 'B-TOKEN', 'I-ACTION', 'O', 'O',
             'B-ACTION', 'B-QUANTITY', 'B-UNIT'],
    }
    
    for category_name, examples_dict in all_examples.items():
        for text in list(examples_dict.keys()):
            labels = list(examples_dict[text])
            original_labels = labels.copy()
            
            # Apply specific fixes first
            if text in specific_fixes:
                labels = specific_fixes[text]
                if labels != original_labels:
                    print(f"✓ Fixed: {text[:50]}...")
                    fixes_applied += 1
                    examples_dict[text] = labels
                    continue
            
            # Apply pattern-based fixes
            if text.startswith('Are ') and 'in stock' in text:
                labels = fix_are_in_stock_pattern(text, labels)
                if labels != original_labels:
                    print(f"✓ Fixed 'Are...in stock' pattern: {text[:50]}...")
                    fixes_applied += 1
                    examples_dict[text] = labels
    
    print(f"\n✅ Applied {fixes_applied} fixes!")
    print("\nRe-run audit to verify:")
    print("  python audit_training_data.py")
    
    return fixes_applied

if __name__ == "__main__":
    generate_fixed_file()

