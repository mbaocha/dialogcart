"""
NLU Intent Service Package

This package provides intent classification and entity extraction services
using Rasa NLU with custom normalization components.
"""

__version__ = "1.0.0"
__author__ = "DialogCart Team"

# Force-load custom component module so it's registered
import normalization.normalizer
