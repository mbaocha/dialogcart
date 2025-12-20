# Testing Dialogcart Core Orchestrator

This guide explains how to test the dialogcart-core orchestrator end-to-end.

## Prerequisites

1. **Luma service** must be running and accessible
   - Default: `http://localhost:9001`
   - Set `LUMA_BASE_URL` environment variable if different

2. **Internal APIs** must be running
   - Default: `http://localhost:3000`
   - Set `INTERNAL_API_BASE_URL` environment variable if different
   - Required endpoints:
     - `GET /api/internal/organizations/1/details`
     - `GET /api/internal/organizations/1/customers?email=...`
     - `POST /api/internal/customers`
     - `POST /api/internal/bookings`

3. **Environment variables** (optional, in `.env` file):
   ```
   LUMA_BASE_URL=http://localhost:9001
   INTERNAL_API_BASE_URL=http://localhost:3000
   ```

## Testing Methods

### Method 1: Direct Python Script (Recommended)

Run the test script directly:

```bash
cd dialogcart/src
python core/test_orchestrator_e2e.py
```

Run specific tests:

```bash
# Test resolved booking flow
python core/test_orchestrator_e2e.py --test resolved

# Test partial booking (clarification) flow
python core/test_orchestrator_e2e.py --test partial

# Test with custom message
python core/test_orchestrator_e2e.py --test custom --text "book massage next week" --user-id user123
```

### Method 2: FastAPI Server

Start the FastAPI server:

```bash
cd dialogcart/src
python core/api/main.py
```

Or with uvicorn directly:

```bash
cd dialogcart/src
uvicorn core.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Then test with curl:

```bash
# Test resolved booking
curl -X POST http://localhost:8000/api/message \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test_user_123",
    "text": "book haircut tomorrow at 2pm",
    "domain": "service",
    "timezone": "UTC"
  }'

# Test partial booking
curl -X POST http://localhost:8000/api/message \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test_user_456",
    "text": "book haircut",
    "domain": "service"
  }'
```

### Method 3: Python Interactive

Test directly in Python:

```python
import sys
from pathlib import Path

# Add src/ to path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from core.orchestration.orchestrator import handle_message

result = handle_message(
    user_id="test_user_123",
    text="book haircut tomorrow at 2pm",
    domain="service"
)

print(result)
```

### Method 4: Unit Tests

Run the existing unit tests:

```bash
cd dialogcart/src
pytest core/tests/test_orchestrator_flow.py -v
```

## Expected Results

### Resolved Booking Flow

When Luma returns a RESOLVED booking:
- Orchestrator calls:
  1. `OrganizationClient.get_details(organization_id=1)`
  2. `CustomerClient.get_customer(...)` or `CustomerClient.create_customer(...)`
  3. `BookingClient.create_booking(...)`
- Returns:
  ```json
  {
    "success": true,
    "outcome": {
      "type": "BOOKING_CREATED",
      "booking_code": "...",
      "status": "pending"
    }
  }
  ```

### Partial Booking Flow (Clarification)

When Luma returns `needs_clarification=true`:
- Orchestrator does NOT call any business APIs
- Returns:
  ```json
  {
    "success": true,
    "outcome": {
      "type": "CLARIFY",
      "template_key": "service.ask_time",
      "data": {...}
    }
  }
  ```

### Error Cases

- If Luma returns `success=false` → Returns error response
- If contract violation → Returns `contract_violation` error
- If upstream API errors → Returns `upstream_error`
- If unsupported intent → Returns `unsupported_intent`

## Troubleshooting

1. **"Unable to connect to Luma"**
   - Ensure Luma service is running on port 9001
   - Check `LUMA_BASE_URL` environment variable

2. **"Organization not found"**
   - Ensure internal API is running
   - Check `INTERNAL_API_BASE_URL` environment variable
   - Note: Currently hardcoded to `organization_id=1` for testing

3. **"customer email or phone is required"**
   - Luma booking payload doesn't include customer info
   - You may need to extract from `user_id` or pass separately
   - For now, this will fail if customer info is missing

4. **Import errors**
   - Make sure you're running from `dialogcart/src/` directory
   - Or ensure `src/` is in your Python path

