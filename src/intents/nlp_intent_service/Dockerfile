FROM my-rasa-base:3.6.10

# Set Python path so custom components can be discovered
ENV PYTHONPATH="/app"

# Build metadata
ARG BUILD_ID=16
RUN echo "BUILD_ID=${BUILD_ID}"

# Suppress various warnings
ENV SQLALCHEMY_WARN_20=0
ENV SQLALCHEMY_SILENCE_UBER_WARNING=1
ENV PYTHONWARNINGS="ignore"
ENV TF_CPP_MIN_LOG_LEVEL=2
ENV PYTHONUNBUFFERED=1

# Install runtime dependencies
RUN pip install --no-cache-dir gunicorn
RUN pip install --no-cache-dir filelock

# Copy source code into container
COPY . /app
WORKDIR /app

# Ensure model storage dir exists and is mounted
RUN mkdir -p /app/model_storage
VOLUME ["/app/model_storage"]

# Expose service port
EXPOSE 8000

# Run using Gunicorn
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8000", "app:app"]
