# Intent classifier build; runs intent classifier API
FROM ibase:3.6.11

ENV INTENT_CLASSIFIER_PORT=9000
# Ensure our source takes precedence over any site-packages `intents`
ENV PYTHONPATH=/app/src:$PYTHONPATH

WORKDIR /app

# Copy only what intent_classifier needs (speeds up cache)
COPY requirements.txt /app/src/intents/requirements.txt
COPY __init__.py /app/src/intents/__init__.py
COPY api /app/src/intents/api
COPY core /app/src/intents/core
COPY config /app/src/intents/config
# Copy trainings directory for normalizer import and training configs
COPY trainings /app/src/intents/trainings

# Also place key training files at /app/src for robustness (some codepaths
# may resolve bare filenames relative to the workdir)
COPY trainings/initial_training_data.yml /app/src/initial_training_data.yml
COPY trainings/config.yml /app/src/config.yml

# Requirements already installed in base
# TEMPORARY FIX: Install compatible packaging version for Rasa 3.6.10
# TODO: Remove this after updating base image with proper packaging version
RUN pip install --no-cache-dir "packaging<22.0"

# Remove any preinstalled package named `intents` that could shadow our code
RUN pip uninstall -y intents || true

# Expose intent classifier port
EXPOSE 9000

# Default command: run intent classifier with gunicorn (as package)
ENV PYTHONPATH=/app/src
WORKDIR /app/src

# 👇 Optimized configuration:
# - Use 1 worker only (Rasa training is heavy, avoids OOM)
# - Increase timeout to allow initial training during boot
# - Avoid --preload so heavy init happens inside worker, not master
CMD ["gunicorn", "-w", "1", "--timeout", "900", "--graceful-timeout", "120", "--max-requests", "1000", "--max-requests-jitter", "100", "-b", "0.0.0.0:9000", "intents.api.intent_classifier:app"]



