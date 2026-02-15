#!/usr/bin/env python3
"""
Test Entry Point for Dialogcart-Core

This script provides a convenient entry point to run all tests or specific test categories.

Usage:
    # Run all tests
    python core/tests/test.py

    # Run specific category
    python core/tests/test.py --category orchestration
    python core/tests/test.py --category planning
    python core/tests/test.py --category e2e

    # Run with pytest options
    python core/tests/test.py -- -v --tb=short
    python core/tests/test.py --category e2e -- -k scenario4

    # Run E2E tests with real Luma (requires RUN_REAL_LUMA_E2E=true)
    RUN_REAL_LUMA_E2E=true python core/tests/test.py --category e2e
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


# Add src to path
def setup_path():
    """Add src directory to Python path."""
    # __file__ = src/core/tests/test.py
    # parent.parent.parent.parent = dialogcart/ or src/
    base_path = Path(__file__).parent.parent.parent.parent

    # If we're in dialogcart/, src/ is a subdirectory
    # If we're in src/, src/ is the current directory
    if (base_path / "src").exists():
        src_path = base_path / "src"
    elif base_path.name == "src":
        src_path = base_path
    else:
        # Fallback: assume src/ is at base_path
        src_path = base_path

    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


# Test categories and their paths (relative to src/ directory)
TEST_CATEGORIES = {
    "all": "core/tests",
    "orchestration": "core/tests/orchestration",
    "planning": "core/tests/planning",
    "e2e": "core/tests/e2e",
    "integration": "core/tests/integration",
    "execution": "core/tests/execution",
    "session": "core/tests/session",
    "workflows": "core/tests/workflows",
    "intents": "core/tests/intents",
    "rendering": "core/tests/rendering",
}


def run_tests(category: str = "all", pytest_args: list = None) -> int:
    """
    Run tests for the specified category.

    Args:
        category: Test category to run (default: "all")
        pytest_args: Additional pytest arguments

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    if category not in TEST_CATEGORIES:
        print(f"Error: Unknown test category '{category}'")
        print(f"Available categories: {', '.join(TEST_CATEGORIES.keys())}")
        return 1

    test_path = TEST_CATEGORIES[category]

    # Determine working directory
    # __file__ = src/core/tests/test.py
    # parent.parent.parent.parent = dialogcart/ or src/
    base_path = Path(__file__).parent.parent.parent.parent

    # Find src/ directory (where pytest.ini is located)
    if (base_path / "src").exists():
        # We're in dialogcart/, src/ is a subdirectory
        src_dir = base_path / "src"
    elif base_path.name == "src":
        # We're already in src/
        src_dir = base_path
    else:
        # Fallback: assume base_path is src/
        src_dir = base_path

    # Build pytest command (paths relative to src/)
    cmd = ["python", "-m", "pytest", test_path]

    # Add custom pytest arguments if provided
    if pytest_args:
        cmd.extend(pytest_args)

    # Print command
    print("=" * 80)
    print(f"Running tests: {category}")
    print(f"Path: {test_path}")
    print(f"Working directory: {src_dir}")
    if pytest_args:
        print(f"Pytest args: {' '.join(pytest_args)}")
    print("=" * 80)
    print()

    # Check for E2E tests requiring real Luma
    if category == "e2e" and "test_core_e2e_followup_availability_real_luma.py" in str(
        test_path
    ):
        if not os.getenv("RUN_REAL_LUMA_E2E"):
            print("⚠️  WARNING: E2E tests with real Luma require RUN_REAL_LUMA_E2E=true")
            print(
                "   Some tests may be skipped. Set environment variable to run all E2E tests."
            )
            print()

    # Run pytest from src/ directory (where pytest.ini is located)
    try:
        result = subprocess.run(cmd, cwd=src_dir)
        return result.returncode
    except KeyboardInterrupt:
        print("\n\nTest run interrupted by user.")
        return 130
    except Exception as e:
        print(f"Error running tests: {e}")
        return 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run Dialogcart-Core tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all tests
  python core/tests/test.py

  # Run orchestration tests
  python core/tests/test.py --category orchestration

  # Run E2E tests with verbose output
  python core/tests/test.py --category e2e -- -v

  # Run specific test by name
  python core/tests/test.py --category planning -- -k scenario4

  # Run E2E tests with real Luma
  RUN_REAL_LUMA_E2E=true python core/tests/test.py --category e2e

  # Run rendering tests
  python core/tests/test.py --category rendering
        """,
    )

    parser.add_argument(
        "--category",
        "-c",
        default="all",
        choices=list(TEST_CATEGORIES.keys()),
        help="Test category to run (default: all)",
    )

    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List all available test categories",
    )

    # Parse known args to separate pytest args
    args, pytest_args = parser.parse_known_args()

    # Handle --list
    if args.list:
        print("Available test categories:")
        for cat, path in TEST_CATEGORIES.items():
            print(f"  {cat:15} -> {path}")
        return 0

    # Setup path
    setup_path()

    # Run tests
    return run_tests(args.category, pytest_args)


if __name__ == "__main__":
    sys.exit(main())
