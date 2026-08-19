"""
FastAPI Main Application

Entry point for running the dialogcart-core API server.
"""

import logging
import sys

from fastapi import FastAPI

from core.api import message
from core.config.session_freshness import load_session_freshness_settings

# Wire up application logging before uvicorn.run() is called.
# Uvicorn's dictConfig only configures uvicorn.* loggers, so handlers added
# here survive startup.
#
# Two loggers need explicit wiring:
#   core.turn_log  — compact structured JSON per stage when decision trace is off
#   core.api.message — session events (when trace off) and decision trace text
_log_handler = logging.StreamHandler(sys.stdout)
_log_handler.setFormatter(
    logging.Formatter("%(asctime)s  %(name)-45s  %(levelname)s  %(message)s")
)

_turn_logger = logging.getLogger("core.turn_log")
_turn_logger.addHandler(_log_handler)
_turn_logger.propagate = False

_api_msg_logger = logging.getLogger("core.api.message")
_api_msg_logger.setLevel(logging.INFO)
_api_msg_logger.addHandler(_log_handler)
_api_msg_logger.propagate = False

_freshness_settings = load_session_freshness_settings()
_api_msg_logger.info(
    "Availability TTL: %s seconds",
    _freshness_settings.availability_ttl_seconds,
)

# Create FastAPI app
app = FastAPI(
    title="Dialogcart Core API",
    description="Stateless orchestration service for dialogcart",
    version="1.0.0",
)

# Include routers
app.include_router(message.router, prefix="/api", tags=["messages"])


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
