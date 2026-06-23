"""
Capability Adapters Package

Contains all capability adapter implementations.
"""

from .noop import NoopAdapter
from .payment import PaymentAdapter

__all__ = [
    "PaymentAdapter",
    "NoopAdapter",
]
