# ============================================================
# 1. Base image with Python 3.10 (needed for AllenNLP 2.x)
# ============================================================
FROM python:3.10-slim

# Prevent Python from writing .pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ============================================================
# 2. Set working directory
# ============================================================
WORKDIR /app

# ============================================================
# 3. Install OS-level dependencies
# ============================================================
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ============================================================
# 4. Copy project files
# ============================================================
COPY requirements.txt .
COPY src/ ./src/

# ============================================================
# 5. Install Python dependencies
# ============================================================
RUN pip install --no-cache-dir -r requirements.txt

# ============================================================
# 6. Pre-download the AllenNLP SRL model (for caching)
# ============================================================
RUN mkdir -p /app/store && \
    curl -L -o /app/store/structured-prediction-srl-bert.2020.12.15.tar.gz \
    https://storage.googleapis.com/allennlp-public-models/structured-prediction-srl-bert.2020.12.15.tar.gz

# ============================================================
# 7. Expose the port FastAPI will use
# ============================================================
EXPOSE 8080

# ============================================================
# 8. Run the FastAPI service
# ============================================================
CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8080"]
