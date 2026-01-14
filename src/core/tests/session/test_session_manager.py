"""
Unit tests for session manager.

Tests session save/load, TTL expiry, and clear operations.
"""

import pytest
import json
from unittest.mock import Mock, patch

from core.session.session_manager import (
    get_session,
    save_session,
    clear_session,
    SESSION_TTL_SECONDS,
    SESSION_KEY_PREFIX,
)


class TestSaveLoad:
    """Test save and load operations."""
    
    def test_save_then_load_returns_same_data(self):
        """Test that saving a session and then loading it returns the same data."""
        user_id = "test_user_123"
        session_state = {
            "intent": "CREATE_APPOINTMENT",
            "slots": {"service": "haircut", "date": "2024-01-01"},
            "missing_slots": ["time"],
            "status": "NEEDS_CLARIFICATION"
        }
        
        mock_redis = Mock()
        mock_redis.get.return_value = json.dumps(session_state).encode('utf-8')
        
        with patch('core.session.session_manager._get_redis_client', return_value=mock_redis):
            save_session(user_id, session_state)
            result = get_session(user_id)
            
            # Verify save was called
            expected_key = f"{SESSION_KEY_PREFIX}{user_id}"
            mock_redis.setex.assert_called_once()
            call_args = mock_redis.setex.call_args
            assert call_args[0][0] == expected_key
            assert call_args[0][1] == SESSION_TTL_SECONDS
            assert json.loads(call_args[0][2]) == session_state
            
            # Verify load was called
            mock_redis.get.assert_called_once_with(expected_key)
            
            # Verify result matches input
            assert result == session_state
    
    def test_load_returns_none_when_session_not_found(self):
        """Test that loading a non-existent session returns None."""
        user_id = "non_existent_user"
        
        mock_redis = Mock()
        mock_redis.get.return_value = None
        
        with patch('core.session.session_manager._get_redis_client', return_value=mock_redis):
            result = get_session(user_id)
            
            assert result is None
            expected_key = f"{SESSION_KEY_PREFIX}{user_id}"
            mock_redis.get.assert_called_once_with(expected_key)
    
    def test_save_with_valid_session_schema(self):
        """Test that save works with valid session schema."""
        user_id = "test_user"
        session_state = {
            "intent": "CREATE_RESERVATION",
            "slots": {"room_type": "suite"},
            "missing_slots": ["check_in_date", "check_out_date"],
            "status": "NEEDS_CLARIFICATION"
        }
        
        mock_redis = Mock()
        
        with patch('core.session.session_manager._get_redis_client', return_value=mock_redis):
            save_session(user_id, session_state)
            
            # Verify setex was called with correct parameters
            call_args = mock_redis.setex.call_args
            serialized = call_args[0][2]
            assert json.loads(serialized) == session_state
    
    def test_save_resets_ttl(self):
        """Test that save resets TTL on each call."""
        user_id = "test_user"
        session_state = {
            "intent": "CREATE_APPOINTMENT",
            "slots": {},
            "missing_slots": ["service"],
            "status": "NEEDS_CLARIFICATION"
        }
        
        mock_redis = Mock()
        
        with patch('core.session.session_manager._get_redis_client', return_value=mock_redis):
            # Save multiple times
            save_session(user_id, session_state)
            save_session(user_id, session_state)
            save_session(user_id, session_state)
            
            # Verify setex was called 3 times, each with TTL reset
            assert mock_redis.setex.call_count == 3
            for call in mock_redis.setex.call_args_list:
                assert call[0][1] == SESSION_TTL_SECONDS


class TestTTLExpiry:
    """Test TTL expiry behavior."""
    
    def test_load_returns_none_after_expiry(self):
        """Test that loading an expired session returns None."""
        user_id = "expired_user"
        
        mock_redis = Mock()
        mock_redis.get.return_value = None  # Redis returns None for expired keys
        
        with patch('core.session.session_manager._get_redis_client', return_value=mock_redis):
            result = get_session(user_id)
            
            assert result is None
            expected_key = f"{SESSION_KEY_PREFIX}{user_id}"
            mock_redis.get.assert_called_once_with(expected_key)
    
    def test_ttl_is_set_correctly(self):
        """Test that TTL is set to expected value (20 minutes)."""
        user_id = "test_user"
        session_state = {
            "intent": "MODIFY_BOOKING",
            "slots": {"booking_id": "ABC123"},
            "missing_slots": ["new_date"],
            "status": "NEEDS_CLARIFICATION"
        }
        
        mock_redis = Mock()
        
        with patch('core.session.session_manager._get_redis_client', return_value=mock_redis):
            save_session(user_id, session_state)
            
            call_args = mock_redis.setex.call_args
            ttl = call_args[0][1]
            assert ttl == SESSION_TTL_SECONDS
            assert ttl == 20 * 60  # 20 minutes in seconds


class TestClear:
    """Test clear operations."""
    
    def test_clear_deletes_session(self):
        """Test that clear deletes the session from Redis."""
        user_id = "test_user"
        
        mock_redis = Mock()
        mock_redis.delete.return_value = 1
        
        with patch('core.session.session_manager._get_redis_client', return_value=mock_redis):
            clear_session(user_id)
            
            expected_key = f"{SESSION_KEY_PREFIX}{user_id}"
            mock_redis.delete.assert_called_once_with(expected_key)
    
    def test_clear_after_save_removes_session(self):
        """Test that clearing after saving removes the session."""
        user_id = "test_user"
        session_state = {
            "intent": "CANCEL_BOOKING",
            "slots": {"booking_id": "XYZ789"},
            "missing_slots": [],
            "status": "READY"
        }
        
        mock_redis = Mock()
        mock_redis.get.return_value = None  # After clear, get returns None
        
        with patch('core.session.session_manager._get_redis_client', return_value=mock_redis):
            save_session(user_id, session_state)
            clear_session(user_id)
            result = get_session(user_id)
            
            assert result is None
            expected_key = f"{SESSION_KEY_PREFIX}{user_id}"
            mock_redis.delete.assert_called_once_with(expected_key)


class TestRedisUnavailable:
    """Test behavior when Redis is not available."""
    
    def test_get_returns_none_when_redis_unavailable(self):
        """Test that get returns None when Redis is not available."""
        user_id = "test_user"
        
        with patch('core.session.session_manager._get_redis_client', return_value=None):
            result = get_session(user_id)
            assert result is None
    
    def test_save_silently_fails_when_redis_unavailable(self):
        """Test that save fails silently when Redis is not available."""
        user_id = "test_user"
        session_state = {
            "intent": "CREATE_APPOINTMENT",
            "slots": {},
            "missing_slots": [],
            "status": "READY"
        }
        
        with patch('core.session.session_manager._get_redis_client', return_value=None):
            # Should not raise
            save_session(user_id, session_state)
    
    def test_clear_silently_fails_when_redis_unavailable(self):
        """Test that clear fails silently when Redis is not available."""
        user_id = "test_user"
        
        with patch('core.session.session_manager._get_redis_client', return_value=None):
            # Should not raise
            clear_session(user_id)


class TestSerialization:
    """Test JSON serialization."""
    
    def test_save_load_preserves_json_types(self):
        """Test that JSON serialization preserves data types."""
        user_id = "test_user"
        session_state = {
            "intent": "CREATE_APPOINTMENT",
            "slots": {
                "service": "haircut",
                "count": 2,
                "price": 50.99,
                "active": True,
                "tags": ["urgent", "priority"]
            },
            "missing_slots": ["date", "time"],
            "status": "NEEDS_CLARIFICATION"
        }
        
        mock_redis = Mock()
        mock_redis.get.return_value = json.dumps(session_state).encode('utf-8')
        
        with patch('core.session.session_manager._get_redis_client', return_value=mock_redis):
            save_session(user_id, session_state)
            result = get_session(user_id)
            
            assert result == session_state
            assert isinstance(result["slots"]["count"], int)
            assert isinstance(result["slots"]["price"], float)
            assert isinstance(result["slots"]["active"], bool)
            assert isinstance(result["missing_slots"], list)

