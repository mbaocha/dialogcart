# What Logs Look Like - Examples

## Single Request Example

### Request:
```bash
curl -X POST http://localhost:9001/extract \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: demo-001" \
  -d '{"text": "add 2 kg rice"}'
```

### Logs Generated (3 separate JSON lines):

#### Log 1: Request received and processing starts
```json
{
  "timestamp": "2025-10-11T14:32:15.050Z",
  "level": "INFO",
  "logger": "luma-api",
  "message": "Processing extraction request",
  "request_id": "demo-001",
  "text_length": 15,
  "force_llm": false
}
```

#### Log 2: Extraction completed
```json
{
  "timestamp": "2025-10-11T14:32:15.098Z",
  "level": "INFO",
  "logger": "luma-api",
  "message": "Extraction completed successfully",
  "request_id": "demo-001",
  "processing_time_ms": 45.32,
  "groups_count": 1,
  "route": "rule",
  "text_length": 15
}
```

#### Log 3: Request completed with HTTP details
```json
{
  "timestamp": "2025-10-11T14:32:15.123Z",
  "level": "INFO",
  "logger": "luma-api",
  "message": "POST /extract 200",
  "request_id": "demo-001",
  "method": "POST",
  "path": "/extract",
  "status_code": 200,
  "duration_ms": 73.45
}
```

---

## Error Example

### Request with missing text:
```bash
curl -X POST http://localhost:9001/extract \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: demo-002" \
  -d '{}'
```

### Logs Generated (2 lines):

#### Log 1: Warning about missing parameter
```json
{
  "timestamp": "2025-10-11T14:33:20.100Z",
  "level": "WARNING",
  "logger": "luma-api",
  "message": "Missing 'text' parameter",
  "request_id": "demo-002"
}
```

#### Log 2: Request completed with 400 error
```json
{
  "timestamp": "2025-10-11T14:33:20.105Z",
  "level": "INFO",
  "logger": "luma-api",
  "message": "POST /extract 400",
  "request_id": "demo-002",
  "method": "POST",
  "path": "/extract",
  "status_code": 400,
  "duration_ms": 5.23
}
```

---

## Multiple Concurrent Requests

When handling multiple requests simultaneously, logs are interleaved but **each has a unique request_id**:

```json
{"timestamp":"2025-10-11T14:35:00.100Z","message":"Processing extraction request","request_id":"req-001",...}
{"timestamp":"2025-10-11T14:35:00.102Z","message":"Processing extraction request","request_id":"req-002",...}
{"timestamp":"2025-10-11T14:35:00.103Z","message":"Processing extraction request","request_id":"req-003",...}
{"timestamp":"2025-10-11T14:35:00.145Z","message":"Extraction completed successfully","request_id":"req-001",...}
{"timestamp":"2025-10-11T14:35:00.148Z","message":"POST /extract 200","request_id":"req-001",...}
{"timestamp":"2025-10-11T14:35:00.152Z","message":"Extraction completed successfully","request_id":"req-002",...}
{"timestamp":"2025-10-11T14:35:00.155Z","message":"POST /extract 200","request_id":"req-002",...}
{"timestamp":"2025-10-11T14:35:00.167Z","message":"Extraction completed successfully","request_id":"req-003",...}
{"timestamp":"2025-10-11T14:35:00.170Z","message":"POST /extract 200","request_id":"req-003",...}
```

---

## How to View Logs

### Raw Docker logs (JSONL format):
```bash
docker logs luma-luma-api-1 2>&1
```

### Pretty print all logs:
```bash
docker logs luma-luma-api-1 2>&1 | jq .
```

### Follow a specific request:
```bash
docker logs luma-luma-api-1 2>&1 | jq 'select(.request_id == "demo-001")'
```

### Use helper script for pretty output:
```bash
cd src/luma
./view_logs.sh -f
```

Output:
```
[INFO] 14:32:15.050 luma-api: Processing extraction request (req_id=demo-001, text_length=15)
[INFO] 14:32:15.098 luma-api: Extraction completed successfully (req_id=demo-001, duration=45.32ms)
[INFO] 14:32:15.123 luma-api: POST /extract 200 (req_id=demo-001, status=200, duration=73.45ms)
```

---

## Understanding the Format

### JSON Lines (JSONL)
- ✅ Each line is a **complete, valid JSON object**
- ✅ Lines are **independent** (not in an array)
- ✅ Easy to stream and process line-by-line
- ✅ Standard format for log aggregation systems

### NOT like this (array):
```json
[
  {"log": "entry1"},
  {"log": "entry2"}
]
```

### Instead like this (JSONL):
```json
{"log": "entry1"}
{"log": "entry2"}
```

This format is:
- ✅ Easier to stream
- ✅ More efficient (no need to load entire file)
- ✅ Compatible with CloudWatch, Elasticsearch, etc.
- ✅ Works great with `jq` and other tools

---

## Common Queries

### Get all logs for one request:
```bash
docker logs luma-luma-api-1 2>&1 | jq 'select(.request_id == "demo-001")'
```

### Count logs by level:
```bash
docker logs luma-luma-api-1 2>&1 | jq -s 'group_by(.level) | map({level: .[0].level, count: length})'
```

### Find slowest requests:
```bash
docker logs luma-luma-api-1 2>&1 | jq 'select(.duration_ms != null)' | jq -s 'sort_by(.duration_ms) | reverse | .[0:10]'
```

### Average processing time:
```bash
docker logs luma-luma-api-1 2>&1 | jq -s '[.[] | select(.processing_time_ms != null) | .processing_time_ms] | add / length'
```

---

## Key Takeaways

1. **Each log = One JSON line** (not an array)
2. **One request = Multiple log lines** (linked by `request_id`)
3. **Multiple requests = Interleaved logs** (filter by `request_id`)
4. **Format = JSON Lines (JSONL)** (standard for structured logging)
5. **Use `jq` to query** (or helper scripts for pretty output)





