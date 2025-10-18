# Helper script to view Luma API logs with pretty formatting
# For Windows PowerShell
# Requires: Docker Desktop

param(
    [switch]$Follow,
    [int]$Lines = 50,
    [string]$Level = "",
    [string]$Path = "",
    [switch]$ErrorsOnly,
    [switch]$Stats,
    [switch]$Raw,
    [switch]$Help
)

$ContainerName = "luma-luma-api-1"

function Show-Usage {
    Write-Host @"
Usage: .\view_logs.ps1 [OPTIONS]

Options:
  -Follow          Follow log output (like tail -f)
  -Lines NUM       Show last NUM lines (default: 50)
  -Level LEVEL     Filter by log level (DEBUG, INFO, WARNING, ERROR)
  -Path PATH       Filter by request path
  -ErrorsOnly      Show only errors
  -Stats           Show request statistics
  -Raw             Show raw JSON (no pretty formatting)
  -Help            Show this help message

Examples:
  .\view_logs.ps1 -Follow                    # Follow logs
  .\view_logs.ps1 -Lines 100                 # Show last 100 lines
  .\view_logs.ps1 -Level ERROR               # Show only errors
  .\view_logs.ps1 -Path /extract             # Show only /extract requests
  .\view_logs.ps1 -ErrorsOnly                # Show errors and exceptions
  .\view_logs.ps1 -Stats                     # Show stats about requests
"@
    exit 0
}

if ($Help) {
    Show-Usage
}

# Function to colorize output
function Write-ColorLog {
    param(
        [string]$Message,
        [string]$Level
    )
    
    $color = switch ($Level) {
        "DEBUG" { "Cyan" }
        "INFO" { "Green" }
        "WARNING" { "Yellow" }
        "ERROR" { "Red" }
        "CRITICAL" { "Magenta" }
        default { "White" }
    }
    
    Write-Host $Message -ForegroundColor $color
}

# Show stats
if ($Stats) {
    Write-Host "Calculating request statistics..." -ForegroundColor Cyan
    
    $logs = docker logs $ContainerName 2>&1 | ConvertFrom-Json -ErrorAction SilentlyContinue
    
    $stats = $logs | Where-Object { $_.method -ne $null } | Group-Object -Property path | ForEach-Object {
        $pathLogs = $_.Group
        $avgDuration = ($pathLogs | Measure-Object -Property duration_ms -Average).Average
        $totalDuration = ($pathLogs | Measure-Object -Property duration_ms -Sum).Sum
        $errorCount = ($pathLogs | Where-Object { $_.status_code -ge 400 }).Count
        
        [PSCustomObject]@{
            Path = $_.Name
            Requests = $_.Count
            Errors = $errorCount
            AvgTime = [math]::Round($avgDuration, 2)
            TotalTime = [math]::Round($totalDuration, 2)
        }
    }
    
    $stats | Format-Table -AutoSize
    exit 0
}

# Get logs
if ($Follow) {
    $cmd = "docker logs -f $ContainerName 2>&1"
} else {
    $cmd = "docker logs --tail $Lines $ContainerName 2>&1"
}

# Parse and display logs
Invoke-Expression $cmd | ForEach-Object {
    try {
        $logLine = $_ | ConvertFrom-Json
        
        # Apply filters
        if ($Level -and $logLine.level -ne $Level) { return }
        if ($Path -and $logLine.path -ne $Path) { return }
        if ($ErrorsOnly -and $logLine.level -notin @("ERROR", "CRITICAL") -and !$logLine.exception) { return }
        
        # Format output
        if ($Raw) {
            $_ | ConvertTo-Json -Compress
        } else {
            $timestamp = $logLine.timestamp
            $level = $logLine.level
            $message = $logLine.message
            $requestId = if ($logLine.request_id) { "[$($logLine.request_id)]" } else { "" }
            $duration = if ($logLine.duration_ms) { "($($logLine.duration_ms)ms)" } else { "" }
            
            $output = "$timestamp [$level] $message $requestId $duration"
            Write-ColorLog -Message $output -Level $level
            
            # Show exception if present
            if ($logLine.exception) {
                Write-Host $logLine.exception -ForegroundColor DarkRed
            }
        }
    }
    catch {
        # Not JSON, just print as-is
        Write-Host $_
    }
}

