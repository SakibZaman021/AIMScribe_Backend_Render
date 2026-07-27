"""
Database access for the v2 integrity layer.

Kept separate from `database/postgres_async.py` so the existing transcription and
NER pipeline is untouched; this module only ever reads and writes the tables
created by `scripts/002_v2_integrity.sql`. It shares that module's connection
pool, because Neon's connection budget is small.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg

import integrity
from integrity import ChainEntry

logger = logging.getLogger(__name__)

# One lock id for the audit chain. Appending has to be serialised or two
# concurrent writers can both read the same prev_hash and fork the chain.
AUDIT_LOCK_ID = 0x41494D53  # 'AIMS'


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(token: str) -> bytes:
    return integrity.sha256_bytes(token.encode("utf-8"))


def new_token() -> str:
    return secrets.token_urlsafe(32)


class V2Repository:
    """All v2 queries. Every method takes and releases one pooled connection."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    # ============================================================
    # Hospitals
    # ============================================================

    async def upsert_hospital(self, hospital_id: str, name: str, timezone_name: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO hospitals (hospital_id, name, timezone)
                VALUES ($1, $2, $3)
                ON CONFLICT (hospital_id) DO UPDATE
                   SET name = EXCLUDED.name, timezone = EXCLUDED.timezone
            """, hospital_id, name, timezone_name)

    async def hospital_timezone(self, hospital_id: str) -> str:
        async with self._pool.acquire() as conn:
            tz = await conn.fetchval(
                "SELECT timezone FROM hospitals WHERE hospital_id = $1", hospital_id)
        return tz or "Asia/Dhaka"

    # ============================================================
    # Enrollment
    # ============================================================

    async def create_enrollment_token(
        self, *, hospital_id: str, doctor_id: str, created_by: str,
        ttl_hours: int = 72
    ) -> str:
        """
        Mint a single-use token. Only the hash is stored.

        The token carries both the hospital and the doctor, so the machine
        receives its identity rather than asserting one. This is the only point
        at which a doctor is named, and an administrator does it.
        """
        token = new_token()
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO enrollment_tokens
                    (token_sha256, hospital_id, doctor_id, created_by, expires_at)
                VALUES ($1, $2, $3, $4, $5)
            """, hash_token(token), hospital_id, doctor_id, created_by,
                 _utcnow() + timedelta(hours=ttl_hours))
        return token

    async def enroll_device(
        self,
        *,
        token: str,
        tpm_pubkey: bytes,
        machine_name: str,
        os_version: str,
        app_version: str,
        protocol_version: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Consume an enrollment token and register the device.

        Returns None when the token is unknown, expired, or already used. The
        whole thing runs in one transaction so a token cannot be redeemed twice
        by two concurrent requests.
        """
        device_token = new_token()

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("""
                    SELECT hospital_id, doctor_id, expires_at, used_at
                    FROM enrollment_tokens
                    WHERE token_sha256 = $1
                    FOR UPDATE
                """, hash_token(token))

                if row is None:
                    return None
                if row["used_at"] is not None:
                    logger.warning("Enrollment token replay attempt")
                    return None
                if row["expires_at"] < _utcnow():
                    logger.warning("Expired enrollment token presented")
                    return None

                device = await conn.fetchrow("""
                    INSERT INTO devices
                        (hospital_id, doctor_id, tpm_pubkey, token_sha256,
                         machine_name, os_version, app_version, protocol_version)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING device_id, hospital_id, doctor_id
                """, row["hospital_id"], row["doctor_id"], tpm_pubkey,
                     hash_token(device_token), machine_name, os_version,
                     app_version, protocol_version)

                await conn.execute("""
                    UPDATE enrollment_tokens
                       SET used_at = now(), device_id = $2
                     WHERE token_sha256 = $1
                """, hash_token(token), device["device_id"])

        return {
            "device_id": str(device["device_id"]),
            "hospital_id": device["hospital_id"],
            # The machine learns which doctor it belongs to here, and nowhere
            # else. Nothing in the browser can change it.
            "doctor_id": device["doctor_id"],
            "device_token": device_token,
        }

    async def device_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT device_id, hospital_id, tpm_pubkey, app_version, revoked_at
                FROM devices WHERE token_sha256 = $1
            """, hash_token(token))
        return dict(row) if row else None

    async def touch_device(
        self, device_id, *, spool_bytes: Optional[int] = None,
        pending_segments: Optional[int] = None, app_version: Optional[str] = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                UPDATE devices
                   SET last_seen_at = now(),
                       spool_bytes = COALESCE($2, spool_bytes),
                       pending_segments = COALESCE($3, pending_segments),
                       app_version = COALESCE($4, app_version)
                 WHERE device_id = $1
            """, device_id, spool_bytes, pending_segments, app_version)

    async def revoke_device(self, device_id, reason: str) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE devices SET revoked_at = now(), revoked_reason = $2,
                                   token_sha256 = NULL
                 WHERE device_id = $1 AND revoked_at IS NULL
            """, device_id, reason)
        return result.endswith("1")

    # ============================================================
    # Sessions
    # ============================================================

    async def open_session(
        self,
        *,
        session_id: str,
        hospital_id: str,
        doctor_id: str,
        patient_id: str,
        device_id,
        session_date,
        opened_at: datetime,
        audio: Dict[str, int],
        consent_method: str,
        genesis: ChainEntry,
    ) -> bool:
        """
        Create a v2 session and store its genesis chain entry atomically.

        Idempotent: an agent that retries after a network failure gets True again
        rather than a duplicate-key error.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchval(
                    "SELECT session_id FROM sessions WHERE session_id = $1", session_id)
                if existing:
                    return True

                await conn.execute("""
                    INSERT INTO sessions
                        (session_id, patient_id, doctor_id, hospital_id, device_id,
                         protocol_version, status, session_date, opened_at,
                         recording_date, sample_rate, channels, sample_width,
                         consent_obtained, consent_method, consent_at)
                    VALUES ($1,$2,$3,$4,$5, 2, 'active', $6,$7,$6, $8,$9,$10,
                            TRUE, $11, $7)
                """, session_id, patient_id, doctor_id, hospital_id, device_id,
                     session_date, opened_at,
                     audio.get("sample_rate"), audio.get("channels"),
                     audio.get("sample_width"), consent_method)

                await self._insert_chain_entry(conn, session_id, genesis)
        return True

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT session_id, patient_id, doctor_id, hospital_id, device_id,
                       protocol_version, status, session_date, opened_at, closed_at,
                       segment_count, chain_head_hash, archive_relpath, archived_at,
                       quarantine_reason, sample_rate, channels, sample_width
                FROM sessions WHERE session_id = $1
            """, session_id)
        return dict(row) if row else None

    async def close_session(
        self, session_id: str, *, closed_at: datetime, duration_seconds: float,
        paused_seconds: float, segment_count: int, chain_head: bytes,
        manifest: Dict[str, Any],
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                UPDATE sessions s
                   SET closed_at = $2, total_duration_seconds = $3,
                       paused_seconds = $4, segment_count = $5,
                       chain_head_hash = $6, chain_verified_at = now(),
                       manifest = $7, status = 'closed', updated_at = now(),

                       -- Wall-clock columns, in the hospital's own timezone, so
                       -- what the console shows matches the filename. opened_at
                       -- and closed_at stay UTC: one is for reading, the other
                       -- for arithmetic, and conflating them is how evening
                       -- consultations end up filed under the previous day.
                       recording_date = (s.opened_at AT TIME ZONE h.timezone)::date,
                       start_time     = (s.opened_at AT TIME ZONE h.timezone)::time,
                       end_time       = ($2 AT TIME ZONE h.timezone)::time
                  FROM hospitals h
                 WHERE s.session_id = $1 AND h.hospital_id = s.hospital_id
            """, session_id, closed_at, duration_seconds, paused_seconds,
                 segment_count, chain_head, json.dumps(manifest, ensure_ascii=False))

    async def quarantine_session(self, session_id: str, reason: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                UPDATE sessions
                   SET status = 'quarantined', quarantine_reason = $2, updated_at = now()
                 WHERE session_id = $1
            """, session_id, reason[:500])

    # ============================================================
    # Chain
    # ============================================================

    @staticmethod
    async def _insert_chain_entry(conn, session_id: str, entry: ChainEntry) -> None:
        await conn.execute("""
            INSERT INTO chain_entries
                (session_id, entry_no, entry_type, payload, payload_sha256,
                 prev_hash, entry_hash, device_signature, occurred_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (session_id, entry_no) DO NOTHING
        """, session_id, entry.entry_no, entry.entry_type,
             json.dumps(entry.payload, ensure_ascii=False),
             entry.payload_sha256, entry.prev_hash, entry.entry_hash,
             entry.signature, entry.occurred_at)

    async def append_chain_entry(self, session_id: str, entry: ChainEntry) -> None:
        async with self._pool.acquire() as conn:
            await self._insert_chain_entry(conn, session_id, entry)

    async def chain_head(self, session_id: str) -> Optional[bytes]:
        """Hash of the highest-numbered entry, or None for an empty chain."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT entry_hash FROM chain_entries
                 WHERE session_id = $1
                 ORDER BY entry_no DESC LIMIT 1
            """, session_id)

    async def next_entry_no(self, session_id: str) -> int:
        async with self._pool.acquire() as conn:
            highest = await conn.fetchval("""
                SELECT max(entry_no) FROM chain_entries WHERE session_id = $1
            """, session_id)
        return 0 if highest is None else highest + 1

    async def load_chain(self, session_id: str) -> List[ChainEntry]:
        """Ordered by entry_no; verify_chain requires entries in sequence."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT entry_no, entry_type, payload, payload_sha256,
                       prev_hash, entry_hash, device_signature
                FROM chain_entries WHERE session_id = $1 ORDER BY entry_no
            """, session_id)

        return [
            ChainEntry(
                entry_no=row["entry_no"],
                entry_type=row["entry_type"],
                payload=json.loads(row["payload"]) if isinstance(row["payload"], str)
                        else row["payload"],
                payload_sha256=bytes(row["payload_sha256"]),
                prev_hash=bytes(row["prev_hash"]) if row["prev_hash"] else None,
                entry_hash=bytes(row["entry_hash"]),
                signature=bytes(row["device_signature"]) if row["device_signature"] else None,
            )
            for row in rows
        ]

    # ============================================================
    # Segments
    # ============================================================

    async def commit_segment(
        self,
        *,
        session_id: str,
        seq_no: int,
        entry: ChainEntry,
        object_key: str,
        byte_length: int,
        duration_seconds: float,
        sha256: bytes,
        rms_mean: Optional[float],
        captured_start_at: Optional[datetime],
        captured_end_at: Optional[datetime],
        is_final: bool,
        clip_name: Optional[str] = None,
    ) -> str:
        """
        Store a verified segment and its chain entry.

        Returns 'stored', or 'duplicate' when the same seq_no arrives again with
        an identical hash - a retry, which must succeed rather than error.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    "SELECT sha256 FROM segments WHERE session_id = $1 AND seq_no = $2",
                    session_id, seq_no)

                if existing is not None:
                    return "duplicate" if bytes(existing["sha256"]) == sha256 else "conflict"

                await self._insert_chain_entry(conn, session_id, entry)
                await conn.execute("""
                    INSERT INTO segments
                        (session_id, seq_no, entry_no, object_key, bytes,
                         duration_seconds, sha256, rms_mean, captured_start_at,
                         captured_end_at, is_final, state, clip_name)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'committed',$12)
                """, session_id, seq_no, entry.entry_no, object_key, byte_length,
                     duration_seconds, sha256, rms_mean, captured_start_at,
                     captured_end_at, is_final, clip_name)

                await conn.execute("""
                    UPDATE sessions
                       SET segment_count = (SELECT count(*) FROM segments
                                             WHERE session_id = $1),
                           updated_at = now()
                     WHERE session_id = $1
                """, session_id)
        return "stored"

    async def segments_for(self, session_id: str) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT seq_no, object_key, clip_name, bytes, duration_seconds,
                       sha256, captured_start_at, captured_end_at, is_final, state,
                       object_deleted_at
                FROM segments WHERE session_id = $1 ORDER BY seq_no
            """, session_id)
        return [dict(row) for row in rows]

    async def pauses_for(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Every pause and resume, read from the signed chain.

        Taken from chain_entries rather than a summary column so the reason handed
        to the archive is the one the device signed at the time.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT entry_no, entry_type, occurred_at, payload
                FROM chain_entries
                WHERE session_id = $1 AND entry_type IN ('pause', 'resume')
                ORDER BY entry_no
            """, session_id)

        pauses = []
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            payload = payload or {}
            pauses.append({
                "entry_no": row["entry_no"],
                "entry_type": row["entry_type"],
                "occurred_at": row["occurred_at"].isoformat() if row["occurred_at"] else None,
                "reason": payload.get("reason"),
                "authorised_by": payload.get("authorised_by"),
                "seconds": payload.get("seconds"),
            })
        return pauses

    async def mark_object_deleted(self, session_id: str, seq_no: int) -> None:
        """Record that the bucket copy is gone. The archive copy is unaffected."""
        async with self._pool.acquire() as conn:
            await conn.execute("""
                UPDATE segments SET object_deleted_at = now()
                 WHERE session_id = $1 AND seq_no = $2
            """, session_id, seq_no)

    async def mark_segments_archived(self, session_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                UPDATE segments SET state = 'archived', archived_at = now()
                 WHERE session_id = $1 AND state = 'committed'
            """, session_id)

    # ============================================================
    # Archive (Option B: the AIMS LAB worker pulls)
    # ============================================================

    async def pending_archive(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Sessions that are closed, verified, and not yet archived.

        Quarantined sessions are excluded: they are held for review, never
        archived automatically and never purged from the agent.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT session_id, hospital_id, doctor_id, patient_id, session_date,
                       opened_at, closed_at, total_duration_seconds, segment_count,
                       sample_rate, channels, sample_width, manifest
                FROM sessions
                WHERE closed_at IS NOT NULL
                  AND archived_at IS NULL
                  AND status = 'closed'
                  AND protocol_version >= 2
                ORDER BY closed_at
                LIMIT $1
            """, limit)
        return [dict(row) for row in rows]

    async def mark_archived(
        self, session_id: str, *, relpath: str, sha256: bytes, byte_length: int
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                UPDATE sessions
                   SET archive_relpath = $2, archive_sha256 = $3,
                       archive_bytes = $4, archived_at = now(),
                       status = 'archived', updated_at = now()
                 WHERE session_id = $1
            """, session_id, relpath, sha256, byte_length)

    # ============================================================
    # Purge receipts
    # ============================================================

    async def store_receipt(
        self, *, session_id: str, scope: str, seq_no: Optional[int],
        sha256: bytes, payload: Dict[str, Any], signature: bytes,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO purge_receipts
                    (session_id, scope, seq_no, sha256, payload, signature)
                VALUES ($1,$2,$3,$4,$5,$6)
                ON CONFLICT DO NOTHING
            """, session_id, scope, seq_no, sha256,
                 json.dumps(payload, ensure_ascii=False), signature)

    async def receipts_for(self, session_id: str) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT payload, signature FROM purge_receipts
                 WHERE session_id = $1 ORDER BY COALESCE(seq_no, 0)
            """, session_id)
        return [
            {
                "payload": json.loads(row["payload"]) if isinstance(row["payload"], str)
                           else row["payload"],
                "signature": bytes(row["signature"]).hex(),
            }
            for row in rows
        ]

    # ============================================================
    # Audit and alerts
    # ============================================================

    async def audit(
        self, *, event_type: str, actor_type: str, actor_id: Optional[str] = None,
        device_id=None, session_id: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Append one hash-chained audit entry.

        Serialised by an advisory lock: without it two concurrent writers can read
        the same prev_hash and fork the chain, which silently destroys its value.
        """
        detail = detail or {}
        occurred_at = _utcnow()

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock($1)", AUDIT_LOCK_ID)
                prev = await conn.fetchval(
                    "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1")

                entry_hash = integrity.audit_entry_hash(
                    prev_hash=bytes(prev) if prev else None,
                    occurred_at=occurred_at,
                    event_type=event_type,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    session_id=session_id,
                    detail=detail,
                )

                await conn.execute("""
                    INSERT INTO audit_log
                        (occurred_at, event_type, actor_type, actor_id, device_id,
                         session_id, detail, prev_hash, entry_hash)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """, occurred_at, event_type, actor_type, actor_id, device_id,
                     session_id, json.dumps(detail, ensure_ascii=False),
                     prev, entry_hash)

    async def raise_alert(
        self, *, alert_type: str, severity: str = "warning",
        session_id: Optional[str] = None, device_id=None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO integrity_alerts
                    (session_id, device_id, alert_type, severity, detail)
                VALUES ($1,$2,$3,$4,$5)
            """, session_id, device_id, alert_type, severity,
                 json.dumps(detail or {}, ensure_ascii=False))
        logger.warning("Integrity alert [%s/%s] session=%s: %s",
                       severity, alert_type, session_id, detail)

    # ============================================================
    # Grants
    # ============================================================

    async def consume_grant(self, jti: str, expires_at: datetime,
                            session_id: Optional[str] = None) -> bool:
        """Record a grant as used. Returns False if it was already spent."""
        async with self._pool.acquire() as conn:
            try:
                await conn.execute("""
                    INSERT INTO used_grants (jti, session_id, expires_at)
                    VALUES ($1, $2, $3)
                """, jti, session_id, expires_at)
                return True
            except asyncpg.UniqueViolationError:
                return False


__all__ = ["V2Repository", "hash_token", "new_token"]
