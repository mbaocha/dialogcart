# Test script for Luma API (PowerShell/Windows)

$API_URL = if ($env:API_URL) { $env:API_URL } else { "http://localhost:9001" }

Write-Host "============================================================"
Write-Host "🧪 Testing Luma Entity Extraction API"
Write-Host "============================================================"
Write-Host "API URL: $API_URL"
Write-Host "============================================================"
Write-Host ""

# Test 1: Health check
Write-Host "1️⃣  Health Check"
Write-Host "GET $API_URL/health"
$response = Invoke-RestMethod -Uri "$API_URL/health" -Method Get
$response | ConvertTo-Json -Depth 10
Write-Host ""
Write-Host "------------------------------------------------------------"
Write-Host ""

# Test 2: API Info
Write-Host "2️⃣  API Info"
Write-Host "GET $API_URL/info"
$response = Invoke-RestMethod -Uri "$API_URL/info" -Method Get
$response | ConvertTo-Json -Depth 10
Write-Host ""
Write-Host "------------------------------------------------------------"
Write-Host ""

# Test 3: Simple extraction
Write-Host "3️⃣  Simple Extraction"
Write-Host "POST $API_URL/extract"
Write-Host 'Body: {"text": "add 2 kg rice"}'
$body = @{ text = "add 2 kg rice" } | ConvertTo-Json
$response = Invoke-RestMethod -Uri "$API_URL/extract" -Method Post -Body $body -ContentType "application/json"
$response | ConvertTo-Json -Depth 10
Write-Host ""
Write-Host "------------------------------------------------------------"
Write-Host ""

# Test 4: Multi-product extraction
Write-Host "4️⃣  Multi-Product Extraction"
Write-Host 'Body: {"text": "add 2 kg rice and 3 bags of beans"}'
$body = @{ text = "add 2 kg rice and 3 bags of beans" } | ConvertTo-Json
$response = Invoke-RestMethod -Uri "$API_URL/extract" -Method Post -Body $body -ContentType "application/json"
$response | ConvertTo-Json -Depth 10
Write-Host ""
Write-Host "------------------------------------------------------------"
Write-Host ""

# Test 5: Ordinal reference
Write-Host "5️⃣  Ordinal Reference"
Write-Host 'Body: {"text": "add item 1"}'
$body = @{ text = "add item 1" } | ConvertTo-Json
$response = Invoke-RestMethod -Uri "$API_URL/extract" -Method Post -Body $body -ContentType "application/json"
$response | ConvertTo-Json -Depth 10
Write-Host ""
Write-Host "------------------------------------------------------------"
Write-Host ""

# Test 6: With brands
Write-Host "6️⃣  With Brands"
Write-Host 'Body: {"text": "buy 5 bottles of Coca-Cola"}'
$body = @{ text = "buy 5 bottles of Coca-Cola" } | ConvertTo-Json
$response = Invoke-RestMethod -Uri "$API_URL/extract" -Method Post -Body $body -ContentType "application/json"
$response | ConvertTo-Json -Depth 10
Write-Host ""
Write-Host "------------------------------------------------------------"
Write-Host ""

# Test 7: Check/availability intent
Write-Host "7️⃣  Check Intent"
Write-Host 'Body: {"text": "do you have rice"}'
$body = @{ text = "do you have rice" } | ConvertTo-Json
$response = Invoke-RestMethod -Uri "$API_URL/extract" -Method Post -Body $body -ContentType "application/json"
$response | ConvertTo-Json -Depth 10
Write-Host ""
Write-Host "------------------------------------------------------------"
Write-Host ""

# Test 8: Referential (memory route)
Write-Host "8️⃣  Referential Request"
Write-Host 'Body: {"text": "add it"}'
$body = @{ text = "add it" } | ConvertTo-Json
$response = Invoke-RestMethod -Uri "$API_URL/extract" -Method Post -Body $body -ContentType "application/json"
$response | ConvertTo-Json -Depth 10
Write-Host ""
Write-Host "------------------------------------------------------------"
Write-Host ""

Write-Host "============================================================"
Write-Host "✅ All tests completed!"
Write-Host "============================================================"

