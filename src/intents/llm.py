"""
Grocery Assistant — Simple, Reliable NLU + Deterministic Dialog
(Refactored to use new modular structure)

This file now serves as a compatibility wrapper for the original llm.py functionality.
The actual implementation has been moved to llm_service/core/ modules.

For interactive testing, use:
  python src/intents/llm_service/cli.py

For REST API, use:
  python src/intents/llm_service/app.py

For unified API (Rasa + LLM fallback), use:
  python src/intents/unified_api/app.py
  python src/intents/unified_api/cli.py
"""

from llm_service.cli import main

if __name__ == "__main__":
    main()