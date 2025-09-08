"""
Configuration for Unified API
"""
import os

# Service URLs
RASA_URL = os.getenv("RASA_URL", "http://localhost:8001")
LLM_URL = os.getenv("LLM_URL", "http://localhost:9100")

# Fallback thresholds
FALLBACK_CONF = {"low": 0, "medium": 1, "high": 2}
FALLBACK_THRESHOLD = 1  # >= medium considered sufficient

# Rasa confidence threshold for avoiding LLM fallback
RASA_CONFIDENCE_THRESHOLD = 0.85

# API settings
PORT = int(os.getenv("UNIFIED_API_PORT", "9000"))
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
