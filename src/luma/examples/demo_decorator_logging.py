#!/usr/bin/env python3
"""
Demo script to showcase decorator-based logging.

Run this to see how the logging decorators work in practice.
"""
import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_path))

import os

# Set logging configuration
os.environ['LOG_LEVEL'] = 'INFO'
os.environ['LOG_FORMAT'] = 'pretty'  # Use pretty format for demo

from luma.core.pipeline import EntityExtractionPipeline

print("=" * 60)
print("Decorator-Based Logging Demo")
print("=" * 60)
print()

# Initialize pipeline (uses decorator on __init__)
print("1. Initializing pipeline...")
print()
pipeline = EntityExtractionPipeline(use_luma=False)  # Use legacy for simplicity

print()
print("2. Making extraction request...")
print("   (Watch for automatic input/output logging)")
print()

# Make extraction (uses decorator on extract())
result = pipeline.extract("add 2 kg rice and 3 bottles of coke")

print()
print("=" * 60)
print("Result Summary (from decorator logs):")
print("=" * 60)
print(f"Status: {result.status.value}")
print(f"Groups: {len(result.groups)}")
print(f"Route: {result.grouping_result.route if result.grouping_result else 'N/A'}")
print()

print("=" * 60)
print("Try with DEBUG level:")
print("=" * 60)
print("Run: LOG_LEVEL=DEBUG python demo_decorator_logging.py")
print("You'll see full input/output details!")
print()





