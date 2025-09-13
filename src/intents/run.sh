#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Parse command line arguments
CLEAR_MODELS=false
FORCE_REBUILD=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --cm|--clear-models)
            CLEAR_MODELS=true
            shift
            ;;
        --force-rebuild)
            FORCE_REBUILD=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--cm|--clear-models] [--force-rebuild]"
            echo "  --cm, --clear-models  Clear model storage volumes to force retraining"
            echo "  --force-rebuild       Force rebuild app image (ignore cache)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo "Stopping compose services (if any)..."
if [ "$CLEAR_MODELS" = true ]; then
    echo "🗑️  Clearing model storage volumes..."
    docker compose -f docker-compose.yml down --remove-orphans --volumes
    
    # Also clear the bind-mounted storage directory
    echo "🗑️  Clearing model files from storage directory..."
    if [ -d "trainings/storage" ]; then
        rm -rf trainings/storage/*
        echo "✅ Cleared all files from trainings/storage/"
    else
        echo "⚠️  trainings/storage directory not found"
    fi
else
    docker compose -f docker-compose.yml down --remove-orphans
fi

echo "🔧 Killing any lingering docker-proxy processes..."
if command -v pkill >/dev/null 2>&1; then
    sudo pkill -f docker-proxy 2>/dev/null || true
    echo "✅ Killed docker-proxy processes"
else
    echo "⚠️  pkill not available, skipping docker-proxy cleanup"
fi

sleep 2

echo "Building images efficiently..."

# Base image: build only if missing
if ! docker image inspect my-rasa-base:3.6.10 >/dev/null 2>&1; then
    echo "Building base image (not found)..."
    docker build -f Dockerfile.base -t my-rasa-base:3.6.10 .
else
    echo "✅ Base image exists, skipping build"
fi

# App image: rebuild optionally with no-cache
echo "Building Intent Classifier image..."
if [ "$FORCE_REBUILD" = true ]; then
    echo "🔄 Force rebuild enabled - ignoring cache"
    docker build --no-cache -f Dockerfile -t dialogcart-intent-classifier .
else
    docker build -f Dockerfile -t dialogcart-intent-classifier .
fi

echo "Starting services in detached mode..."
docker compose -f docker-compose.yml up -d

echo "✅ Services are up. Endpoints:"
echo "  Intent Classifier: http://localhost:9000/classify (Rasa + LLM validation)"
echo ""
echo "📝 Usage Examples:"
echo "  curl -X POST http://localhost:9000/classify -d '{\"text\": \"add rice\", \"validate\": true}'"
echo "  curl -X GET http://localhost:9000/health"
echo ""
echo "🔧 Configuration:"
echo "  RASA_CONFIDENCE_THRESHOLD=\${RASA_CONFIDENCE_THRESHOLD:-0.85} (set via env var)"
echo "  Lower values = more Rasa usage, higher values = more LLM validation"
echo "  OPENAI_API_KEY required for LLM validation"
echo "  INTENT_CLASSIFIER_PORT=9000 (API port)"
echo ""
echo "🧠 Architecture:"
echo "  - Rasa handles intent classification and entity extraction"
echo "  - LLM validator corrects low-confidence Rasa results"
echo "  - All services embedded in intent classifier container for simplicity"
echo ""
echo "💡 Tips:"
echo "  - Use --force-rebuild to ignore Docker cache and rebuild the app image"
echo "  - Use --clear-models to force retraining of Rasa models"
echo "  - Docker automatically uses cache when files haven't changed"
