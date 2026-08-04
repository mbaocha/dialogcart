#!/usr/bin/env python3
"""
Run the NLU API server with a fixed test date for deterministic testing.

This script sets LUMA_TEST_NOW from the shared E2E test clock and starts
src/nlu, ensuring relative dates (tomorrow, weekday names, etc.) resolve
consistently with Core E2E fixtures.

Production NLU startup is ``python -m nlu.api`` (no LUMA_TEST_NOW) — wall clock.
"""

import os
import sys
from pathlib import Path

# Add src/ before importing the shared clock / nlu.
src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from core.tests.harness.test_clock import (  # noqa: E402
    LUMA_TEST_NOW_ENV,
    TEST_NOW_ISO,
)

# Set environment variable before importing nlu.api
os.environ[LUMA_TEST_NOW_ENV] = TEST_NOW_ISO

# Import and run the API
if __name__ == "__main__":
    print(f"Starting NLU API with fixed test date: {TEST_NOW_ISO}")
    print("=" * 60)

    from nlu.api import main

    main()
