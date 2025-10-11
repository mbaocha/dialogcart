#!/usr/bin/env python3
"""
Comprehensive audit script for training data.
Detects mislabeling and inconsistencies.
"""

from ner_training_data import training_examples

# Define known brands and products for validation
KNOWN_BRANDS = {
    # Fashion
    'nike', 'adidas', 'puma', 'zara', 'gucci', 'h&m', 'fendi', 'prada', 'uniqlo',
    'converse', 'reebok', 'burberry', 'levi', 'new', 'balance', 'louis', 'vuitton',
    
    # Beauty
    'fenty', 'mac', 'dior', 'chanel', 'nars', 'nivea', 'olay', 'revlon', 'maybelline',
    'lancome', 'garnier', 'kiehls', 'clinique', 'shiseido', 'estee', 'lauder',
    'urban', 'decay', 'body', 'shop', 'loreal',
    
    # Groceries
    'coca-cola', 'coca', 'cola', 'fanta', 'sprite', 'pepsi', 'heinz', 'nestle',
    'nescafe', 'milo', 'lipton', 'kelloggs', 'dangote', 'peak', 'golden', 'penny',
    'mama', 'gold', 'indomie'
}

KNOWN_PRODUCTS = {
    # Groceries
    'rice', 'beans', 'milk', 'eggs', 'bread', 'sugar', 'salt', 'pepper', 'oil',
    'flour', 'tea', 'coffee', 'yams', 'garri', 'plantain', 'tomatoes', 'onions',
    'carrots', 'garlic', 'bananas', 'apples', 'sardines', 'noodles', 'coke',
    'soda', 'juice', 'wine', 'chocolate', 'pasta', 'cereal', 'biscuits',
    'ketchup', 'yogurt', 'jam', 'potatoes',
    
    # Fashion
    'shoes', 'socks', 'shirts', 'jeans', 'sneakers', 'belts', 'hats', 'shorts',
    'trainers', 'hoodies', 'jackets', 'skirts', 'dresses', 'sandals', 'boots',
    'coats', 'pants', 'handbags', 'wallets',
    
    # Beauty
    'foundation', 'lipstick', 'perfume', 'lotion', 'cream', 'shampoo', 'serum',
    'cleanser', 'sunscreen', 'mascara', 'moisturizer', 'toner', 'nail', 'polish',
    'eyeshadow', 'palette', 'face', 'wash', 'lip', 'gloss'
}

def audit_training_data():
    """Run comprehensive audit on training data."""
    
    issues = []
    word_label_map = {}  # Track how each word is labeled across examples
    
    print("="*80)
    print("COMPREHENSIVE TRAINING DATA AUDIT")
    print("="*80)
    print()
    
    for text, labels in training_examples.items():
        tokens = text.split()
        
        # Check token/label count match
        if len(tokens) != len(labels):
            issues.append({
                'type': 'LENGTH_MISMATCH',
                'text': text,
                'issue': f'Tokens: {len(tokens)}, Labels: {len(labels)}',
                'severity': 'CRITICAL'
            })
            continue
        
        # Analyze each token
        for i, (token, label) in enumerate(zip(tokens, labels)):
            token_lower = token.lower()
            
            # Track labeling consistency
            if token_lower not in word_label_map:
                word_label_map[token_lower] = []
            word_label_map[token_lower].append((label, text))
            
            # Check for known brand mislabeled as product
            if token_lower in KNOWN_BRANDS and 'PRODUCT' in label:
                issues.append({
                    'type': 'BRAND_AS_PRODUCT',
                    'text': text,
                    'token': token,
                    'label': label,
                    'issue': f'"{token}" is a known BRAND but labeled as {label}',
                    'severity': 'HIGH'
                })
            
            # Check for known product mislabeled as brand
            if token_lower in KNOWN_PRODUCTS and 'BRAND' in label:
                issues.append({
                    'type': 'PRODUCT_AS_BRAND',
                    'text': text,
                    'token': token,
                    'label': label,
                    'issue': f'"{token}" is a known PRODUCT but labeled as {label}',
                    'severity': 'HIGH'
                })
            
            # Check for placeholder token mislabeling
            if 'token' in token_lower:
                expected_labels = {
                    'producttoken': ['B-PRODUCT', 'I-PRODUCT'],
                    'brandtoken': ['B-BRAND', 'I-BRAND'],
                    'unittoken': ['B-UNIT', 'I-UNIT'],
                    'varianttoken': ['B-TOKEN', 'I-TOKEN'],
                    'quantitytoken': ['B-QUANTITY', 'I-QUANTITY']
                }
                
                for placeholder, valid_labels in expected_labels.items():
                    if placeholder in token_lower and label not in valid_labels:
                        issues.append({
                            'type': 'PLACEHOLDER_MISLABEL',
                            'text': text,
                            'token': token,
                            'label': label,
                            'expected': valid_labels,
                            'issue': f'"{token}" should be {valid_labels} but is {label}',
                            'severity': 'CRITICAL'
                        })
            
            # Check for I- without preceding B-
            if label.startswith('I-') and i > 0:
                prev_label = labels[i-1]
                entity_type = label.split('-')[1]
                if not (prev_label == f'B-{entity_type}' or prev_label == f'I-{entity_type}'):
                    issues.append({
                        'type': 'INVALID_I_TAG',
                        'text': text,
                        'token': token,
                        'label': label,
                        'prev_label': prev_label,
                        'issue': f'I-{entity_type} at position {i} without proper B- or I- before it',
                        'severity': 'MEDIUM'
                    })
    
    # Check for inconsistent labeling of same word
    inconsistencies = []
    for word, labelings in word_label_map.items():
        if 'token' in word or word in {'?', ',', '.', 'and', 'or', 'in', 'to', 'from', 'of', 'the', 'my', 'a', 'an'}:
            continue  # Skip common words and placeholders
        
        unique_labels = set(label for label, _ in labelings)
        
        # Filter out 'O' for flexibility (some contexts may not tag all entities)
        significant_labels = {l for l in unique_labels if l != 'O'}
        
        # Check if same word has conflicting entity type labels
        if len(significant_labels) > 1:
            # Check if it's a legitimate multi-type situation
            has_brand = any('BRAND' in l for l in significant_labels)
            has_product = any('PRODUCT' in l for l in significant_labels)
            has_token = any('TOKEN' in l for l in significant_labels)
            
            # Brand/Product conflict is serious
            if has_brand and has_product:
                inconsistencies.append({
                    'word': word,
                    'labels': significant_labels,
                    'count': len(labelings),
                    'examples': labelings[:3],  # Show first 3 examples
                    'severity': 'HIGH'
                })
            # TOKEN with other types might be intentional (variants)
            elif has_token and (has_brand or has_product):
                if word not in KNOWN_BRANDS and word not in KNOWN_PRODUCTS:
                    # Might be OK for ambiguous words
                    pass
                else:
                    inconsistencies.append({
                        'word': word,
                        'labels': significant_labels,
                        'count': len(labelings),
                        'examples': labelings[:3],
                        'severity': 'MEDIUM'
                    })
    
    # Print results
    print(f"\n{'='*80}")
    print(f"AUDIT RESULTS: Found {len(issues)} issues")
    print(f"{'='*80}\n")
    
    # Group by severity
    critical = [i for i in issues if i['severity'] == 'CRITICAL']
    high = [i for i in issues if i['severity'] == 'HIGH']
    medium = [i for i in issues if i['severity'] == 'MEDIUM']
    
    if critical:
        print(f"\n🚨 CRITICAL ISSUES ({len(critical)}):")
        print("-" * 80)
        for issue in critical[:10]:  # Show first 10
            print(f"\nType: {issue['type']}")
            print(f"Text: {issue['text']}")
            print(f"Issue: {issue['issue']}")
        if len(critical) > 10:
            print(f"\n... and {len(critical) - 10} more critical issues")
    
    if high:
        print(f"\n⚠️  HIGH SEVERITY ISSUES ({len(high)}):")
        print("-" * 80)
        for issue in high[:10]:
            print(f"\nType: {issue['type']}")
            print(f"Text: {issue['text']}")
            print(f"Issue: {issue['issue']}")
        if len(high) > 10:
            print(f"\n... and {len(high) - 10} more high severity issues")
    
    if medium:
        print(f"\n⚡ MEDIUM SEVERITY ISSUES ({len(medium)}):")
        print("-" * 80)
        for issue in medium[:5]:
            print(f"\nType: {issue['type']}")
            print(f"Text: {issue['text']}")
            print(f"Issue: {issue['issue']}")
        if len(medium) > 5:
            print(f"\n... and {len(medium) - 5} more medium severity issues")
    
    # Print inconsistencies
    if inconsistencies:
        print(f"\n\n🔀 INCONSISTENT LABELING ({len(inconsistencies)}):")
        print("-" * 80)
        for incon in inconsistencies[:10]:
            print(f"\nWord: '{incon['word']}'")
            print(f"Different labels used: {incon['labels']}")
            print(f"Appears {incon['count']} times")
            print("Examples:")
            for label, text in incon['examples']:
                print(f"  - {label:15} in: {text[:60]}...")
        if len(inconsistencies) > 10:
            print(f"\n... and {len(inconsistencies) - 10} more inconsistencies")
    
    # Summary
    print(f"\n\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"Total examples: {len(training_examples)}")
    print(f"Critical issues: {len(critical)}")
    print(f"High severity issues: {len(high)}")
    print(f"Medium severity issues: {len(medium)}")
    print(f"Inconsistent labelings: {len(inconsistencies)}")
    print()
    
    if len(issues) == 0 and len(inconsistencies) == 0:
        print("✅ No major issues found! Training data looks good.")
    else:
        print("⚠️  Issues detected. Review above for details.")
    
    print()
    return issues, inconsistencies


if __name__ == "__main__":
    issues, inconsistencies = audit_training_data()

