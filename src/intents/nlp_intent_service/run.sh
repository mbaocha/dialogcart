#!/bin/bash

echo "🚀 Starting NLP Intent Service..."

# stop/remove old same-named container
docker stop nlp_intent_service_container >/dev/null 2>&1 || true
docker rm   nlp_intent_service_container >/dev/null 2>&1 || true

echo "🔨 Building Docker image..."
docker build -t nlp_intent_service .

mkdir -p ./model_storage

# NEW: free port 8000 if another container is using it
CID_ON_8000=$(docker ps --format '{{.ID}} {{.Ports}}' | awk '/0\.0\.0\.0:8000->/ {print $1}')
if [ -n "$CID_ON_8000" ]; then
  echo "🔌 Port 8000 in use by container $CID_ON_8000 — stopping it..."
  docker stop "$CID_ON_8000" >/dev/null || true
fi

echo "🐳 Starting container with volume mount..."
docker run -d \
  --name nlp_intent_service_container \
  -p 8000:8000 \
  -v "$(pwd)/model_storage:/app/model_storage" \
  nlp_intent_service

# NEW: bail out if docker run failed (e.g., port still busy)
if [ $? -ne 0 ]; then
  echo "❌ Failed to start container. Is port 8000 free?"
  exit 1
fi

echo "✅ NLP Intent Service started!"
echo "📁 Model storage mounted at: $(pwd)/model_storage"
echo "🌐 Service available at: http://localhost:8000"
echo ""
echo "📋 Useful commands:"
echo "  View logs: docker logs -f nlp_intent_service_container"
echo "  Stop: docker stop nlp_intent_service_container"
echo "  Remove: docker rm nlp_intent_service_container"
echo "  Shell access: docker exec -it nlp_intent_service_container /bin/bash"
