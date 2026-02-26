"""
AIMScribe AI Backend - MinIO Client
Object storage for audio files with presigned URL support.
"""

import os
import logging
from datetime import timedelta
from typing import Optional, BinaryIO
from functools import lru_cache

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)


class MinIOClient:
    """
    MinIO client for audio file storage.

    Features:
    - Presigned upload URLs (for direct client upload)
    - Presigned download URLs
    - File upload/download
    - Bucket management
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
        external_endpoint: str = None
    ):
        """
        Initialize MinIO client.

        Args:
            endpoint: MinIO server endpoint (host:port)
            access_key: Access key (username)
            secret_key: Secret key (password)
            bucket: Default bucket name
            secure: Use HTTPS if True
            external_endpoint: External endpoint for presigned URLs (for clients outside Docker)
        """
        self.endpoint = endpoint
        self.external_endpoint = external_endpoint or endpoint
        self.bucket = bucket
        self.secure = secure

        # Internal client for operations (uploads, downloads, bucket checks)
        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure
        )

        # External client for presigned URLs only
        # Uses fixed region to avoid network calls to unreachable external endpoint
        if external_endpoint and external_endpoint != endpoint:
            self.presign_client = Minio(
                external_endpoint,
                access_key=access_key,
                secret_key=secret_key,
                secure=secure,
                region="us-east-1"  # Fixed region to skip server query
            )
        else:
            self.presign_client = self.client

        self._ensure_bucket_exists()
        logger.info(f"MinIO client initialized: {endpoint}/{bucket} (external: {self.external_endpoint})")
    
    def _ensure_bucket_exists(self):
        """Create bucket if it doesn't exist."""
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.info(f"Created bucket: {self.bucket}")
        except S3Error as e:
            logger.error(f"Failed to create bucket: {e}")
            raise
    
    def get_presigned_upload_url(
        self,
        object_name: str,
        expires: int = 300
    ) -> str:
        """
        Generate a presigned URL for uploading.

        Args:
            object_name: Object path (e.g., "audio/session123/clip_1.wav")
            expires: URL expiry in seconds (default: 5 minutes)

        Returns:
            Presigned URL for PUT request
        """
        try:
            # Use presign_client for external URLs (signature includes host)
            url = self.presign_client.presigned_put_object(
                self.bucket,
                object_name,
                expires=timedelta(seconds=expires)
            )
            logger.debug(f"Generated presigned upload URL for: {object_name}")
            return url
        except S3Error as e:
            logger.error(f"Failed to generate presigned upload URL: {e}")
            raise
    
    def get_presigned_download_url(
        self,
        object_name: str,
        expires: int = 3600
    ) -> str:
        """
        Generate a presigned URL for downloading.

        Args:
            object_name: Object path
            expires: URL expiry in seconds (default: 1 hour)

        Returns:
            Presigned URL for GET request
        """
        try:
            # Use presign_client for external URLs (signature includes host)
            url = self.presign_client.presigned_get_object(
                self.bucket,
                object_name,
                expires=timedelta(seconds=expires)
            )
            return url
        except S3Error as e:
            logger.error(f"Failed to generate presigned download URL: {e}")
            raise
    
    def upload_file(
        self,
        object_name: str,
        file_path: str,
        content_type: str = "audio/wav"
    ) -> bool:
        """
        Upload a file to MinIO.
        
        Args:
            object_name: Destination object path
            file_path: Local file path
            content_type: MIME type
            
        Returns:
            True if successful
        """
        try:
            self.client.fput_object(
                self.bucket,
                object_name,
                file_path,
                content_type=content_type
            )
            logger.info(f"Uploaded file: {object_name}")
            return True
        except S3Error as e:
            logger.error(f"Failed to upload file: {e}")
            raise
    
    def upload_data(
        self,
        object_name: str,
        data: BinaryIO,
        length: int,
        content_type: str = "audio/wav"
    ) -> bool:
        """
        Upload data stream to MinIO.
        
        Args:
            object_name: Destination object path
            data: File-like object
            length: Data length in bytes
            content_type: MIME type
            
        Returns:
            True if successful
        """
        try:
            self.client.put_object(
                self.bucket,
                object_name,
                data,
                length,
                content_type=content_type
            )
            logger.info(f"Uploaded data: {object_name}")
            return True
        except S3Error as e:
            logger.error(f"Failed to upload data: {e}")
            raise
    
    def download_file(
        self,
        object_name: str,
        file_path: str
    ) -> str:
        """
        Download a file from MinIO.
        
        Args:
            object_name: Source object path
            file_path: Local destination path
            
        Returns:
            Local file path
        """
        try:
            self.client.fget_object(
                self.bucket,
                object_name,
                file_path
            )
            logger.info(f"Downloaded file: {object_name} -> {file_path}")
            return file_path
        except S3Error as e:
            logger.error(f"Failed to download file: {e}")
            raise
    
    def delete_file(self, object_name: str) -> bool:
        """
        Delete a file from MinIO.
        
        Args:
            object_name: Object path to delete
            
        Returns:
            True if successful
        """
        try:
            self.client.remove_object(self.bucket, object_name)
            logger.info(f"Deleted file: {object_name}")
            return True
        except S3Error as e:
            logger.error(f"Failed to delete file: {e}")
            raise
    
    def file_exists(self, object_name: str) -> bool:
        """Check if a file exists in MinIO."""
        try:
            self.client.stat_object(self.bucket, object_name)
            return True
        except S3Error:
            return False
    
    def list_files(self, prefix: str = "") -> list:
        """
        List files in bucket with optional prefix.
        
        Args:
            prefix: Filter by prefix (e.g., "audio/session123/")
            
        Returns:
            List of object names
        """
        try:
            objects = self.client.list_objects(
                self.bucket,
                prefix=prefix,
                recursive=True
            )
            return [obj.object_name for obj in objects]
        except S3Error as e:
            logger.error(f"Failed to list files: {e}")
            raise
    
    def delete_session_files(self, session_id: str) -> int:
        """
        Delete all files for a session.
        
        Args:
            session_id: Session ID
            
        Returns:
            Number of files deleted
        """
        prefix = f"audio/{session_id}/"
        files = self.list_files(prefix)
        
        for file in files:
            self.delete_file(file)
        
        logger.info(f"Deleted {len(files)} files for session {session_id}")
        return len(files)
    
    @staticmethod
    def generate_object_key(session_id: str, clip_number: int) -> str:
        """
        Generate a standardized object key for an audio clip.
        
        Args:
            session_id: Session ID
            clip_number: Clip number
            
        Returns:
            Object key path
        """
        return f"audio/{session_id}/clip_{clip_number}.wav"


# Global instance
_minio_client: Optional[MinIOClient] = None


def get_minio_client() -> MinIOClient:
    """Get or create the MinIO client singleton."""
    global _minio_client

    if _minio_client is None:
        from config import settings

        _minio_client = MinIOClient(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            bucket=settings.minio_bucket,
            secure=settings.minio_secure,
            external_endpoint=settings.minio_external_endpoint
        )

    return _minio_client
