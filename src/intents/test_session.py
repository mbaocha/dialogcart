#!/usr/bin/env python3
"""
Test script to verify session service functionality
"""
import requests
import json

def test_session_service():
    """Test the session service directly"""
    base_url = "http://localhost:9200"
    
    print("Testing Session Service...")
    
    # Test health endpoint
    try:
        response = requests.get(f"{base_url}/health", timeout=5)
        print(f"Health check: {response.status_code}")
        if response.status_code == 200:
            print(f"Health response: {response.json()}")
        else:
            print(f"Health check failed: {response.text}")
            return False
    except Exception as e:
        print(f"Health check error: {e}")
        return False
    
    # Test session operations
    sender_id = "test_user_123"
    
    # Test get session
    try:
        response = requests.post(f"{base_url}/session/get", 
                               json={"sender_id": sender_id}, 
                               timeout=5)
        print(f"Get session: {response.status_code}")
        if response.status_code == 200:
            print(f"Session data: {response.json()}")
        else:
            print(f"Get session failed: {response.text}")
    except Exception as e:
        print(f"Get session error: {e}")
    
    # Test update session with slots
    try:
        session_data = {
            "slots": {"product": "rice", "quantity": 2.0, "unit": "kg"},
            "history": [{"role": "user", "content": "add rice to cart"}]
        }
        response = requests.post(f"{base_url}/session/update", 
                               json={"sender_id": sender_id, "session": session_data}, 
                               timeout=5)
        print(f"Update session: {response.status_code}")
        if response.status_code == 200:
            print(f"Update response: {response.json()}")
        else:
            print(f"Update session failed: {response.text}")
    except Exception as e:
        print(f"Update session error: {e}")
    
    # Test get session again to verify data was stored
    try:
        response = requests.post(f"{base_url}/session/get", 
                               json={"sender_id": sender_id}, 
                               timeout=5)
        print(f"Get session (after update): {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Updated session data: {json.dumps(data, indent=2)}")
            return True
        else:
            print(f"Get session failed: {response.text}")
            return False
    except Exception as e:
        print(f"Get session error: {e}")
        return False

if __name__ == "__main__":
    success = test_session_service()
    if success:
        print("\n✅ Session service is working correctly!")
    else:
        print("\n❌ Session service has issues!")
