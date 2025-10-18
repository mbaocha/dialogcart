#!/bin/bash
# Test script for Luma API (Linux/Mac/WSL)

API_URL="${API_URL:-http://localhost:9001}"

echo "============================================================"
echo "🧪 Testing Luma Entity Extraction API"
echo "============================================================"
echo "API URL: $API_URL"
echo "============================================================"
echo

# Test 1: Health check
echo "1️⃣  Health Check"
echo "GET $API_URL/health"
curl -s "$API_URL/health" | jq '.'
echo
echo "------------------------------------------------------------"
echo

# Test 2: API Info
echo "2️⃣  API Info"
echo "GET $API_URL/info"
curl -s "$API_URL/info" | jq '.'
echo
echo "------------------------------------------------------------"
echo

# Test 3: Simple extraction
echo "3️⃣  Simple Extraction"
echo "POST $API_URL/extract"
echo 'Body: {"text": "add 2 kg rice"}'
curl -s -X POST "$API_URL/extract" \
  -H "Content-Type: application/json" \
  -d '{"text": "add 2 kg rice"}' | jq '.'
echo
echo "------------------------------------------------------------"
echo

# Test 4: Multi-product extraction
echo "4️⃣  Multi-Product Extraction"
echo 'Body: {"text": "add 2 kg rice and 3 bags of beans"}'
curl -s -X POST "$API_URL/extract" \
  -H "Content-Type: application/json" \
  -d '{"text": "add 2 kg rice and 3 bags of beans"}' | jq '.'
echo
echo "------------------------------------------------------------"
echo

# Test 5: Ordinal reference
echo "5️⃣  Ordinal Reference"
echo 'Body: {"text": "add item 1"}'
curl -s -X POST "$API_URL/extract" \
  -H "Content-Type: application/json" \
  -d '{"text": "add item 1"}' | jq '.'
echo
echo "------------------------------------------------------------"
echo

# Test 6: With brands
echo "6️⃣  With Brands"
echo 'Body: {"text": "buy 5 bottles of Coca-Cola"}'
curl -s -X POST "$API_URL/extract" \
  -H "Content-Type: application/json" \
  -d '{"text": "buy 5 bottles of Coca-Cola"}' | jq '.'
echo
echo "------------------------------------------------------------"
echo

# Test 7: Check/availability intent
echo "7️⃣  Check Intent"
echo 'Body: {"text": "do you have rice"}'
curl -s -X POST "$API_URL/extract" \
  -H "Content-Type: application/json" \
  -d '{"text": "do you have rice"}' | jq '.'
echo
echo "------------------------------------------------------------"
echo

# Test 8: Referential (memory route)
echo "8️⃣  Referential Request"
echo 'Body: {"text": "add it"}'
curl -s -X POST "$API_URL/extract" \
  -H "Content-Type: application/json" \
  -d '{"text": "add it"}' | jq '.'
echo
echo "------------------------------------------------------------"
echo

echo "============================================================"
echo "✅ All tests completed!"
echo "============================================================"

