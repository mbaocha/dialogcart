"""
Capability Adapters Package

Contains all capability adapter implementations.
"""

from .payment import PaymentAdapter
from .noop import NoopAdapter

__all__ = [
    "PaymentAdapter",
    "NoopAdapter",
]

