#!/usr/bin/env python3
"""
Example usage of the new validate_by_intent function for modify_cart intent.
"""

from validator import validate_by_intent, validate_modify_cart

def example_usage():
    """Example of how to use the new intent-specific validation."""
    
    # Example Rasa response for modify_cart intent
    rasa_response = {
        "result": {
            "actions": [
                {"action": "add", "product": "rice", "quantity": 2.0, "unit": "kg"}
            ],
            "intent": "modify_cart",
            "confidence_score": 0.95
        }
    }
    
    user_text = "add 2kg rice to cart"
    
    # Method 1: Using validate_by_intent with specific synonyms
    print("=== Method 1: validate_by_intent ===")
    result1 = validate_by_intent(
        user_text=user_text,
        rasa_response=rasa_response,
        intent="modify_cart",
        allowed_synonyms=["add", "remove", "set"]  # Only these synonyms will be used
    )
    print(f"Result: {result1}")
    print(f"Route: {result1['route']}")
    print(f"Allowed synonyms used: {result1['allowed_synonyms']}")
    
    # Method 2: Using the convenience function for modify_cart
    print("\n=== Method 2: validate_modify_cart (convenience function) ===")
    result2 = validate_modify_cart(user_text, rasa_response)
    print(f"Result: {result2}")
    print(f"Route: {result2['route']}")
    
    # Method 3: Compare with legacy validator (uses ALL synonyms)
    print("\n=== Method 3: Legacy validator (all synonyms) ===")
    from validator import validate_rasa_response
    result3 = validate_rasa_response(user_text, rasa_response)
    print(f"Result: {result3}")
    print(f"Route: {result3['route']}")

if __name__ == "__main__":
    example_usage()
