#!/usr/bin/env python3
"""
Manual LLM review of training data with corrections.
Reviews each example and suggests fixes.
"""

from ner_training_data import training_examples

def review_example(text, labels):
    """
    Review a single example and return issues found.
    """
    tokens = text.split()
    issues = []
    
    # Known entities
    BRANDS = {
        'nike', 'adidas', 'puma', 'zara', 'gucci', 'h&m', 'fendi', 'prada', 'uniqlo',
        'converse', 'reebok', 'burberry', 'levi', 'new', 'balance', 'louis', 'vuitton',
        'fenty', 'mac', 'dior', 'chanel', 'nars', 'nivea', 'olay', 'revlon', 'maybelline',
        'lancome', 'garnier', 'kiehls', 'clinique', 'shiseido', 'estee', 'lauder',
        'urban', 'decay', 'body', 'shop', 'loreal', 'coca-cola', 'coca', 'cola', 'fanta',
        'sprite', 'pepsi', 'heinz', 'nestle', 'nescafe', 'milo', 'lipton', 'kelloggs',
        'dangote', 'peak', 'golden', 'penny', 'mama', 'gold', 'indomie'
    }
    
    PRODUCTS = {
        'rice', 'beans', 'milk', 'eggs', 'bread', 'sugar', 'salt', 'pepper', 'oil',
        'flour', 'tea', 'coffee', 'yams', 'garri', 'plantain', 'tomatoes', 'onions',
        'carrots', 'garlic', 'bananas', 'apples', 'sardines', 'noodles', 'coke', 'soda',
        'juice', 'wine', 'chocolate', 'pasta', 'cereal', 'biscuits', 'ketchup', 'yogurt',
        'shoes', 'socks', 'shirts', 'jeans', 'sneakers', 'belts', 'hats', 'shorts',
        'trainers', 'hoodies', 'jackets', 'skirts', 'dresses', 'sandals', 'boots',
        'coats', 'pants', 'handbags', 'wallets', 'foundation', 'lipstick', 'perfume',
        'lotion', 'cream', 'shampoo', 'serum', 'cleanser', 'sunscreen', 'mascara',
        'moisturizer', 'toner', 'nail', 'polish', 'eyeshadow', 'palette', 'face',
        'wash', 'lip', 'gloss', 'jam', 'fish'
    }
    
    # Check length
    if len(tokens) != len(labels):
        return [{
            'severity': 'CRITICAL',
            'issue': f'Length mismatch: {len(tokens)} tokens vs {len(labels)} labels',
            'suggestion': None
        }]
    
    # Check each token
    for i, (token, label) in enumerate(zip(tokens, labels)):
        token_lower = token.lower()
        
        # Brand checks
        if token_lower in BRANDS:
            if 'PRODUCT' in label:
                issues.append({
                    'severity': 'HIGH',
                    'position': i,
                    'token': token,
                    'label': label,
                    'issue': f'Known BRAND "{token}" labeled as {label}',
                    'suggestion': 'B-BRAND' if label.startswith('B-') else 'I-BRAND'
                })
        
        # Product checks  
        if token_lower in PRODUCTS:
            if 'BRAND' in label:
                issues.append({
                    'severity': 'HIGH',
                    'position': i,
                    'token': token,
                    'label': label,
                    'issue': f'Known PRODUCT "{token}" labeled as {label}',
                    'suggestion': 'B-PRODUCT' if label.startswith('B-') else 'I-PRODUCT'
                })
    
    return issues

def main():
    print("="*80)
    print("MANUAL LLM REVIEW OF TRAINING DATA")
    print("="*80)
    print(f"\nReviewing {len(training_examples)} examples...")
    print()
    
    all_issues = []
    critical_count = 0
    high_count = 0
    
    for text, labels in training_examples.items():
        issues = review_example(text, labels)
        if issues:
            for issue in issues:
                all_issues.append({
                    'text': text,
                    'labels': labels,
                    **issue
                })
                if issue['severity'] == 'CRITICAL':
                    critical_count += 1
                elif issue['severity'] == 'HIGH':
                    high_count += 1
    
    # Print results
    print(f"Review complete!")
    print(f"Total issues found: {len(all_issues)}")
    print(f"  - Critical: {critical_count}")
    print(f"  - High: {high_count}")
    print()
    
    if critical_count > 0:
        print("\n🚨 CRITICAL ISSUES:")
        print("-"*80)
        for item in [i for i in all_issues if i['severity'] == 'CRITICAL'][:10]:
            print(f"\nText: {item['text']}")
            print(f"Issue: {item['issue']}")
            if item.get('suggestion'):
                print(f"Suggestion: {item['suggestion']}")
    
    if high_count > 0:
        print("\n⚠️  HIGH SEVERITY ISSUES:")
        print("-"*80)
        for item in [i for i in all_issues if i['severity'] == 'HIGH'][:20]:
            tokens = item['text'].split()
            print(f"\nText: {item['text']}")
            print(f"Position {item['position']}: '{item['token']}' -> {item['label']}")
            print(f"Issue: {item['issue']}")
            print(f"Suggested fix: {item['token']} -> {item['suggestion']}")
    
    if len(all_issues) == 0:
        print("\n✅ ALL EXAMPLES VALIDATED! No issues found.")
        print("   Training data is ready for model training.")
    else:
        print(f"\n⚠️  Found {len(all_issues)} issues to review.")
    
    print()

if __name__ == "__main__":
    main()

