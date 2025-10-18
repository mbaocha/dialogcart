# Setup script for running Luma API natively on Windows
# with proper logging configuration

$ErrorActionPreference = "Stop"

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "Luma API - Native Logging Setup (Windows)" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# Create log directory
$LogDir = "logs"
if (!(Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
    Write-Host "✅ Created log directory: $LogDir" -ForegroundColor Green
} else {
    Write-Host "✅ Log directory already exists: $LogDir" -ForegroundColor Green
}

# Create .env file if it doesn't exist
if (!(Test-Path ".env")) {
    @"
# Luma API Configuration
PORT=9001

# Logging Settings
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_FILE=$LogDir/api.log
ENABLE_REQUEST_LOGGING=true
LOG_PERFORMANCE_METRICS=true

# Feature Toggles
ENABLE_LLM_FALLBACK=false
ENABLE_FUZZY_MATCHING=false
ENABLE_INTENT_MAPPER=true
"@ | Out-File -FilePath ".env" -Encoding UTF8
    Write-Host "✅ Created .env file with logging configuration" -ForegroundColor Green
} else {
    Write-Host "⚠️  .env file already exists, skipping" -ForegroundColor Yellow
}

# Create log rotation script
$rotateScript = @"
# Log rotation script for Luma API
`$logPath = "$((Get-Location).Path)\$LogDir\api.log"
`$maxSize = 10MB
`$keepFiles = 7

if (Test-Path `$logPath) {
    if ((Get-Item `$logPath).Length -gt `$maxSize) {
        `$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
        `$archivePath = "$((Get-Location).Path)\$LogDir\api-`$timestamp.log"
        
        Move-Item `$logPath `$archivePath
        Compress-Archive `$archivePath "`$archivePath.zip"
        Remove-Item `$archivePath
        
        # Clean old files
        Get-ChildItem "$((Get-Location).Path)\$LogDir\api-*.zip" | 
            Sort-Object CreationTime -Descending | 
            Select-Object -Skip `$keepFiles | 
            Remove-Item
        
        Write-Host "✅ Rotated log file: `$archivePath.zip"
    }
}
"@

$rotateScript | Out-File -FilePath "rotate_logs.ps1" -Encoding UTF8
Write-Host "✅ Created rotate_logs.ps1" -ForegroundColor Green

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "Setup Complete!" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "To start the API:" -ForegroundColor Yellow
Write-Host "  cd src" -ForegroundColor White
Write-Host "  python luma/api.py" -ForegroundColor White
Write-Host ""
Write-Host "Logs will be written to:" -ForegroundColor Yellow
Write-Host "  Console: JSON format (stdout)" -ForegroundColor White
Write-Host "  File: $LogDir\api.log" -ForegroundColor White
Write-Host ""
Write-Host "To view logs:" -ForegroundColor Yellow
Write-Host "  Get-Content $LogDir\api.log -Wait | ConvertFrom-Json | ConvertTo-Json" -ForegroundColor White
Write-Host ""
Write-Host "To setup automatic log rotation:" -ForegroundColor Yellow
Write-Host "  1. Open Task Scheduler" -ForegroundColor White
Write-Host "  2. Create Basic Task" -ForegroundColor White
Write-Host "  3. Trigger: Daily" -ForegroundColor White
Write-Host "  4. Action: Start a program" -ForegroundColor White
Write-Host "     Program: powershell.exe" -ForegroundColor White
Write-Host "     Arguments: -File `"$((Get-Location).Path)\rotate_logs.ps1`"" -ForegroundColor White
Write-Host ""





