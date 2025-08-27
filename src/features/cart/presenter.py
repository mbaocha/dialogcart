"""
Cart presenter layer for presentation logic and data formatting.
"""

from typing import Dict, Any, List


class CartPresenter:
    """Presenter class for cart data formatting and presentation."""
    
    def format_cart_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Format a single cart item for presentation."""
        if not item:
            return {}
        
        return {
            'id': item.get('id'),
            'user_id': item.get('user_id'),
            'product_id': item.get('product_id'),
            'quantity': item.get('quantity'),
            'price': item.get('price'),
            'created_at': item.get('created_at'),
            'updated_at': item.get('updated_at')
        }
    
    def format_cart_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Format a list of cart items for presentation."""
        return [self.format_cart_item(item) for item in items]
    
    def format_cart_summary(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Format a cart summary for presentation."""
        if not items:
            return {
                'total_items': 0,
                'total_quantity': 0,
                'total_price': 0.0,
                'items': []
            }
        
        total_items = len(items)
        total_quantity = sum(float(item.get('quantity', 0)) for item in items)
        total_price = sum(float(item.get('price', 0)) for item in items)
        
        return {
            'total_items': total_items,
            'total_quantity': total_quantity,
            'total_price': round(total_price, 2),
            'items': self.format_cart_items(items)
        }
    
    def format_add_item_response(self, added_item: Dict[str, Any], cart_contents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Format the response when adding an item to cart."""
        return {
            'added_item': self.format_cart_item(added_item),
            'cart_contents': self.format_cart_summary(cart_contents)
        }
    
    def format_remove_item_response(self, success: bool) -> Dict[str, Any]:
        """Format the response when removing an item from cart."""
        return {
            'removed': success
        }
    
    def format_update_quantity_response(self, success: bool) -> Dict[str, Any]:
        """Format the response when updating cart quantity."""
        return {
            'updated': success
        }
    
    def format_clear_cart_response(self, removed_count: int) -> Dict[str, Any]:
        """Format the response when clearing the cart."""
        return {
            'removed_items': removed_count
        }
    
    def format_error_response(self, error_message: str, error_code: str = None) -> Dict[str, Any]:
        """Format error response for presentation."""
        response = {
            'success': False,
            'error': error_message
        }
        if error_code:
            response['error_code'] = error_code
        return response
    
    def format_success_response(self, data: Any = None, message: str = "Operation completed successfully") -> Dict[str, Any]:
        """Format success response for presentation."""
        response = {
            'success': True,
            'message': message
        }
        if data is not None:
            response['data'] = data
        return response 