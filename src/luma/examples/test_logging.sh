#!/bin/bash
# Test script to demonstrate Luma API logging
# Run after starting the API with docker-compose

set -e

API_URL="http://localhost:9001"

echo "================================================"
echo "Luma API Logging Demo"
echo "================================================"
echo ""

# Test 1: Successful request
echo "Test 1: Successful extraction request"
curl -X POST "$API_URL/extract" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: test-001" \
  -d '{"text": "add 2 kg rice"}' \
  -s | jq .
echo ""

# Test 2: Another successful request
echo "Test 2: Multiple items extraction"
curl -X POST "$API_URL/extract" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: test-002" \
  -d '{"text": "add 3 bottles of coca cola and 5kg sugar"}' \
  -s | jq .
echo ""

# Test 3: Missing text parameter (should error)
echo "Test 3: Missing text parameter (expected error)"
curl -X POST "$API_URL/extract" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: test-003" \
  -d '{}' \
  -s | jq .
echo ""

# Test 4: Invalid JSON (should error)
echo "Test 4: Invalid JSON format (expected error)"
curl -X POST "$API_URL/extract" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: test-004" \
  -d 'not json' \
  -s
echo ""
echo ""

# Test 5: Health check
echo "Test 5: Health check"
curl -X GET "$API_URL/health" \
  -H "X-Request-ID: test-005" \
  -s | jq .
echo ""
echo ""

echo "================================================"
echo "Tests complete! Now view the logs:"
echo "================================================"
echo ""
echo "View all logs:"
echo "  docker-compose logs luma-api"
echo ""
echo "View logs with our helper script:"
echo "  cd src/luma && ./view_logs.sh -n 20"
echo ""
echo "View only errors:"
echo "  cd src/luma && ./view_logs.sh -e"
echo ""
echo "View request statistics:"
echo "  cd src/luma && ./view_logs.sh -s"
echo ""
echo "Filter with jq:"
echo "  docker logs luma-luma-api-1 2>&1 | jq 'select(.request_id | startswith(\"test-\"))'"
echo ""

