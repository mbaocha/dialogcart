# Intent Service

This directory contains all intent classification services for the DialogCart application.

## Services

### NLP Intent Service
- **Location**: `nlp-intent-service/`
- **Technology**: Rasa NLU
- **Purpose**: Fast, rule-based intent classification
- **Port**: 8000

## Directory Structure

```
intent-service/
├── nlp-intent-service/           # Rasa-based intent service
│   ├── app.py                   # Flask application
│   ├── Dockerfile               # Docker configuration
│   ├── initial_training_data.yml # Training data
│   ├── run.sh                   # Linux/Mac startup script
│   ├── run.bat                  # Windows startup script
│   └── ...
└── README.md                    # This file
```

## Quick Start

### Using the Scripts

**Linux/Mac:**
```bash
cd nlp-intent-service
chmod +x run.sh
./run.sh
```

**Windows:**
```cmd
cd nlp-intent-service
run.bat
```

### Manual Commands

```bash
cd nlp-intent-service
docker build -t nlp-intent-service .
docker run -p 8000:8000 nlp-intent-service
```

## API Usage

### Predict Intent
```bash
curl -X POST http://localhost:8000 \
  -H "Content-Type: application/json" \
  -d '{"action": "predict", "text": "hello"}'
```

### Train New Intent
```bash
curl -X POST http://localhost:8000 \
  -H "Content-Type: application/json" \
  -d '{
    "action": "train",
    "intent": "greet",
    "examples": ["hello", "hi", "hey"]
  }'
```

## Future Services

This directory is designed to accommodate additional intent services:

- `llm-intent-service/` - LLM-based intent classification
- `hybrid-intent-service/` - Combined NLU + LLM approach
- `specialized-intent-service/` - Domain-specific intent classification

## Development

Each service should:
- Follow the same API interface
- Use different ports (8000, 8001, 8002, etc.)
- Have its own Dockerfile and requirements
- Include startup scripts for easy deployment
