#!/usr/bin/env python3
"""
Test script to demonstrate phone number lookup and save_user (upsert) functionality.
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))

from db.user import UserDB
from features.user import get_user, get_user_by_phone, is_user_registered, save_user

def test_phone_lookup_and_save():
    """Test the phone number lookup and save_user (upsert) functionality."""
    
    # Initialize the database
    db = UserDB()
    
    print("=== Testing Phone Number Lookup and Save User Functionality ===\n")
    
    # Test 1: Create a user with phone number using save_user
    print("1. Creating test user using save_user...")
    test_user = save_user(
        user_id="test-user-001",
        first_name="John",
        last_name="Doe",
        email="john@example.com",
        phone="+4472122345556",
        source="whatsapp"
    )
    print(f"   Created user: {test_user['data']['user_id']} with phone: {test_user['data']['phone']}")
    print(f"   Success: {test_user['success']}\n")
    
    # Test 2: Get user by user_id
    print("2. Getting user by user_id...")
    user_by_id = db.get_user("test-user-001")
    print(f"   Found user: {user_by_id['first_name']} {user_by_id['last_name']}")
    print(f"   User ID: {user_by_id['user_id']}\n")
    
    # Test 3: Get user by phone number using new method
    print("3. Getting user by phone number using get_user_by_phone...")
    user_by_phone = db.get_user_by_phone("+4472122345556")
    print(f"   Found user: {user_by_phone['first_name']} {user_by_phone['last_name']}")
    print(f"   User ID: {user_by_phone['user_id']}")
    print(f"   Phone: {user_by_phone['phone']}\n")
    
    # Test 4: Test UPSERT behavior - update existing user with same phone
    print("4. Testing UPSERT behavior - updating existing user...")
    updated_user = save_user(
        user_id="test-user-002",  # Different user_id but same phone
        first_name="John",
        last_name="Smith",  # Changed last name
        email="john.smith@example.com",  # Changed email
        phone="+4472122345556",  # Same phone number
        source="whatsapp"
    )
    print(f"   Updated user result: {updated_user['success']}")
    if updated_user['success']:
        user_data = updated_user['data']
        print(f"   Updated user: {user_data['first_name']} {user_data['last_name']}")
        print(f"   New email: {user_data['email']}")
        print(f"   User ID: {user_data['user_id']}")
        print(f"   Phone: {user_data['phone']}\n")
    
    # Test 5: Verify the update worked by checking phone lookup
    print("5. Verifying update worked with phone lookup...")
    verify_user = db.get_user_by_phone("+4472122345556")
    print(f"   Found user after update: {verify_user['first_name']} {verify_user['last_name']}")
    print(f"   Email: {verify_user['email']}")
    print(f"   User ID: {verify_user['user_id']}\n")
    
    # Test 6: Check if user is registered by phone
    print("6. Checking if user is registered by phone...")
    is_registered = db.is_user_registered("+4472122345556")
    print(f"   Is registered: {is_registered}\n")
    
    # Test 7: Test API function get_user_by_phone
    print("7. Testing API function get_user_by_phone...")
    api_result = get_user_by_phone("+4472122345556")
    print(f"   API result: {api_result['success']}")
    if api_result['success']:
        user_data = api_result['data']
        print(f"   User data: {user_data['first_name']} {user_data['last_name']}")
        print(f"   User ID: {user_data['user_id']}")
        print(f"   Phone: {user_data['phone']}\n")
    
    # Test 8: Test is_user_registered API
    print("8. Testing is_user_registered API...")
    reg_result = is_user_registered("+4472122345556")
    print(f"   Registration check: {reg_result['data']['is_registered']}")
    if reg_result['data']['is_registered']:
        print(f"   User ID: {reg_result['data']['user_id']}\n")
    
    # Test 9: Test creating a completely new user
    print("9. Testing creating a completely new user...")
    new_user = save_user(
        user_id="test-user-003",
        first_name="Jane",
        last_name="Wilson",
        email="jane@example.com",
        phone="+4472122345557",  # Different phone number
        source="web"
    )
    print(f"   New user result: {new_user['success']}")
    if new_user['success']:
        user_data = new_user['data']
        print(f"   New user: {user_data['first_name']} {user_data['last_name']}")
        print(f"   Email: {user_data['email']}")
        print(f"   Phone: {user_data['phone']}\n")
    
    # Test 10: Test non-existent phone
    print("10. Testing non-existent phone...")
    non_existent = db.get_user_by_phone("+99999999999")
    print(f"   Non-existent user result: {non_existent}\n")
    
    # Test 11: Test API with non-existent phone
    print("11. Testing API with non-existent phone...")
    api_non_existent = get_user_by_phone("+99999999999")
    print(f"   API result: {api_non_existent['success']}")
    print(f"   Error: {api_non_existent.get('error', 'No error')}\n")
    
    print("=== All tests completed! ===")

if __name__ == "__main__":
    test_phone_lookup_and_save() 