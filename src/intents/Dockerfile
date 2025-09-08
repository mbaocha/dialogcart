# Unified build for intent services; runs unified API by default
FROM intents-base:py310

ENV UNIFIED_API_PORT=9000

WORKDIR /app

# Copy only what unified/llm/session need (speeds up cache)
COPY src/intents/requirements.txt /app/src/intents/requirements.txt
COPY src/intents/unified_api /app/src/intents/unified_api
COPY src/intents/shared /app/src/intents/shared
COPY src/intents/llm_service /app/src/intents/llm_service
COPY src/intents/session_service /app/src/intents/session_service

# Requirements already installed in base

# Expose unified API port
EXPOSE 9000

# Default command: run unified API with gunicorn (as package)
ENV PYTHONPATH=/app/src
WORKDIR /app/src
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:9000", "intents.unified_api.app:app"]
