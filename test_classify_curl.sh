#!/bin/bash
# Quick curl-based test script for classify endpoint

API_URL="http://localhost:9000/classify"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Classify Endpoint Tester${NC}"
echo "=================================="

# Test function
test_classify() {
    local text="$1"
    local sender_id="${2:-test_user}"
    local validate="${3:-false}"
    
    echo -e "\n${YELLOW}🔍 Testing: '$text'${NC}"
    echo -e "${BLUE}📤 Sender: $sender_id, Validate: $validate${NC}"
    
    response=$(curl -s -X POST "$API_URL" \
        -H "Content-Type: application/json" \
        -d "{\"text\": \"$text\", \"sender_id\": \"$sender_id\", \"validate\": $validate}")
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Success!${NC}"
        echo "$response" | jq '.' 2>/dev/null || echo "$response"
    else
        echo -e "${RED}❌ Error!${NC}"
        echo "$response"
    fi
}

# Example tests
echo -e "\n${YELLOW}📝 Running example tests...${NC}"

test_classify "add rice to cart"
test_classify "remove 2 apples"
test_classify "+ ancarton flour, change rice to 4 carton; dec noodles 2 carton"
test_classify "remove yam, add 2g garri to cart"
test_classify "remove yam, 2g garri to cart"
test_classify "add 3kg sugar and 2 bottles of water"

echo -e "\n${BLUE}💡 To test interactively, run:${NC}"
echo "python test_classify_interactive.py"
echo ""
echo -e "${BLUE}💡 To test a single command:${NC}"
echo "python test_classify_interactive.py 'your text here'"
