"""
FastAPI Main Application

Entry point for running the dialogcart-core API server.
"""

from fastapi import FastAPI
from core.orchestration.api import message

# Create FastAPI app
app = FastAPI(
    title="Dialogcart Core API",
    description="Stateless orchestration service for dialogcart",
    version="1.0.0"
)

# Include routers
app.include_router(message.router, prefix="/api", tags=["messages"])


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

