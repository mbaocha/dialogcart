@echo off
REM Script to run the NLP Intent Service

echo 🚀 Starting NLP Intent Service...

REM Build the Docker image
echo 🔨 Building Docker image...
docker build -t nlp-intent-service .

REM Create model storage directory if it doesn't exist
if not exist ".\model_storage" mkdir ".\model_storage"

REM Run the container with volume mount
echo 🐳 Starting container with volume mount...
docker run -d ^
  --name nlp-intent-service-container ^
  -p 8000:8000 ^
  -v "%cd%\model_storage:/app/model_storage" ^
  nlp-intent-service

echo ✅ NLP Intent Service started!
echo 📁 Model storage mounted at: %cd%\model_storage
echo 🌐 Service available at: http://localhost:8000
echo.
echo 📋 Useful commands:
echo   View logs: docker logs -f nlp-intent-service-container
echo   Stop: docker stop nlp-intent-service-container
echo   Remove: docker rm nlp-intent-service-container
echo   Shell access: docker exec -it nlp-intent-service-container /bin/bash
