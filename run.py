#!/usr/bin/env python3
"""
Run Luma API server with fixed test date for deterministic testing.

This script sets LUMA_TEST_NOW environment variable and starts the server,
ensuring all relative dates (tomorrow, weekday names, etc.) resolve consistently.
"""

import os
import sys
from pathlib import Path

# Set fixed test date for deterministic testing
# 2026-01-13 (Wednesday) ensures:
# - "tomorrow" = 2026-01-14
# - "wednesday" = 2026-01-14 (nearest future Wednesday)
# - "next week" = 2026-01-19 to 2026-01-25
TEST_NOW = "2026-01-13T10:00:00Z"

# Set environment variable before importing luma.api
os.environ["LUMA_TEST_NOW"] = TEST_NOW

# Add src/ to path if needed
src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Import and run the API
if __name__ == "__main__":
    print(f"Starting Luma API with fixed test date: {TEST_NOW}")
    print("=" * 60)
    
    # Import and run
    from luma.api import main
    main()










