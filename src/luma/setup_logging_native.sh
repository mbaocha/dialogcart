#!/bin/bash
# Setup script for running Luma API natively (without Docker)
# with proper logging configuration

set -e

echo "================================================"
echo "Luma API - Native Logging Setup"
echo "================================================"
echo ""

# Create log directory
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
echo "✅ Created log directory: $LOG_DIR"

# Create .env file if it doesn't exist
if [ ! -f ".env" ]; then
    cat > .env << EOF
# Luma API Configuration
PORT=9001

# Logging Settings
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_FILE=$LOG_DIR/api.log
ENABLE_REQUEST_LOGGING=true
LOG_PERFORMANCE_METRICS=true

# Feature Toggles
ENABLE_LLM_FALLBACK=false
ENABLE_FUZZY_MATCHING=false
ENABLE_INTENT_MAPPER=true
EOF
    echo "✅ Created .env file with logging configuration"
else
    echo "⚠️  .env file already exists, skipping"
fi

# Create logrotate config (Linux only)
if command -v logrotate &> /dev/null; then
    cat > logrotate.conf << EOF
$PWD/$LOG_DIR/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
EOF
    echo "✅ Created logrotate.conf"
    echo ""
    echo "To enable automatic log rotation, add to crontab:"
    echo "  0 0 * * * /usr/sbin/logrotate -s $PWD/logrotate.state $PWD/logrotate.conf"
fi

echo ""
echo "================================================"
echo "Setup Complete!"
echo "================================================"
echo ""
echo "To start the API:"
echo "  cd src"
echo "  python luma/api.py"
echo ""
echo "Logs will be written to:"
echo "  Console: JSON format (stdout)"
echo "  File: $LOG_DIR/api.log"
echo ""
echo "To view logs:"
echo "  tail -f $LOG_DIR/api.log | jq ."
echo ""
echo "For production with gunicorn:"
echo "  gunicorn -w 4 -b 0.0.0.0:9001 luma.api:app > $LOG_DIR/gunicorn.log 2>&1 &"
echo ""






