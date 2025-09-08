"""
Session Client - Helper for accessing shared session service
"""
import requests
from typing import Dict, Any, Optional

class SessionClient:
    def __init__(self, session_service_url: str = "http://localhost:9200"):
        self.base_url = session_service_url.rstrip("/")
    
    def get_session(self, sender_id: str) -> Dict[str, Any]:
        """Get complete session data"""
        try:
            response = requests.post(
                f"{self.base_url}/session/get",
                json={"sender_id": sender_id},
                timeout=5
            )
            response.raise_for_status()
            return response.json().get("session", {})
        except Exception:
            return {"slots": {}, "history": [], "metadata": {}}
    
    def update_session(self, sender_id: str, session_data: Dict[str, Any]) -> bool:
        """Update session data"""
        try:
            response = requests.post(
                f"{self.base_url}/session/update",
                json={"sender_id": sender_id, "session": session_data},
                timeout=5
            )
            response.raise_for_status()
            return response.json().get("success", False)
        except Exception:
            return False
    
    def clear_session(self, sender_id: str) -> bool:
        """Clear session data"""
        try:
            response = requests.post(
                f"{self.base_url}/session/clear",
                json={"sender_id": sender_id},
                timeout=5
            )
            response.raise_for_status()
            return response.json().get("success", False)
        except Exception:
            return False
    
    def append_history(self, sender_id: str, role: str, content: str) -> bool:
        """Append message to history"""
        try:
            response = requests.post(
                f"{self.base_url}/session/append_history",
                json={"sender_id": sender_id, "role": role, "content": content},
                timeout=5
            )
            response.raise_for_status()
            return response.json().get("success", False)
        except Exception:
            return False
