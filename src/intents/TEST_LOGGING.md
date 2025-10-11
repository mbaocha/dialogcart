# Test Logging Control

Follow these steps to verify the logging control is working correctly.

## Step 1: Test Without Debug Logs (Default)

By default, debug logs are disabled. You should only see the JSON response.

### Windows PowerShell
```powershell
cd src\intents
python test_classify_interactive.py
```

Then type: `do you sell mamma gold rice`

**Expected Output:**
Only the final JSON response should be printed:
```json
{
  "brands": ["mama gold"],
  "products": ["rice"],
  ...
}
```

## Step 2: Test With Debug Logs Enabled

Enable debug logs by setting the environment variable.

### Windows PowerShell
```powershell
$env:DEBUG_NLP="1"
python test_classify_interactive.py
```

Then type: `do you sell mamma gold rice`

**Expected Output:**
You should now see detailed debug information including:
- `[DEBUG] Tokens before replacement: ...`
- `[DEBUG] Word: do`
- `[DEBUG] Label: B-ACTION`
- `[DEBUG] align_quantities_to_products INPUT`
- And much more debug info...
- Finally, the JSON response

## Step 3: Disable Debug Logs Again

### Windows PowerShell
```powershell
$env:DEBUG_NLP="0"
python test_classify_interactive.py
```

Then type: `do you sell mamma gold rice`

**Expected Output:**
Only the final JSON response again (no debug logs).

## Alternative: One-Line Test

### Windows PowerShell
```powershell
# With debug
$env:DEBUG_NLP="1"; python -c "from semantics.entity_extraction_pipeline import extract_entities; print(extract_entities('add rice to cart'))"

# Without debug (default)
$env:DEBUG_NLP="0"; python -c "from semantics.entity_extraction_pipeline import extract_entities; print(extract_entities('add rice to cart'))"
```

### Linux/Mac
```bash
# With debug
DEBUG_NLP=1 python -c "from semantics.entity_extraction_pipeline import extract_entities; print(extract_entities('add rice to cart'))"

# Without debug (default)
DEBUG_NLP=0 python -c "from semantics.entity_extraction_pipeline import extract_entities; print(extract_entities('add rice to cart'))"
```

## Summary

- **Default behavior**: Only JSON response (clean output)
- **With `DEBUG_NLP=1`**: Full debug logs + JSON response
- **Environment variable persists** for the current terminal session
- **To reset**: Set `DEBUG_NLP=0` or close the terminal

## Testing with the Interactive Script

The interactive test script at `src/intents/test_classify_interactive.py` will respect the `DEBUG_NLP` environment variable automatically. Just set it before running the script.

