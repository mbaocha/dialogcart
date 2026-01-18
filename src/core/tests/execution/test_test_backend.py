"""
Tests for test execution backend.
"""

import os
import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from core.routing.execution.test_backend import TestExecutionBackend
from core.routing.execution.config import get_execution_mode, EXECUTION_MODE_TEST, EXECUTION_MODE_PRODUCTION


def test_test_backend_create_service_booking():
    """Test test backend creates service booking deterministically."""
    TestExecutionBackend.reset_counter()
    
    result = TestExecutionBackend.create_booking(
        organization_id=1,
        customer_id=100,
        booking_type="service",
        item_id=5,
        start_time="2026-01-15T10:00:00+00:00",
        end_time="2026-01-15T11:00:00+00:00",
        staff_id=2,
    )
    
    assert result["booking_code"] == "TEST-BOOKING-001"
    assert result["code"] == "TEST-BOOKING-001"
    assert result["status"] == "pending"
    assert result["booking"]["booking_type"] == "service"
    assert result["booking"]["organization_id"] == 1
    assert result["booking"]["customer_id"] == 100
    assert result["booking"]["item_id"] == 5
    assert result["booking"]["starts_at"] == "2026-01-15T10:00:00+00:00"
    assert result["booking"]["ends_at"] == "2026-01-15T11:00:00+00:00"
    assert result["booking"]["total_amount"] == 0


def test_test_backend_create_reservation_booking():
    """Test test backend creates reservation booking deterministically."""
    TestExecutionBackend.reset_counter()
    
    result = TestExecutionBackend.create_booking(
        organization_id=2,
        customer_id=200,
        booking_type="reservation",
        item_id=10,
        check_in="2026-01-20T14:00:00+00:00",
        check_out="2026-01-22T11:00:00+00:00",
        guests=2,
    )
    
    assert result["booking_code"] == "TEST-BOOKING-001"
    assert result["booking"]["booking_type"] == "reservation"
    assert result["booking"]["organization_id"] == 2
    assert result["booking"]["customer_id"] == 200
    assert result["booking"]["item_id"] == 10
    assert result["booking"]["starts_at"] == "2026-01-20T14:00:00+00:00"
    assert result["booking"]["ends_at"] == "2026-01-22T11:00:00+00:00"
    assert result["booking"]["reservation_fee"] == 0


def test_test_backend_get_booking():
    """Test test backend gets booking deterministically."""
    result = TestExecutionBackend.get_booking("TEST-CODE-123")
    
    assert result["booking"]["booking_code"] == "TEST-CODE-123"
    assert result["booking"]["code"] == "TEST-CODE-123"
    assert result["booking"]["status"] == "pending"
    assert "data" in result


def test_test_backend_update_booking():
    """Test test backend updates booking deterministically."""
    result = TestExecutionBackend.update_booking(
        booking_code="TEST-UPDATE-001",
        organization_id=1,
        updates={"status": "confirmed"}
    )
    
    assert result["booking"]["booking_code"] == "TEST-UPDATE-001"
    # Updates should be applied (status from updates dict)
    assert result["booking"]["status"] == "confirmed"
    assert result["booking"]["organization_id"] == 1


def test_test_backend_cancel_booking():
    """Test test backend cancels booking deterministically."""
    result = TestExecutionBackend.cancel_booking(
        booking_code="TEST-CANCEL-001",
        organization_id=1,
        cancellation_type="user_initiated",
        reason="Changed plans",
    )
    
    assert result["status"] == "cancelled"
    assert result["booking_code"] == "TEST-CANCEL-001"


def test_test_backend_booking_code_increment():
    """Test that booking codes increment deterministically."""
    TestExecutionBackend.reset_counter()
    
    result1 = TestExecutionBackend.create_booking(
        organization_id=1,
        customer_id=1,
        booking_type="service",
        item_id=1,
        start_time="2026-01-15T10:00:00+00:00",
        end_time="2026-01-15T11:00:00+00:00",
    )
    
    result2 = TestExecutionBackend.create_booking(
        organization_id=1,
        customer_id=1,
        booking_type="service",
        item_id=1,
        start_time="2026-01-15T12:00:00+00:00",
        end_time="2026-01-15T13:00:00+00:00",
    )
    
    assert result1["booking_code"] == "TEST-BOOKING-001"
    assert result2["booking_code"] == "TEST-BOOKING-002"


def test_execution_mode_config():
    """Test execution mode configuration."""
    # Save original value
    original_mode = os.environ.get("CORE_EXECUTION_MODE")
    
    try:
        # Test default (production)
        if "CORE_EXECUTION_MODE" in os.environ:
            del os.environ["CORE_EXECUTION_MODE"]
        # Note: get_execution_mode() will return production as default
        # We can't easily test the default without mocking, but we can test explicit values
        
        # Test test mode
        os.environ["CORE_EXECUTION_MODE"] = EXECUTION_MODE_TEST
        assert get_execution_mode() == EXECUTION_MODE_TEST
        
        # Test production mode
        os.environ["CORE_EXECUTION_MODE"] = EXECUTION_MODE_PRODUCTION
        assert get_execution_mode() == EXECUTION_MODE_PRODUCTION
        
    finally:
        # Restore original value
        if original_mode is not None:
            os.environ["CORE_EXECUTION_MODE"] = original_mode
        elif "CORE_EXECUTION_MODE" in os.environ:
            del os.environ["CORE_EXECUTION_MODE"]


if __name__ == "__main__":
    print("=" * 50)
    print("Test Execution Backend Tests")
    print("=" * 50)
    print()
    
    try:
        test_test_backend_create_service_booking()
        print("[OK] test_test_backend_create_service_booking")
        
        test_test_backend_create_reservation_booking()
        print("[OK] test_test_backend_create_reservation_booking")
        
        test_test_backend_get_booking()
        print("[OK] test_test_backend_get_booking")
        
        test_test_backend_update_booking()
        print("[OK] test_test_backend_update_booking")
        
        test_test_backend_cancel_booking()
        print("[OK] test_test_backend_cancel_booking")
        
        test_test_backend_booking_code_increment()
        print("[OK] test_test_backend_booking_code_increment")
        
        test_execution_mode_config()
        print("[OK] test_execution_mode_config")
        
        print()
        print("=" * 50)
        print("[OK] All tests passed!")
        print("=" * 50)
        sys.exit(0)
    except AssertionError as e:
        print()
        print("=" * 50)
        print(f"[FAIL] Test failed: {e}")
        print("=" * 50)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print()
        print("=" * 50)
        print(f"[ERROR] Error: {e}")
        print("=" * 50)
        import traceback
        traceback.print_exc()
        sys.exit(1)

