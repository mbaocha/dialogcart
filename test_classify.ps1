# PowerShell script for testing classify endpoint
param(
    [string]$Text = "",
    [string]$SenderId = "test_user",
    [bool]$Validate = $false
)

$API_URL = "http://localhost:9000/classify"

function Test-Classify {
    param(
        [string]$Text,
        [string]$SenderId = "test_user",
        [bool]$Validate = $false
    )
    
    Write-Host "`n🔍 Testing: '$Text'" -ForegroundColor Yellow
    Write-Host "📤 Sender: $SenderId, Validate: $Validate" -ForegroundColor Blue
    
    $payload = @{
        text = $Text
        sender_id = $SenderId
        validate = $Validate
    } | ConvertTo-Json
    
    try {
        $response = Invoke-RestMethod -Uri $API_URL -Method POST -Body $payload -ContentType "application/json"
        Write-Host "✅ Success!" -ForegroundColor Green
        $response | ConvertTo-Json -Depth 10
    }
    catch {
        Write-Host "❌ Error: $($_.Exception.Message)" -ForegroundColor Red
    }
}

if ($Text) {
    # Single test
    Test-Classify -Text $Text -SenderId $SenderId -Validate $Validate
} else {
    # Interactive mode
    Write-Host "🚀 Interactive Classify Endpoint Tester" -ForegroundColor Blue
    Write-Host "======================================"
    Write-Host "Commands:"
    Write-Host "  - Type your text to test"
    Write-Host "  - 'validate' - toggle validation on/off"
    Write-Host "  - 'sender <id>' - set sender ID"
    Write-Host "  - 'examples' - show example inputs"
    Write-Host "  - 'quit' or 'exit' - exit"
    Write-Host "======================================"
    
    $validate = $false
    $senderId = "test_user"
    
    while ($true) {
        $userInput = Read-Host "`n[$senderId] $('[' + 'VALIDATE' + ']' * $validate) >"
        
        if ($userInput -in @('quit', 'exit', 'q')) {
            Write-Host "👋 Goodbye!" -ForegroundColor Green
            break
        }
        elseif ($userInput -eq 'validate') {
            $validate = -not $validate
            Write-Host "🔄 Validation: $(if ($validate) { 'ON' } else { 'OFF' })" -ForegroundColor Yellow
        }
        elseif ($userInput -like 'sender *') {
            $senderId = $userInput.Substring(7).Trim()
            Write-Host "👤 Sender ID: $senderId" -ForegroundColor Blue
        }
        elseif ($userInput -eq 'examples') {
            Write-Host "`n📝 Example test cases:" -ForegroundColor Yellow
            $examples = @(
                "add rice to cart",
                "remove 2 apples", 
                "change rice to 4 cartons",
                "+ ancarton flour, change rice to 4 carton; dec noodles 2 carton",
                "remove yam, add 2g garri to cart",
                "remove yam, 2g garri to cart",
                "add 3kg sugar and 2 bottles of water",
                "set rice to 5kg",
                "clear cart",
                "show me my cart"
            )
            for ($i = 0; $i -lt $examples.Length; $i++) {
                Write-Host "  $($i + 1). $($examples[$i])"
            }
        }
        elseif ($userInput) {
            Test-Classify -Text $userInput -SenderId $senderId -Validate $validate
        }
        else {
            Write-Host "❓ Please enter some text to test" -ForegroundColor Red
        }
    }
}
