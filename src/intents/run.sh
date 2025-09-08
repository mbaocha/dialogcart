#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Parse command line arguments
CLEAR_MODELS=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --cm|--clear-models)
            CLEAR_MODELS=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--cm|--clear-models]"
            echo "  --cm, --clear-models  Clear model storage volumes to force retraining"
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
else
    docker compose -f docker-compose.yml down --remove-orphans
fi

echo "🔧 Killing any lingering docker-proxy processes..."
# Kill docker-proxy processes that might be holding ports
if command -v pkill >/dev/null 2>&1; then
    sudo pkill -f docker-proxy 2>/dev/null || true
    echo "✅ Killed docker-proxy processes"
else
    echo "⚠️  pkill not available, skipping docker-proxy cleanup"
fi

# Wait a moment for processes to fully terminate
sleep 2

echo "Building images..."
docker compose -f docker-compose.yml build --no-cache

echo "Starting services in detached mode..."
docker compose -f docker-compose.yml up -d

echo "✅ Services are up. Endpoints:"
echo "  Unified API: http://localhost:9000/classify (always returns list of intents)"
echo "  Rasa:       http://localhost:8000"
echo "  LLM:        http://localhost:9100/classify (multi-intent)"
echo "  LLM (single): http://localhost:9100/classify-single (backwards compatibility)"
echo "  Session:    http://localhost:9200/health (shared session storage)"
echo ""
echo "🔧 Configuration:"
echo "  RASA_CONFIDENCE_THRESHOLD=${RASA_CONFIDENCE_THRESHOLD:-0.7} (set via env var)"
echo "  Lower values = more Rasa usage, higher values = more LLM fallback"
echo "  SESSION_URL=http://session:9200 (internal Docker network)"
echo ""
echo "🧠 Session Management:"
echo "  - Shared session storage for conversation history"
echo "  - Accessible by both Rasa and LLM services"
echo "  - Supports sender_id-based session isolation"


