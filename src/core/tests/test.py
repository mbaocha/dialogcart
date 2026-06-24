#!/usr/bin/env python3
"""
Test Entry Point for Dialogcart-Core

Usage:
    python core/tests/test.py --category planning
    python core/tests/test.py --category execution
    RUN_REAL_LUMA_E2E=true python core/tests/test.py --category smoke
    python core/tests/test.py --category unit
    python core/tests/test.py --list
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def setup_path():
    base_path = Path(__file__).parent.parent.parent.parent
    if (base_path / "src").exists():
        src_path = base_path / "src"
    elif base_path.name == "src":
        src_path = base_path
    else:
        src_path = base_path
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


# Single-path categories (relative to src/)
TEST_CATEGORIES = {
    "all": "core/tests",
    "planning": "core/tests/planning",
    "execution": "core/tests/execution",
    "smoke": "core/tests/smoke",
    "orchestration": "core/tests/orchestration",
    "session": "core/tests/session",
    "rendering": "core/tests/rendering",
    "routing": "core/tests/routing",
    "workflows": "core/tests/workflows",
    "intents": "core/tests/intents",
}

# Fast unit tests (no Luma, no smoke)
UNIT_PATHS = [
    "core/tests/orchestration",
    "core/tests/session",
    "core/tests/rendering",
    "core/tests/routing",
    "core/tests/intents",
    "core/tests/workflows",
    "core/tests/execution/test_test_backend.py",
    "core/tests/execution/test_booking_execution.py",
    "core/tests/execution/test_availability_execution.py",
    "core/tests/execution/test_confirmation_execution.py",
]


def _src_dir() -> Path:
    base_path = Path(__file__).parent.parent.parent.parent
    if (base_path / "src").exists():
        return base_path / "src"
    if base_path.name == "src":
        return base_path
    return base_path


def run_tests(category: str = "all", pytest_args: list = None) -> int:
    if category not in TEST_CATEGORIES and category != "unit":
        print(f"Error: Unknown test category '{category}'")
        print(f"Available: {', '.join(list(TEST_CATEGORIES.keys()) + ['unit'])}")
        return 1

    src_dir = _src_dir()

    if category == "unit":
        cmd = ["python", "-m", "pytest", *UNIT_PATHS]
        path_display = " ".join(UNIT_PATHS)
    else:
        test_path = TEST_CATEGORIES[category]
        cmd = ["python", "-m", "pytest", test_path]
        path_display = test_path

    if pytest_args:
        cmd.extend(pytest_args)

    print("=" * 80)
    print(f"Running tests: {category}")
    print(f"Path: {path_display}")
    print(f"Working directory: {src_dir}")
    if pytest_args:
        print(f"Pytest args: {' '.join(pytest_args)}")
    print("=" * 80)
    print()

    if category == "smoke" and not os.getenv("RUN_REAL_LUMA_E2E"):
        print("WARNING: Smoke tests require RUN_REAL_LUMA_E2E=true")
        print()

    try:
        return subprocess.run(cmd, cwd=src_dir).returncode
    except KeyboardInterrupt:
        print("\n\nTest run interrupted by user.")
        return 130
    except Exception as e:
        print(f"Error running tests: {e}")
        return 1


def main():
    all_choices = list(TEST_CATEGORIES.keys()) + ["unit"]
    parser = argparse.ArgumentParser(
        description="Run Dialogcart-Core tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python core/tests/test.py --category planning
  python core/tests/test.py --category execution
  RUN_REAL_LUMA_E2E=true python core/tests/test.py --category smoke
  python core/tests/test.py --category unit
  python core/tests/test.py --category planning -- -k scenario_22
        """,
    )
    parser.add_argument(
        "--category",
        "-c",
        default="all",
        choices=all_choices,
        help="Test category to run (default: all)",
    )
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List all available test categories",
    )

    args, pytest_args = parser.parse_known_args()

    if args.list:
        print("Available test categories:")
        for cat, path in TEST_CATEGORIES.items():
            print(f"  {cat:15} -> {path}")
        print(f"  {'unit':15} -> {' '.join(UNIT_PATHS[:3])} ...")
        return 0

    setup_path()
    return run_tests(args.category, pytest_args)


if __name__ == "__main__":
    sys.exit(main())
