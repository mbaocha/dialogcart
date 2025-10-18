# Test script to demonstrate Luma API logging (Windows)
# Run after starting the API with docker-compose

$API_URL = "http://localhost:9001"

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "Luma API Logging Demo" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# Test 1: Successful request
Write-Host "Test 1: Successful extraction request" -ForegroundColor Yellow
$body1 = @{text = "add 2 kg rice"} | ConvertTo-Json
Invoke-RestMethod -Uri "$API_URL/extract" -Method Post -Body $body1 -ContentType "application/json" -Headers @{"X-Request-ID"="test-001"} | ConvertTo-Json
Write-Host ""

# Test 2: Another successful request
Write-Host "Test 2: Multiple items extraction" -ForegroundColor Yellow
$body2 = @{text = "add 3 bottles of coca cola and 5kg sugar"} | ConvertTo-Json
Invoke-RestMethod -Uri "$API_URL/extract" -Method Post -Body $body2 -ContentType "application/json" -Headers @{"X-Request-ID"="test-002"} | ConvertTo-Json
Write-Host ""

# Test 3: Missing text parameter (should error)
Write-Host "Test 3: Missing text parameter (expected error)" -ForegroundColor Yellow
try {
    $body3 = @{} | ConvertTo-Json
    Invoke-RestMethod -Uri "$API_URL/extract" -Method Post -Body $body3 -ContentType "application/json" -Headers @{"X-Request-ID"="test-003"} | ConvertTo-Json
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
}
Write-Host ""

# Test 4: Health check
Write-Host "Test 4: Health check" -ForegroundColor Yellow
Invoke-RestMethod -Uri "$API_URL/health" -Method Get -Headers @{"X-Request-ID"="test-004"} | ConvertTo-Json
Write-Host ""
Write-Host ""

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "Tests complete! Now view the logs:" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "View all logs:" -ForegroundColor Green
Write-Host "  docker-compose logs luma-api" -ForegroundColor White
Write-Host ""
Write-Host "View logs with our helper script:" -ForegroundColor Green
Write-Host "  cd src\luma" -ForegroundColor White
Write-Host "  .\view_logs.ps1 -Lines 20" -ForegroundColor White
Write-Host ""
Write-Host "View only errors:" -ForegroundColor Green
Write-Host "  .\view_logs.ps1 -ErrorsOnly" -ForegroundColor White
Write-Host ""
Write-Host "View request statistics:" -ForegroundColor Green
Write-Host "  .\view_logs.ps1 -Stats" -ForegroundColor White
Write-Host ""

