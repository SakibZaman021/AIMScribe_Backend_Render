"""
AIMScribe AI Backend - Async Worker
Uses asyncio for true parallel processing of jobs.
"""

import os
import sys
import asyncio
import logging
import traceback
from typing import Optional

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from message_queue.redis_async import AsyncRedisClient, TRANSCRIPTION_QUEUE, DEAD_LETTER_QUEUE
from storage.minio_client import get_minio_client
from database.postgres_async import AsyncPostgreSQLDatabase
from config import settings
from processing.transcriber_v4 import TranscriberV4
from processing.ner_extractor import NERExtractor

# Configure Logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format='%(asctime)s - ASYNC_WORKER - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AsyncAIMScribeWorker:
    """
    Async worker for AIMScribe using asyncio.

    Features:
    - True async I/O for database and Redis
    - Parallel NER extraction (ThreadPoolExecutor for LLM calls)
    - Non-blocking job processing
    """

    def __init__(self):
        logger.info("Initializing Async AIMScribe Worker...")

        # Async clients (initialized in start())
        self.redis: Optional[AsyncRedisClient] = None
        self.db: Optional[AsyncPostgreSQLDatabase] = None

        # Sync clients (MinIO, AI processing)
        self.minio = get_minio_client()
        # Using GPT-4o-transcribe-diarize via Audio Transcriptions API
        self.transcriber = TranscriberV4()
        self.ner_extractor = NERExtractor()

        # State
        self.running = True

    async def start(self):
        """Initialize async connections and start worker."""
        logger.info("Starting async connections...")

        # Initialize async PostgreSQL
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

        # Initialize async Redis
        self.redis = AsyncRedisClient(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password
        )
        await self.redis.initialize()

        logger.info("Async worker started. Listening for jobs...")

        # Start main loop
        await self.run()

    async def stop(self):
        """Gracefully stop the worker."""
        self.running = False
        if self.db:
            await self.db.close()
        if self.redis:
            await self.redis.close()
        logger.info("Async worker stopped")

    async def run(self):
        """Main worker loop."""
        logger.info(f"Worker listening on {TRANSCRIPTION_QUEUE}...")

        while self.running:
            try:
                # Non-blocking pop with 5 second timeout
                job = await self.redis.pop_job(TRANSCRIPTION_QUEUE, timeout=5)

                if job:
                    # Process job (could spawn multiple concurrent jobs here)
                    await self.process_job(job)

            except asyncio.CancelledError:
                logger.info("Worker cancelled")
                break
            except Exception as e:
                logger.error(f"Unexpected error in worker loop: {e}")
                await asyncio.sleep(5)  # Backoff

    async def process_job(self, job: dict):
        """Process a single job with async I/O."""
        import time
        start_time = time.time()

        job_id = job.get('job_id')
        session_id = job.get('session_id')
        clip_number = job.get('clip_number')
        object_key = job.get('object_key')

        # Temp file path
        temp_file = f"/tmp/{session_id}_{clip_number}.wav"

        logger.info(f"Processing job {job_id} (Session: {session_id}, Clip: {clip_number})")

        try:
            # 1. Update Status (async)
            await asyncio.gather(
                self.redis.set_job_status(session_id, clip_number, 'processing'),
                self.db.update_clip_status(session_id, clip_number, 'transcribing')
            )

            # 2. Download Audio (sync - MinIO is fast)
            self.minio.download_file(object_key, temp_file)

            # 3. Transcribe (runs in thread pool - CPU/IO bound)
            loop = asyncio.get_event_loop()
            transcript = await loop.run_in_executor(
                None,
                self.transcriber.transcribe,
                temp_file,
                session_id
            )
            duration = 0.0  # TODO: Get actual duration

            # 4. Update Database (async)
            await self.db.complete_clip_transcription(
                session_id,
                clip_number,
                transcript,
                duration
            )

            # 5. Invalidate session cache (async)
            await self.redis.invalidate_session_context(session_id)

            # 6. Check NER Trigger
            clip_count = await self.db.get_clip_count(session_id)
            is_final = job.get('is_final', False)

            if clip_count >= settings.ner_trigger_clips or is_final:
                await self._run_ner_extraction(session_id, is_final)
            else:
                logger.info(f"Skipping NER for session {session_id} (Clips: {clip_count}/{settings.ner_trigger_clips})")

            # 7. Update Status (async)
            await self.redis.set_job_status(session_id, clip_number, 'completed')
            logger.info(f"Job {job_id} completed in {time.time() - start_time:.2f}s")

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            logger.error(traceback.format_exc())

            # Update status (async)
            await asyncio.gather(
                self.db.update_clip_status(session_id, clip_number, 'failed', str(e)),
                self.redis.set_job_status(session_id, clip_number, 'failed', {'error': str(e)}),
                self.redis.move_to_dead_letter(job, str(e))
            )

        finally:
            # Guaranteed cleanup
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                    logger.debug(f"Cleaned up temp file: {temp_file}")
                except OSError as cleanup_error:
                    logger.warning(f"Failed to cleanup temp file {temp_file}: {cleanup_error}")

    async def _run_ner_extraction(self, session_id: str, is_final: bool):
        """Run NER extraction with async database operations."""
        import time
        start_time = time.time()

        logger.info(f"Triggering NER for session {session_id} (Final: {is_final})")

        # Get full transcript (async)
        full_transcript = await self.db.get_full_transcript(session_id)
        if not full_transcript:
            logger.warning("No transcript found for NER")
            return

        # Get session (async)
        session = await self.db.get_session(session_id)
        patient_id = session['patient_id']

        # Check cache first, then DB (async)
        baseline = await self.redis.get_cached_patient_baseline(patient_id)
        if not baseline:
            logger.debug(f"Fetching patient baseline from DB: {patient_id}")
            baseline = await self.db.get_patient_baseline(patient_id)
            await self.redis.cache_patient_baseline(patient_id, baseline, ttl=3600)

        # Get previous medications (async)
        prev_meds = await self.redis.get_cached_previous_medications(patient_id)
        if prev_meds is None:
            logger.debug(f"Fetching previous medications from DB: {patient_id}")
            prev_meds = await self.db.get_last_visit_medications(patient_id)
            if prev_meds:
                await self.redis.cache_previous_medications(patient_id, prev_meds, ttl=3600)

        # Run NER Extraction (in thread pool - CPU bound LLM calls)
        loop = asyncio.get_event_loop()
        ner_result = await loop.run_in_executor(
            None,
            self.ner_extractor.extract_all,
            full_transcript,
            baseline,
            prev_meds['medications'] if prev_meds else None
        )

        # Merge session's health_screening (client input) into NER result
        if session.get('health_screening'):
            ner_result['Health Screening'] = session['health_screening']

        # Save Result (async) - include patient_id
        await self.db.save_ner_result(
            session_id=session_id,
            ner_json=ner_result,
            patient_id=patient_id,
            is_final=is_final
        )

        # If final, archive and invalidate caches (async)
        if is_final:
            await asyncio.gather(
                self.db.finalize_session(session_id),
                self.db.archive_to_previous_visits(
                    session_id=session_id,
                    patient_id=patient_id,
                    doctor_id=session.get('doctor_id', 'AUTO'),
                    ner_json=ner_result
                )
            )

            # Invalidate caches
            await asyncio.gather(
                self.redis.invalidate_patient_baseline(patient_id),
                self.redis.invalidate_session_context(session_id)
            )

            logger.info(f"Session {session_id} finalized and archived")

        logger.info(f"NER extraction completed in {time.time() - start_time:.2f}s")


async def main():
    """Entry point for async worker."""
    worker = AsyncAIMScribeWorker()

    try:
        await worker.start()
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
