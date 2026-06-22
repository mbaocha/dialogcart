# NLU Service - Dockerfile
FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY nlu/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY nlu/ ./nlu/

EXPOSE 9002

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:9002/health || exit 1

CMD ["python", "nlu/api.py"]
