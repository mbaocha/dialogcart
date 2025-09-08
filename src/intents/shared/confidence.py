from .constants import CONF_RANK


def score_to_bucket(score: float) -> str:
    if score is None:
        return "low"
    if score >= 0.7:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def is_sufficient(bucket: str, threshold: str = "medium") -> bool:
    return CONF_RANK.get(bucket.lower(), 0) >= CONF_RANK.get(threshold.lower(), 1)


