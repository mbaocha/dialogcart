"""
DynamoDB Table: b_users

Partition key: user_id (string)

Attributes:
    - user_id (string, PK)
    - first_name (string)
    - last_name (string)
    - email (string)
    - phone (string)
    - source (string)         # e.g., 'whatsapp', 'web', 'telegram'
    - consent_time (string, ISO8601 or epoch)
    - status (string)         # 'active', 'inprogress', 'disabled', etc.
    - is_admin (bool)         # NEW: admin flag, default False
    - created_at (string, ISO8601)
    - updated_at (string, ISO8601)
    - last_seen (string, ISO8601)
    - state_data (map, optional)
    - chat_summary (string, optional)
"""

import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from utils.coreutil import convert_floats_for_dynamodb

try:
    import boto3
    from boto3.dynamodb.conditions import Key, Attr
    BOTO3_AVAILABLE = True
except ImportError:
    # Mock boto3 for development when not available
    print("Warning: boto3 not available, using mock implementation")
    BOTO3_AVAILABLE = False
    
    # Create mock boto3
    class MockDynamoDB:
        def __init__(self):
            self.data = {}
        
        def get_item(self, **kwargs):
            key = kwargs.get('Key', {})
            user_id = key.get('user_id')
            if user_id in self.data:
                return {'Item': self.data[user_id]}
            return {}
        
        def put_item(self, **kwargs):
            item = kwargs.get('Item', {})
            user_id = item.get('user_id')
            if user_id:
                self.data[user_id] = item
            return {}
        
        def scan(self, **kwargs):
            items = list(self.data.values())
            return {'Items': items}
    
    class MockTable:
        def __init__(self):
            self.db = MockDynamoDB()
        
        def get_item(self, **kwargs):
            return self.db.get_item(**kwargs)
        
        def put_item(self, **kwargs):
            return self.db.put_item(**kwargs)
        
        def scan(self, **kwargs):
            return self.db.scan(**kwargs)
    
    # Mock boto3 module
    class MockBoto3:
        def __init__(self):
            self.resource = self
            self.dynamodb = self
        
        def __call__(self, service_name, **kwargs):
            return self
        
        def Table(self, name):
            return MockTable()
        
        def conditions(self):
            class MockConditions:
                def Attr(self, name):
                    class MockAttr:
                        def eq(self, value):
                            return f"Attr({name}).eq({value})"
                        def contains(self, value):
                            return f"Attr({name}).contains({value})"
                        def __or__(self, other):
                            return f"({self}) OR ({other})"
                    return MockAttr()
            return MockConditions()
    
    boto3 = MockBoto3()
    Attr = boto3.conditions().Attr

def now_iso() -> str:
    """Get current timestamp in ISO format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

class UserDB:
    def __init__(self, table_name="b_users"):
        self.table = boto3.resource('dynamodb').Table(table_name)

    def save_user(
        self,
        user_id: str,
        first_name: str,
        last_name: str,
        email: str,
        phone: str,
        source: str,
        consent_time: Optional[str] = None,
        status: str = "inprogress",
        is_admin: bool = False,          # NEW param with default False
        state_data: Optional[Dict[str, Any]] = None,
        chat_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update a user (upsert operation)."""
        timestamp = now_iso()
        
        # First, check if a user with this phone number already exists
        existing_user = self.lookup_user_by_phone(phone)
        
        if existing_user:
            # User exists - update the existing user
            print(f"User with phone {phone} already exists, updating...")
            
            # Prepare update expression and values
            update_expression = "SET "
            expression_values = {}
            expression_names = {}
            
            # Build dynamic update expression
            updates = []
            if first_name:
                updates.append("first_name = :fn")
                expression_values[":fn"] = first_name
            if last_name:
                updates.append("last_name = :ln")
                expression_values[":ln"] = last_name
            if email:
                updates.append("email = :email")
                expression_values[":email"] = email
            if source:
                updates.append("#src = :source")
                expression_names["#src"] = "source"
                expression_values[":source"] = source
            if status:
                updates.append("#st = :status")
                expression_names["#st"] = "status"
                expression_values[":status"] = status
            if consent_time:
                updates.append("consent_time = :consent")
                expression_values[":consent"] = consent_time
            if state_data:
                updates.append("state_data = :state")
                expression_values[":state"] = convert_floats_for_dynamodb(state_data)
            if chat_summary:
                updates.append("chat_summary = :chat")
                expression_values[":chat"] = chat_summary
            
            # Always update these fields
            updates.append("updated_at = :updated")
            updates.append("last_seen = :last_seen")
            expression_values[":updated"] = timestamp
            expression_values[":last_seen"] = timestamp
            
            # Update is_admin if provided
            if is_admin is not None:
                updates.append("is_admin = :admin")
                expression_values[":admin"] = is_admin
            
            update_expression += ", ".join(updates)
            
            try:
                response = self.table.update_item(
                    Key={"user_id": existing_user["user_id"]},
                    UpdateExpression=update_expression,
                    ExpressionAttributeValues=expression_values,
                    ExpressionAttributeNames=expression_names,
                    ReturnValues="ALL_NEW"
                )
                return response.get("Attributes", existing_user)
            except Exception as e:
                print(f"Error updating user with phone {phone}: {e}")
                return existing_user
        else:
            # User doesn't exist - create new user
            print(f"Creating new user with phone {phone}...")
            item = {
                "user_id": user_id,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": phone,
                "source": source,
                "status": status,
                "is_admin": is_admin,           # Store is_admin flag
                "created_at": timestamp,
                "updated_at": timestamp,
                "last_seen": timestamp,
            }
            if consent_time is not None:
                item["consent_time"] = consent_time
            if state_data is not None:
                item["state_data"] = convert_floats_for_dynamodb(state_data)
            if chat_summary is not None:
                item["chat_summary"] = chat_summary

            self.table.put_item(Item=item)
            return item

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a user by user_id."""
        try:
            response = self.table.get_item(Key={"user_id": user_id})
            return response.get("Item")
        except Exception as e:
            print(f"Error getting user {user_id}: {e}")
            return None


    def lookup_user_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Get a user by phone number using scan operation (returns first match only)."""
        try:
            # Use scan with filter expression for phone lookup
            try:
                response = self.table.scan(
                    FilterExpression=Attr("phone").eq(phone),
                    Limit=1
                )
            except AttributeError:
                # Fallback if boto3.dynamodb.conditions is not available
                response = self.table.scan(Limit=100)
                items = response.get("Items", [])
                # Filter manually
                for item in items:
                    if item.get("phone") == phone:
                        return item
                return None
            
            items = response.get("Items", [])
            return items[0] if items else None
            
        except Exception as e:
            print(f"Error getting user by phone {phone}: {e}")
            return None

    def list_users(self, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """List all users, optionally filtered by status."""
        try:
            # Start with a scan
            scan_kwargs = {
                "Limit": limit
            }
            
            # Add status filter if provided
            if status:
                try:
                    scan_kwargs["FilterExpression"] = Attr("status").eq(status)
                except AttributeError:
                    # Fallback if boto3.dynamodb.conditions is not available
                    print("Warning: boto3.dynamodb.conditions not available, skipping status filter")
            
            response = self.table.scan(**scan_kwargs)
            items = response.get("Items", [])
            
            # Handle pagination if needed
            while "LastEvaluatedKey" in response and len(items) < limit:
                scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                response = self.table.scan(**scan_kwargs)
                items.extend(response.get("Items", []))
            
            return items[:limit]
        except Exception as e:
            print(f"Error listing users: {e}")
            return []

    def search_users(self, query: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Search users by first name, last name, email, or phone (case-insensitive)."""
        try:
            # Use scan with filter expression for more efficient searching
            try:
                scan_kwargs = {
                    "Limit": limit,
                    "FilterExpression": (
                        Attr("first_name").contains(query.lower()) |
                        Attr("last_name").contains(query.lower()) |
                        Attr("email").contains(query.lower()) |
                        Attr("phone").contains(query)
                    )
                }
            except AttributeError:
                # Fallback if boto3.dynamodb.conditions is not available
                print("Warning: boto3.dynamodb.conditions not available, using basic scan")
                scan_kwargs = {
                    "Limit": limit
                }
            
            response = self.table.scan(**scan_kwargs)
            items = response.get("Items", [])
            
            # Handle pagination if needed
            while "LastEvaluatedKey" in response and len(items) < limit:
                scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                response = self.table.scan(**scan_kwargs)
                items.extend(response.get("Items", []))
            
            return items[:limit]
        except Exception as e:
            print(f"Error searching users: {e}")
            return []

    def load_state_data(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Load state_data for a user by user_id."""
        try:
            response = self.table.get_item(Key={"user_id": user_id})
            user = response.get("Item")
            if user and 'state_data' in user:
                return user['state_data']
            return None
        except Exception as e:
            print(f"Error loading state_data for user {user_id}: {e}")
            return None

    def save_state_data(self, state_data: Dict[str, Any]) -> bool:
        """Save state_data for a user. user_id is extracted from state_data."""
        try:
            # Extract user_id from state_data
            user_id = state_data.get("user_id")
            if not user_id:
                print("Error: user_id not found in state_data")
                return False
            
            # Remove user_id from state_data to avoid duplication in database
            state_data_for_db = {k: v for k, v in state_data.items() if k != "user_id"}
            
            timestamp = now_iso()
            response = self.table.update_item(
                Key={"user_id": user_id},
                UpdateExpression="SET state_data = :state, updated_at = :updated, last_seen = :last_seen",
                ExpressionAttributeValues={
                    ":state": convert_floats_for_dynamodb(state_data_for_db),
                    ":updated": timestamp,
                    ":last_seen": timestamp
                },
                ReturnValues="ALL_NEW"
            )
            print(f"State data saved successfully for user {user_id}")
            return True
        except Exception as e:
            print(f"Error saving state_data: {e}")
            return False
