import pytest
from unittest.mock import Mock, patch
from features.cart.service import CartService


@pytest.fixture
def mock_cart_repo():
    return Mock()


@pytest.fixture
def mock_product_repo():
    return Mock()


@pytest.fixture
def cart_service(mock_cart_repo, mock_product_repo):
    with patch('features.cart.service.ProductRepo') as mock_product_repo_class:
        mock_product_repo_class.return_value = mock_product_repo
        service = CartService(repo=mock_cart_repo)
        return service


class TestCartService:
    
    def test_restore_cart_success(self, cart_service, mock_cart_repo):
        """Test successful cart restoration"""
        # Mock the repository response
        mock_cart_repo.restore_cart.return_value = {
            "restored": True,
            "merge": True,
            "added_from_backup": 2,
            "increased_existing": 1,
            "backup_id": "backup-123"
        }
        
        # Mock the get_cart response for updated cart contents
        mock_cart_repo.get_cart.return_value = [
            {"product_id": "prod-1", "quantity": 3, "product_name": "Test Product"}
        ]
        
        result = cart_service.restore_cart("user-123")
        
        assert result["success"] is True
        assert result["data"]["restored"] is True
        assert result["data"]["merge"] is True
        assert result["data"]["added_from_backup"] == 2
        assert result["data"]["increased_existing"] == 1
        assert result["data"]["backup_id"] == "backup-123"
        assert "cart_contents" in result["data"]
        
        # Verify repository methods were called
        mock_cart_repo.restore_cart.assert_called_once_with("user-123")
        mock_cart_repo.get_cart.assert_called_once_with("user-123")
    
    def test_restore_cart_no_backup(self, cart_service, mock_cart_repo):
        """Test cart restoration when no backup exists"""
        mock_cart_repo.restore_cart.return_value = {
            "restored": False,
            "reason": "no_backup"
        }
        
        result = cart_service.restore_cart("user-123")
        
        assert result["success"] is False
        assert result["error"] == "no_backup"
        
        mock_cart_repo.restore_cart.assert_called_once_with("user-123")
        mock_cart_repo.get_cart.assert_not_called()
    
    def test_restore_cart_expired_backup(self, cart_service, mock_cart_repo):
        """Test cart restoration when backup has expired"""
        mock_cart_repo.restore_cart.return_value = {
            "restored": False,
            "reason": "backup_expired"
        }
        
        result = cart_service.restore_cart("user-123")
        
        assert result["success"] is False
        assert result["error"] == "backup_expired"
    
    def test_restore_cart_missing_user_id(self, cart_service):
        """Test cart restoration with missing user_id"""
        result = cart_service.restore_cart("")
        
        assert result["success"] is False
        assert result["error"] == "user_id is required"
    
    def test_restore_cart_exception(self, cart_service, mock_cart_repo):
        """Test cart restoration when an exception occurs"""
        mock_cart_repo.restore_cart.side_effect = Exception("Database error")
        
        result = cart_service.restore_cart("user-123")
        
        assert result["success"] is False
        assert result["error"] == "Database error"
    
    def test_restore_cart_with_none_user_id(self, cart_service):
        """Test cart restoration with None user_id"""
        result = cart_service.restore_cart(None)
        
        assert result["success"] is False
        assert result["error"] == "user_id is required" 