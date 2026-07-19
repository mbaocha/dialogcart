"""
Dialogcart Core Application

Loads environment variables used by the Core API at startup.
"""

from pathlib import Path

# Load environment variables from .env files at startup
try:
    from dotenv import load_dotenv

    _src_root = Path(__file__).parent.parent
    _core_env = Path(__file__).parent / ".env"
    _env_file = _src_root / ".env"
    _env_local_file = _src_root / ".env.local"

    if _env_file.exists():
        load_dotenv(_env_file, override=False)
    if _core_env.exists():
        load_dotenv(_core_env, override=True)
    if _env_local_file.exists():
        load_dotenv(_env_local_file, override=True)
except ImportError:
    # python-dotenv not installed, skip .env loading
    pass
