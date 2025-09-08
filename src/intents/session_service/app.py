"""
Shared Session Service - Centralized session storage for Rasa and LLM services
"""
from flask import Flask, request, jsonify
import threading
from typing import Dict, Any

app = Flask(__name__)

# Shared in-memory storage
_sessions = {}  # { sender_id: { "slots": {}, "history": [], "metadata": {} } }
_sessions_lock = threading.Lock()

@app.route("/session/get", methods=["POST"])
def get_session():
    """Get complete session data for a sender"""
    data = request.get_json(force=True)
    sender_id = data.get("sender_id", "anonymous")
    
    with _sessions_lock:
        session_data = _sessions.get(sender_id, {
            "slots": {},
            "history": [],
            "metadata": {}
        })
        return jsonify({
            "sender_id": sender_id,
            "session": session_data
        })

@app.route("/session/update", methods=["POST"])
def update_session():
    """Update session data for a sender"""
    data = request.get_json(force=True)
    sender_id = data.get("sender_id", "anonymous")
    session_updates = data.get("session", {})
    
    with _sessions_lock:
        if sender_id not in _sessions:
            _sessions[sender_id] = {
                "slots": {},
                "history": [],
                "metadata": {}
            }
        
        # Update each section if provided
        if "slots" in session_updates:
            _sessions[sender_id]["slots"].update(session_updates["slots"])
        if "history" in session_updates:
            _sessions[sender_id]["history"].extend(session_updates["history"])
        if "metadata" in session_updates:
            _sessions[sender_id]["metadata"].update(session_updates["metadata"])
        
        return jsonify({
            "success": True,
            "sender_id": sender_id
        })

@app.route("/session/clear", methods=["POST"])
def clear_session():
    """Clear session data for a sender"""
    data = request.get_json(force=True)
    sender_id = data.get("sender_id", "anonymous")
    
    with _sessions_lock:
        _sessions.pop(sender_id, None)
        return jsonify({
            "success": True,
            "sender_id": sender_id,
            "cleared": True
        })

@app.route("/session/append_history", methods=["POST"])
def append_history():
    """Append a message to session history"""
    data = request.get_json(force=True)
    sender_id = data.get("sender_id", "anonymous")
    role = data.get("role", "user")
    content = data.get("content", "")
    
    if not content:
        return jsonify({"error": "content required"}), 400
    
    with _sessions_lock:
        if sender_id not in _sessions:
            _sessions[sender_id] = {
                "slots": {},
                "history": [],
                "metadata": {}
            }
        
        _sessions[sender_id]["history"].append({
            "role": role,
            "content": content
        })
        
        return jsonify({
            "success": True,
            "sender_id": sender_id,
            "history_length": len(_sessions[sender_id]["history"])
        })

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "session_service",
        "active_sessions": len(_sessions)
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9200)
