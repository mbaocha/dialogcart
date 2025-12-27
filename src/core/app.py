"""
Dialogcart Core Application

Legacy wrapper - use orchestrator.handle_message() directly.
This file is kept for backward compatibility.
"""

from pathlib import Path

# Load environment variables from .env files at startup
try:
    from dotenv import load_dotenv
    # Try loading .env and .env.local from project root
    project_root = Path(__file__).parent.parent.parent.parent  # dialogcart/
    env_file = project_root / ".env"
    env_local_file = project_root / ".env.local"

    # Load .env first, then .env.local (which can override)
    if env_file.exists():
        load_dotenv(env_file, override=False)
    if env_local_file.exists():
        load_dotenv(env_local_file, override=True)
except ImportError:
    # python-dotenv not installed, skip .env loading
    pass

from core.orchestration.orchestrator import handle_message

# Re-export for backward compatibility
process_message = handle_message
