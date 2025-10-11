#!/usr/bin/env python3
"""
Script to fix annotation span mismatches in training examples.
This script removes extra 'O' labels at the end of label sequences to match token counts.
"""

import re

def fix_annotation_spans():
    """Fix annotation span mismatches by removing extra 'O' labels."""
    
    # Read the file
    with open('ner_training_data.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Pattern to match training examples with their labels
    pattern = r'(\s*"[^"]+":\s*\[[^\]]+\],)'
    
    def fix_labels(match):
        line = match.group(1)
        
        # Extract the text and labels
        text_match = re.search(r'"([^"]+)"', line)
        labels_match = re.search(r'\[([^\]]+)\]', line)
        
        if not text_match or not labels_match:
            return line
        
        text = text_match.group(1)
        labels_str = labels_match.group(1)
        
        # Parse labels
        labels = [label.strip().strip("'\"") for label in labels_str.split(',')]
        
        # Count tokens in text (split by spaces)
        tokens = text.split()
        token_count = len(tokens)
        label_count = len(labels)
        
        # If labels have more elements than tokens, remove extra 'O' labels from the end
        if label_count > token_count:
            # Remove extra 'O' labels from the end
            while len(labels) > token_count and labels[-1] == 'O':
                labels.pop()
            
            # Reconstruct the line
            labels_str = ', '.join([f"'{label}'" for label in labels])
            return f'    "{text}": [{labels_str}],\n'
        
        return line
    
    # Apply the fix
    fixed_content = re.sub(pattern, fix_labels, content)
    
    # Write back to file
    with open('ner_training_data.py', 'w', encoding='utf-8') as f:
        f.write(fixed_content)
    
    print("Fixed annotation span mismatches!")

if __name__ == "__main__":
    fix_annotation_spans()
