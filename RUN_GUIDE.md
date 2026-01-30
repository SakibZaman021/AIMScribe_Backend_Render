# AIMScribe Backend - Complete Setup & Run Guide

## Prerequisites

Before running the backend, ensure you have:

1. **Python 3.10+** installed
2. **PostgreSQL 14+** running
3. **Redis 7+** running
4. **MinIO** running (or use Docker)
5. **Azure OpenAI** access with:
   - GPT-4o-transcribe deployment (for audio transcription)
   - GPT-4o deployment (for NER extraction)

---

## Step 1: Install Python Dependencies

```bash
cd D:\AIMS LAB REVIEW PAPER\pyaudio secondary version\aimscribe-backend

# Create virtual environment (recommended)
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Additional required packages
pip install python-dotenv
```

---

## Step 2: Configure Environment Variables

Edit the `.env` file with your actual credentials:

```bash
# Open .env file and replace placeholders
notepad .env
```

**Required configurations:**

| Variable | Description | Example |
|----------|-------------|---------|
| `AZURE_TRANSCRIBE_ENDPOINT` | Azure OpenAI endpoint for transcription | `https://myresource.openai.azure.com/` |
| `AZURE_TRANSCRIBE_API_KEY` | API key for transcription | `abc123...` |
| `AZURE_TRANSCRIBE_DEPLOYMENT` | Deployment name | `gpt-4o-transcribe` |
| `AZURE_NER_ENDPOINT` | Azure OpenAI endpoint for NER | `https://myresource.openai.azure.com/` |
| `AZURE_NER_API_KEY` | API key for NER | `abc123...` |
| `AZURE_NER_DEPLOYMENT` | Deployment name | `gpt-4o` |
| `POSTGRES_PASSWORD` | Your PostgreSQL password | `yourpassword` |

---

## Step 3: Start Required Services

### Option A: Using Docker (Recommended)

```bash
# Start PostgreSQL, Redis, MinIO with Docker
docker run -d --name postgres \
  -e POSTGRES_DB=aimscribe_db \
  -e POSTGRES_USER=aimscribe_user \
  -e POSTGRES_PASSWORD=yourpassword \
  -p 5432:5432 \
  postgres:14

docker run -d --name redis \
  -p 6379:6379 \
  redis:7

docker run -d --name minio \
  -e MINIO_ROOT_USER=aimscribe \
  -e MINIO_ROOT_PASSWORD=aimscribe123 \
  -p 9000:9000 \
  -p 9001:9001 \
  minio/minio server /data --console-address ":9001"
```

### Option B: Local Installation

1. **PostgreSQL**: Download from https://www.postgresql.org/download/
2. **Redis**: Download from https://redis.io/download/ (or use Windows WSL)
3. **MinIO**: Download from https://min.io/download

---

## Step 4: Initialize Database

```bash
# Run setup script
python scripts/setup.py
```

This will:
- Create database tables
- Verify Redis connection
- Create MinIO bucket
- Verify Azure configuration

---

## Step 5: Test API Connections

```bash
# Run API tests
python tests/test_azure_apis.py
```

Expected output:
```
============================================================
       AIMScribe Backend - API & Services Test
============================================================

============================================================
  TEST 1: Environment Variables
============================================================
✅ PASS: Transcription API Endpoint (AZURE_TRANSCRIBE_ENDPOINT)
✅ PASS: Transcription API Key (AZURE_TRANSCRIBE_API_KEY)
...

============================================================
  TEST 2: NER API (GPT-4o)
============================================================
  Sending test request to NER API...
  Response: NER API কাজ করছে!
✅ PASS: NER API is working!

...
```

---

## Step 6: Run the Backend

You need to run **TWO** processes:

### Terminal 1: API Server

```bash
# Option A: Flask (Synchronous)
python src/main.py

# Option B: FastAPI (Async - Recommended)
python src/main_fastapi.py
```

Expected output:
```
2024-XX-XX 10:00:00 - INFO - AIMScribe AI Backend starting...
2024-XX-XX 10:00:00 - INFO - Server running on http://0.0.0.0:6000
```

### Terminal 2: Worker

```bash
# Option A: Standard Worker
python src/worker.py

# Option B: Async Worker (for FastAPI)
python src/worker_async.py
```

Expected output:
```
2024-XX-XX 10:00:00 - WORKER - INFO - Initializing AIMScribe Worker...
2024-XX-XX 10:00:00 - WORKER - INFO - Worker started. Listening on aimscribe:queue:transcription...
```

---

## Step 7: Verify System is Running

### Check API Health

```bash
curl http://localhost:6000/health
```

Expected response:
```json
{"status": "healthy", "version": "1.0.0"}
```

### Check Worker Status

The worker will show:
```
Worker started. Listening on aimscribe:queue:transcription...
```

---

## Complete Workflow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENT SIDE                              │
│  AIMScribe.exe → Records Audio → Sends to API                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     API SERVER (Port 6000)                      │
│  POST /api/upload-clip                                          │
│    1. Save audio to MinIO                                       │
│    2. Create job in Redis queue                                 │
│    3. Return job_id                                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         REDIS QUEUE                             │
│  aimscribe:queue:transcription                                  │
│    { session_id, clip_number, object_key, is_final }           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                           WORKER                                │
│  1. Pop job from queue                                          │
│  2. Download audio from MinIO                                   │
│  3. Transcribe with GPT-4o-transcribe (Bengali + Speaker)      │
│  4. Save transcript to PostgreSQL                               │
│  5. Check if NER should run (≥2 clips OR is_final)             │
│  6. If yes → Run parallel NER extraction                        │
│  7. Save NER JSON to PostgreSQL                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        POSTGRESQL                               │
│  Tables:                                                        │
│    - patients (demographics)                                    │
│    - health_screening (baseline)                                │
│    - sessions (consultations)                                   │
│    - audio_clips (transcripts)                                  │
│    - ner_extractions (extracted entities)                       │
│    - previous_visits (archived for follow-up)                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    DOCTOR DASHBOARD                             │
│  GET /api/session/{session_id}/ner                              │
│    → Returns NER JSON with medications, diagnosis, etc.         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Troubleshooting

### Error: "Connection refused" (PostgreSQL)
```bash
# Check if PostgreSQL is running
docker ps | grep postgres

# Or on Windows
pg_isready -h localhost -p 5432
```

### Error: "Connection refused" (Redis)
```bash
# Check if Redis is running
docker ps | grep redis

# Or test connection
redis-cli ping
```

### Error: "Azure API Error"
1. Check your API key in `.env`
2. Verify the deployment name exists in Azure OpenAI Studio
3. Ensure API version is correct: `2024-02-15-preview`

### Error: "MinIO bucket not found"
```bash
# Create bucket manually
python -c "from minio import Minio; c = Minio('localhost:9000', 'aimscribe', 'aimscribe123', secure=False); c.make_bucket('aimscribe-audio')"
```

---

## Quick Commands Reference

```bash
# Activate virtual environment
venv\Scripts\activate

# Run tests
python tests/test_azure_apis.py

# Start API server
python src/main.py

# Start worker (in another terminal)
python src/worker.py

# Check logs
tail -f logs/aimscribe.log
```

---

## Port Reference

| Service | Port | Description |
|---------|------|-------------|
| API Server | 6000 | Flask/FastAPI HTTP API |
| PostgreSQL | 5432 | Database |
| Redis | 6379 | Job queue & cache |
| MinIO API | 9000 | Object storage |
| MinIO Console | 9001 | MinIO web UI |
