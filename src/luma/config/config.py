"""
Luma Configuration

Centralized configuration for all luma features and components.
All settings can be overridden via environment variables.
"""
import os
from typing import Optional


class LumaConfig:
    """
    Central configuration for luma pipeline.

    All settings have sensible defaults and can be overridden via environment variables.

    Example:
        >>> from luma.config import config
        >>> print(config.ENABLE_INTENT_MAPPER)
        True

        # Override via environment:
        >>> os.environ["ENABLE_INTENT_MAPPER"] = "false"
        >>> config = LumaConfig()  # Reload
        >>> print(config.ENABLE_INTENT_MAPPER)
        False
    """

    # ========================================================================
    # Feature Toggles
    # ========================================================================

    ENABLE_INTENT_MAPPER: bool = os.getenv(
        "ENABLE_INTENT_MAPPER", "true").lower() == "true"
    """Enable ML-based intent mapping (add → ADD_TO_CART, etc.)"""

    ENABLE_LLM_FALLBACK: bool = os.getenv(
        "ENABLE_LLM_FALLBACK", "false").lower() == "true"
    """Enable LLM fallback for complex/ambiguous extractions"""

    ENABLE_FUZZY_MATCHING: bool = os.getenv(
        "ENABLE_FUZZY_MATCHING", "false").lower() == "true"
    """Enable fuzzy matching for typo tolerance (requires rapidfuzz)"""

    # ========================================================================
    # Debug Settings
    # ========================================================================

    DEBUG_NLP: bool = os.getenv("DEBUG_NLP", "0") == "1"
    """Enable debug logging for NLP processing"""

    DEBUG_ENABLED: bool = DEBUG_NLP  # Alias for backward compatibility
    """Alias for DEBUG_NLP"""

    # ========================================================================
    # Logging Settings
    # ========================================================================

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    """Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL"""

    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "json")
    """Log format: 'json' (structured) or 'pretty' (readable)"""

    LOG_FILE: Optional[str] = os.getenv("LOG_FILE")
    """Optional: Write logs to file (e.g., '/var/log/luma/api.log')"""

    ENABLE_REQUEST_LOGGING: bool = os.getenv(
        "ENABLE_REQUEST_LOGGING", "true").lower() == "true"
    """Log all HTTP requests/responses with timing"""

    LOG_PERFORMANCE_METRICS: bool = os.getenv(
        "LOG_PERFORMANCE_METRICS", "true").lower() == "true"
    """Log extraction performance metrics (processing time, groups count, etc.)"""

    LOG_SLOT_TRACKING: bool = os.getenv(
        "LOG_SLOT_TRACKING", "false").lower() == "true"
    """Enable detailed slot tracking logs for debugging data loss (adds overhead)"""

    # ========================================================================
    # LLM Settings
    # ========================================================================

    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    """OpenAI model to use for LLM fallback"""

    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    """OpenAI API key (required if ENABLE_LLM_FALLBACK=true)"""

    # ========================================================================
    # Model Paths
    # ========================================================================

    NER_MODEL_PATH: str = os.getenv(
        "NER_MODEL_PATH", "luma/store/bert-ner-best")
    """Path to trained NER model (relative to src/)"""

    ENTITY_CATALOG_PATH: str = os.getenv(
        "ENTITY_CATALOG_PATH", "luma/store/merged_v9.json")
    """Path to entity catalog JSON (relative to src/)"""

    # ========================================================================
    # Global JSON Configuration
    # ========================================================================

    GLOBAL_JSON_VERSION: str = os.getenv("GLOBAL_JSON_VERSION", "v3")
    """Version of global normalization JSON file (e.g., 'v3', 'v2', 'v1')"""

    # ========================================================================
    # API Settings
    # ========================================================================

    API_PORT: int = int(os.getenv("PORT", "9001"))
    """Port for Flask API server"""

    API_HOST: str = os.getenv("HOST", "0.0.0.0")
    """Host for Flask API server"""

    API_DEBUG: bool = os.getenv("API_DEBUG", "false").lower() == "true"
    """Enable Flask debug mode (DO NOT use in production)"""

    # ========================================================================
    # Fuzzy Matching Settings
    # ========================================================================

    FUZZY_THRESHOLD: int = int(os.getenv("FUZZY_THRESHOLD", "88"))
    """Minimum similarity score (0-100) for fuzzy matches"""

    # ========================================================================
    # Performance Settings
    # ========================================================================

    LAZY_LOAD_MODELS: bool = os.getenv(
        "LAZY_LOAD_MODELS", "false").lower() == "true"
    """Lazy load models on first request (faster startup, slower first request)"""

    WARMUP_ON_STARTUP: bool = os.getenv(
        "WARMUP_ON_STARTUP", "true").lower() == "true"
    """Preload models on startup (slower startup, faster requests)"""


    # ========================================================================
    # Helper Methods
    # ========================================================================

    @classmethod
    def from_env(cls):
        """
        Create config from environment variables.

        Returns:
            New LumaConfig instance with current environment values
        """
        return cls()

    def summary(self) -> str:
        """
        Get configuration summary as formatted string.

        Returns:
            Multi-line string with all config values
        """
        lines = [
            "=" * 60,
            "Luma Configuration",
            "=" * 60,
            "",
            "Features:",
            f"  Intent Mapping:     {'✅ Enabled' if self.ENABLE_INTENT_MAPPER else '❌ Disabled'}",
            f"  LLM Fallback:       {'✅ Enabled' if self.ENABLE_LLM_FALLBACK else '❌ Disabled'}",
            f"  Fuzzy Matching:     {'✅ Enabled' if self.ENABLE_FUZZY_MATCHING else '❌ Disabled'}",
            "",
            "Debug:",
            f"  Debug Logging:      {'✅ Enabled' if self.DEBUG_NLP else '❌ Disabled'}",
            "",
        ]

        if self.ENABLE_LLM_FALLBACK:
            lines.extend([
                "LLM:",
                f"  Model:              {self.LLM_MODEL}",
                f"  API Key:            {'✅ Set' if self.OPENAI_API_KEY else '❌ Not set'}",
                "",
            ])

        lines.extend([
            "Models:",
            f"  NER Model:          {self.NER_MODEL_PATH}",
            f"  Entity Catalog:     {self.ENTITY_CATALOG_PATH}",
            "",
            "API:",
            f"  Host:               {self.API_HOST}",
            f"  Port:               {self.API_PORT}",
            "",
            "Logging:",
            f"  Level:              {self.LOG_LEVEL}",
            f"  Format:             {self.LOG_FORMAT}",
            f"  File:               {self.LOG_FILE or 'None'}",
            f"  Request Logging:    {'✅ Enabled' if self.ENABLE_REQUEST_LOGGING else '❌ Disabled'}",
            f"  Performance Logs:   {'✅ Enabled' if self.LOG_PERFORMANCE_METRICS else '❌ Disabled'}",
            f"  Slot Tracking:      {'✅ Enabled' if self.LOG_SLOT_TRACKING else '❌ Disabled'}",
            "",
            "Performance:",
            f"  Lazy Load:          {'✅ Yes' if self.LAZY_LOAD_MODELS else '❌ No'}",
            f"  Warmup:             {'✅ Yes' if self.WARMUP_ON_STARTUP else '❌ No'}",
            "",
            "=" * 60,
        ])

        return "\n".join(lines)

    def __repr__(self):
        """String representation."""
        return f"<LumaConfig intent={self.ENABLE_INTENT_MAPPER} llm={self.ENABLE_LLM_FALLBACK}>"


# Global config instance
config = LumaConfig()


# Convenience function for debug printing
def debug_print(*args, **kwargs):
    """Print debug message only if DEBUG_NLP is enabled."""
    if config.DEBUG_ENABLED:
        print(*args, **kwargs)



