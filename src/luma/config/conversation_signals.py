"""
Conversation Signals Configuration

Loads conversation-level signals (confirmation, negation, etc.) from YAML config.
"""
from pathlib import Path
from typing import Dict, Any, Optional, Set, List
import yaml
import logging

logger = logging.getLogger(__name__)

# Cache for loaded conversation signals
_CONVERSATION_SIGNALS_CACHE: Optional[Dict[str, Any]] = None


def load_conversation_signals() -> Dict[str, Any]:
    """
    Load conversation signals from conversation_signals.yaml (cached).
    
    Returns:
        Dictionary containing conversation signals configuration
        
    Raises:
        FileNotFoundError: If conversation_signals.yaml is not found
    """
    global _CONVERSATION_SIGNALS_CACHE
    
    if _CONVERSATION_SIGNALS_CACHE is not None:
        return _CONVERSATION_SIGNALS_CACHE
    
    # Try config/data first
    config_dir = Path(__file__).resolve().parent
    config_data_path = config_dir / "data" / "conversation_signals.yaml"
    
    if not config_data_path.exists():
        raise FileNotFoundError(
            f"conversation_signals.yaml not found. Tried:\n"
            f"  - {config_data_path}\n"
            f"Please ensure conversation_signals.yaml exists in this location."
        )
    
    with config_data_path.open(encoding="utf-8") as f:
        _CONVERSATION_SIGNALS_CACHE = yaml.safe_load(f) or {}
    
    return _CONVERSATION_SIGNALS_CACHE


def get_confirmation_terms() -> Set[str]:
    """
    Get confirmation terms from config.
    
    Returns:
        Set of confirmation terms (exact matches)
    """
    signals = load_conversation_signals()
    confirmation_cfg = signals.get("confirmation", {})
    
    if not confirmation_cfg.get("enabled", True):
        return set()
    
    match_cfg = confirmation_cfg.get("match", {})
    exact_terms = match_cfg.get("exact", [])
    return set(term.lower() for term in exact_terms if isinstance(term, str))


def get_confirmation_phrases() -> List[str]:
    """
    Get confirmation phrases from config.
    
    Returns:
        List of confirmation phrases (substring matches)
    """
    signals = load_conversation_signals()
    confirmation_cfg = signals.get("confirmation", {})
    
    if not confirmation_cfg.get("enabled", True):
        return []
    
    match_cfg = confirmation_cfg.get("match", {})
    phrases = match_cfg.get("phrases", [])
    return [phrase.lower() for phrase in phrases if isinstance(phrase, str)]


def is_confirmation_enabled() -> bool:
    """
    Check if confirmation detection is enabled.
    
    Returns:
        True if confirmation detection is enabled, False otherwise
    """
    signals = load_conversation_signals()
    confirmation_cfg = signals.get("confirmation", {})
    return confirmation_cfg.get("enabled", True)


