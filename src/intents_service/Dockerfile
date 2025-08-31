FROM public.ecr.aws/lambda/python:3.11
ARG BUILD_ID=19
RUN echo "BUILD_ID=${BUILD_ID}"

# Install deps (CPU-only torch via extra index)
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    numpy==1.26.4 boto3==1.34.162 \
    torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 \
    sentence-transformers==3.0.1

# Bake model into the image where Lambda can read it
RUN mkdir -p /app/models && \
    python -c "\
from sentence_transformers import SentenceTransformer; \
m = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); \
m.save('/app/models/all-MiniLM-L6-v2'); \
print('✅ Model baked into /app/models/all-MiniLM-L6-v2')"

# Run offline
ENV HF_HUB_DISABLE_TELEMETRY=1 TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1

COPY app.py ${LAMBDA_TASK_ROOT}
CMD ["app.handler"]
