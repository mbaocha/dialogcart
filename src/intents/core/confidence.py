"""
Confidence scoring utilities
"""

def score_to_bucket(score: float) -> str:
    """Convert numeric confidence score to bucket."""
    if score >= 0.7:
        return "high"
    elif score >= 0.4:
        return "medium"
    else:
        return "low"


