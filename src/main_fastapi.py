"""
AIMScribe AI Backend - FastAPI Server (Async)
High-performance async API with true parallelism.
"""

import logging
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import settings
from database.postgres_async import AsyncPostgreSQLDatabase
from storage.minio_client import get_minio_client, MinIOClient
from message_queue.redis_async import AsyncRedisClient, push_transcription_job_async

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format='%(asctime)s - API - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# Pydantic Models (Request/Response)
# ============================================================================

class SessionCreateRequest(BaseModel):
    patient_id: str
    doctor_id: str = "DR_DEFAULT"
    hospital_id: str = "HOSP_DEFAULT"


class SessionCreateResponse(BaseModel):
    session_id: str
    status: str
    created_at: str


class UploadRequestModel(BaseModel):
    session_id: str
    clip_number: int


class UploadCompleteRequest(BaseModel):
    session_id: str
    clip_number: int
    object_key: str
    is_final: bool = False


class StatusResponse(BaseModel):
    session_id: str
    status: str
    total_clips_transcribed: int
    has_transcript: bool
    has_ner: bool
    ner_version: int
    updated_at: str


class DoctorReviewRequest(BaseModel):
    session_id: str
    doctor_id: str
    field_name: str
    original_value: object = None
    edited_value: object = None


class PrescriptionRequest(BaseModel):
    session_id: str
    doctor_id: str
    prescription: dict


# ============================================================================
# Application Context (Async)
# ============================================================================

class AsyncAppContext:
    """Async application context with connection management."""

    def __init__(self):
        self.db: Optional[AsyncPostgreSQLDatabase] = None
        self.redis: Optional[AsyncRedisClient] = None
        self.minio: Optional[MinIOClient] = None

    async def initialize(self):
        """Initialize all async connections."""
        logger.info("Initializing async connections...")

        # Async PostgreSQL
        self.db = AsyncPostgreSQLDatabase(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            min_connections=settings.postgres_pool_min,
            max_connections=settings.postgres_pool_max
        )
        await self.db.initialize()

        # Async Redis
        self.redis = AsyncRedisClient(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password
        )
        await self.redis.initialize()

        # MinIO (sync is fine - presigned URLs are fast)
        self.minio = get_minio_client()

        logger.info("All async connections initialized")

    async def close(self):
        """Close all connections."""
        if self.db:
            await self.db.close()
        if self.redis:
            await self.redis.close()
        logger.info("All connections closed")


# Global context
ctx = AsyncAppContext()


# ============================================================================
# Application Lifespan
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    # Startup
    await ctx.initialize()
    yield
    # Shutdown
    await ctx.close()


# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="AIMScribe AI Backend",
    description="Async API for Bengali Medical NER Extraction",
    version="6.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Health Check
# ============================================================================

@app.get("/health")
async def health():
    """Health check endpoint."""
    db_healthy = await ctx.db.healthcheck() if ctx.db else False
    redis_healthy = await ctx.redis.healthcheck() if ctx.redis else False

    return {
        "status": "healthy" if (db_healthy and redis_healthy) else "degraded",
        "database": "connected" if db_healthy else "disconnected",
        "redis": "connected" if redis_healthy else "disconnected",
        "minio": "connected",
        "version": "6.0.0",
        "mode": "FastAPI Async"
    }


# ============================================================================
# Session Management
# ============================================================================

@app.post("/api/v1/session/create", response_model=SessionCreateResponse)
async def create_session(request: SessionCreateRequest):
    """Create a new recording session."""
    try:
        # Create session (async)
        session_id = await ctx.db.create_session(
            request.patient_id,
            request.doctor_id,
            request.hospital_id
        )

        # Ensure patient exists (async)
        baseline = await ctx.db.get_patient_baseline(request.patient_id)
        if not baseline['demographics'].get('name'):
            await ctx.db.upsert_patient({
                'patient_id': request.patient_id,
                'name': 'Unknown Patient',
                'age': 'N/A',
                'gender': 'N/A'
            })

        return SessionCreateResponse(
            session_id=session_id,
            status="active",
            created_at=datetime.now().isoformat()
        )

    except Exception as e:
        logger.error(f"Create session failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Audio Upload (Presigned URLs)
# ============================================================================

@app.post("/api/v1/upload/request")
async def request_upload_url(request: UploadRequestModel):
    """Get a presigned URL for uploading an audio clip."""
    try:
        # Generate object key
        object_key = MinIOClient.generate_object_key(
            request.session_id,
            request.clip_number
        )

        # Generate presigned URL (sync - fast operation)
        upload_url = ctx.minio.get_presigned_upload_url(object_key, expires=300)

        # Create pending record (async)
        await ctx.db.save_clip_record(
            request.session_id,
            request.clip_number,
            object_key
        )

        return {
            "upload_url": upload_url,
            "object_key": object_key,
            "expires_in": 300
        }

    except Exception as e:
        logger.error(f"Presigned URL generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/upload/complete")
async def complete_upload(request: UploadCompleteRequest):
    """Notify that upload is complete. Pushes job to processing queue."""
    try:
        # Verify session exists (async)
        session = await ctx.db.get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Push to Redis queue (async)
        job_data = await push_transcription_job_async(
            ctx.redis,
            session_id=request.session_id,
            clip_number=request.clip_number,
            object_key=request.object_key,
            patient_id=session['patient_id'],
            is_final=request.is_final
        )

        # Get queue length (async)
        queue_length = await ctx.redis.queue_length("aimscribe:transcription_queue")

        return {
            "status": "queued",
            "job_id": job_data['job_id'],
            "queue_position": queue_length
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Job queuing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Results & Monitoring
# ============================================================================

@app.get("/api/v1/session/{session_id}/status", response_model=StatusResponse)
async def get_session_status(session_id: str):
    """Get processing status for a session."""
    try:
        # All queries run concurrently with asyncio.gather
        import asyncio

        session, full_transcript, ner_result, clip_count = await asyncio.gather(
            ctx.db.get_session(session_id),
            ctx.db.get_full_transcript(session_id),
            ctx.db.get_latest_ner(session_id),
            ctx.db.get_clip_count(session_id)
        )

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        return StatusResponse(
            session_id=session_id,
            status=session['status'],
            total_clips_transcribed=clip_count,
            has_transcript=bool(full_transcript),
            has_ner=bool(ner_result),
            ner_version=ner_result['version'] if ner_result else 0,
            updated_at=datetime.now().isoformat()
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/transcript/{session_id}")
async def get_transcript(session_id: str):
    """Get full cumulative transcript."""
    try:
        transcript = await ctx.db.get_full_transcript(session_id)
        return {
            "session_id": session_id,
            "transcript": transcript or ""
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/ner/{session_id}")
async def get_ner_result(session_id: str):
    """Get latest NER result with individual fields."""
    try:
        result = await ctx.db.get_latest_ner(session_id)
        if not result:
            return {"version": 0, "fields": None}

        fields = {
            "patient_name": result.get('patient_name'),
            "age": result.get('age'),
            "gender": result.get('gender'),
            "chief_complaints": result.get('chief_complaints'),
            "drug_history": result.get('drug_history'),
            "on_examination": result.get('on_examination'),
            "systemic_examination": result.get('systemic_examination'),
            "additional_notes": result.get('additional_notes'),
            "investigations": result.get('investigations'),
            "diagnosis": result.get('diagnosis'),
            "medications": result.get('medications'),
            "advice": result.get('advice'),
            "follow_up": result.get('follow_up'),
            "health_screening": result.get('health_screening'),
        }

        return {
            "version": result['version'],
            "is_final": result['is_final'],
            "created_at": result['created_at'],
            "fields": fields,
            "full_ner_json": result.get('full_ner_json')
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Doctor Reviews
# ============================================================================

@app.post("/api/v1/doctor-review")
async def save_doctor_review(request: DoctorReviewRequest):
    """Save a doctor's edit to a NER field."""
    try:
        review_id = await ctx.db.save_doctor_review(
            session_id=request.session_id,
            doctor_id=request.doctor_id,
            field_name=request.field_name,
            original_value=request.original_value,
            edited_value=request.edited_value
        )
        return {"review_id": review_id, "status": "saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/doctor-review/{session_id}")
async def get_doctor_reviews(session_id: str):
    """Get all doctor reviews for a session."""
    try:
        reviews = await ctx.db.get_doctor_reviews(session_id)
        return {"session_id": session_id, "reviews": reviews}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Prescriptions
# ============================================================================

@app.post("/api/v1/prescription")
async def save_prescription(request: PrescriptionRequest):
    """Save final prescription data (doctor presses Print)."""
    try:
        prescription_id = await ctx.db.save_prescription(
            session_id=request.session_id,
            doctor_id=request.doctor_id,
            prescription=request.prescription
        )
        return {"prescription_id": prescription_id, "status": "saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/prescription/{session_id}")
async def get_prescription(session_id: str):
    """Get latest prescription for a session."""
    try:
        prescription = await ctx.db.get_prescription(session_id)
        if not prescription:
            return {"session_id": session_id, "prescription": None}
        return {"session_id": session_id, "prescription": prescription}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Entry Point
# ============================================================================

def main():
    """Run the FastAPI server."""
    import uvicorn

    logger.info(f"Starting AIMScribe FastAPI on port {settings.server_port}")
    uvicorn.run(
        "main_fastapi:app",
        host="0.0.0.0",
        port=settings.server_port,
        reload=settings.debug,
        workers=4  # Multiple workers for production
    )


if __name__ == "__main__":
    main()
