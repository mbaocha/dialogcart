"""
Confidence scoring utilities and thresholds
"""

# Confidence thresholds for different use cases
BUCKET_THRESHOLD_HIGH = 0.7
BUCKET_THRESHOLD_MEDIUM = 0.4
LLM_ROUTING_THRESHOLD = 0.8

def score_to_bucket(score: float) -> str:
    """Convert numeric confidence score to bucket."""
    if score >= BUCKET_THRESHOLD_HIGH:
        return "high"
    elif score >= BUCKET_THRESHOLD_MEDIUM:
        return "medium"
    else:
        return "low"

def should_route_to_llm_by_confidence(confidence_score: float) -> bool:
    """Check if confidence score is low enough to route to LLM."""
    return confidence_score < LLM_ROUTING_THRESHOLD


