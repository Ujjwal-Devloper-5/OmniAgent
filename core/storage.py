"""
OmniAgent S3-Compatible Object Storage Subsystem
════════════════════════════════════════════════
Provides an enterprise storage abstraction (StorageBackend) and high-performance
Boto3-based S3/MinIO implementation (S3StorageBackend) with:
  - Dual-client architecture: internal Docker network data plane vs public host URL
    for SigV4-compliant pre-signed URLs without SignatureDoesNotMatch errors.
  - Fully asynchronous operation via asyncio.to_thread wrapping synchronous boto3 I/O.
  - Transparent support for paths, byte buffers, and stream file-likes.
  - Automatic MIME type inference and bucket auto-initialization.
  - Test mock injection and LocalStorageBackend fallback.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
import mimetypes
import os
from pathlib import Path
from typing import Any, BinaryIO, Optional, Union

import boto3
import botocore.client
import botocore.exceptions

from core.logger import get_logger

log = get_logger(__name__)


class StorageBackend(ABC):
    """
    Abstract base class defining the universal storage contract for OmniAgent.
    Enables pluggable backends (MinIO, AWS S3, Cloudflare R2, Local Disk, Mock).
    """

    @abstractmethod
    async def upload_file(
        self,
        file_path_or_bytes: Union[str, Path, bytes, bytearray, BinaryIO],
        destination_key: str,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Uploads a local file path, raw byte payload, or binary stream to storage.

        Args:
            file_path_or_bytes: Local file path, bytes, or file-like object.
            destination_key: Storage key (e.g., 'reports/quarterly.pdf').
            content_type: Optional MIME type; auto-detected if None.

        Returns:
            Canonical storage URI (e.g., 's3://omniagent/reports/quarterly.pdf').
        """
        pass

    @abstractmethod
    async def download_file(
        self,
        source_key: str,
        destination_path: Union[str, Path],
    ) -> str:
        """
        Downloads an object from storage to a local file destination.

        Args:
            source_key: Storage key to download.
            destination_path: Local filesystem destination path.

        Returns:
            String path of the downloaded file.

        Raises:
            FileNotFoundError: If the source_key does not exist in storage.
        """
        pass

    @abstractmethod
    async def generate_presigned_url(
        self,
        key: str,
        expires_in: int = 3600,
        method: str = "GET",
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Generates an externally accessible, pre-signed URL for client download or upload.

        Args:
            key: Storage object key.
            expires_in: Expiration lifetime in seconds (default: 3600).
            method: HTTP method ('GET' for download, 'PUT' for client upload).
            filename: Optional user-facing download filename (sets Content-Disposition).
            content_type: Optional Content-Type header for PUT uploads.

        Returns:
            Fully qualified HTTP pre-signed URL using the public endpoint.
        """
        pass

    @abstractmethod
    async def delete_file(self, key: str) -> bool:
        """
        Deletes an object by key.

        Args:
            key: Storage object key.

        Returns:
            True if deletion succeeded, False on failure.
        """
        pass

    @abstractmethod
    async def file_exists(self, key: str) -> bool:
        """
        Checks whether an object exists in storage.

        Args:
            key: Storage object key.

        Returns:
            True if object exists, False otherwise.
        """
        pass

    @abstractmethod
    async def ensure_bucket_exists(self) -> None:
        """
        Verifies that the target bucket exists, creating it if absent.
        """
        pass

    @abstractmethod
    async def cleanup_old_files(self, prefix: str = "", days: int = 7) -> int:
        """
        Deletes objects matching prefix older than specified retention days.

        Args:
            prefix: Key prefix to filter (e.g., 'reports/').
            days: Retention threshold in days.

        Returns:
            Number of objects deleted.
        """
        pass


class S3StorageBackend(StorageBackend):
    """
    Production-grade S3-compatible backend (MinIO / AWS S3 / Cloudflare R2).

    Features:
      - Dual Boto3 clients:
          * _internal_client: connects via internal Docker DNS (http://minio:9000)
            for high-throughput backend data plane operations.
          * _presign_client: binds to external host/domain (http://localhost:9000)
            for generating valid AWS SigV4 signatures matching browser Host headers.
      - Non-blocking async execution: all boto3 calls wrapped in asyncio.to_thread().
      - Path-style addressing configured for full MinIO and private S3 compatibility.
      - Pluggable mock backend support for isolated unit testing.
    """

    def __init__(
        self,
        bucket_name: Optional[str] = None,
        internal_endpoint: Optional[str] = None,
        public_endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: Optional[str] = None,
        mock_backend: Optional[Any] = None,
    ) -> None:
        try:
            from config import settings as app_settings
        except ImportError:
            app_settings = None

        self.bucket: str = (
            bucket_name
            or os.environ.get("MINIO_BUCKET")
            or os.environ.get("S3_BUCKET_NAME")
            or getattr(app_settings, "s3_bucket_name", None)
            or "omniagent"
        )

        self.internal_endpoint: str = (
            internal_endpoint
            or os.environ.get("MINIO_INTERNAL_ENDPOINT")
            or os.environ.get("S3_ENDPOINT_URL")
            or getattr(app_settings, "s3_endpoint_url", None)
            or "http://minio:9000"
        ).rstrip("/")

        self.public_endpoint: str = (
            public_endpoint
            or os.environ.get("MINIO_PUBLIC_ENDPOINT")
            or os.environ.get("S3_PUBLIC_ENDPOINT_URL")
            or getattr(app_settings, "s3_public_endpoint_url", None)
            or "http://localhost:9000"
        ).rstrip("/")

        self.access_key: str = (
            access_key
            or os.environ.get("MINIO_ROOT_USER")
            or os.environ.get("AWS_ACCESS_KEY_ID")
            or os.environ.get("S3_ACCESS_KEY")
            or getattr(app_settings, "s3_access_key", None)
            or "minioadmin"
        )

        self.secret_key: str = (
            secret_key
            or os.environ.get("MINIO_ROOT_PASSWORD")
            or os.environ.get("AWS_SECRET_ACCESS_KEY")
            or os.environ.get("S3_SECRET_KEY")
            or getattr(app_settings, "s3_secret_key", None)
            or "minioadmin"
        )

        self.region: str = (
            region
            or os.environ.get("AWS_REGION")
            or os.environ.get("S3_REGION_NAME")
            or getattr(app_settings, "s3_region_name", None)
            or "us-east-1"
        )

        self._mock = mock_backend

        if not self._mock:
            session = boto3.Session(
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
            )
            boto_config = botocore.client.Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
            )
            self._internal_client = session.client(
                "s3",
                endpoint_url=self.internal_endpoint,
                config=boto_config,
            )
            self._presign_client = session.client(
                "s3",
                endpoint_url=self.public_endpoint,
                config=boto_config,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Public Asynchronous Interface (Non-blocking)
    # ─────────────────────────────────────────────────────────────────────────

    async def upload_file(
        self,
        file_path_or_bytes: Union[str, Path, bytes, bytearray, BinaryIO],
        destination_key: str,
        content_type: Optional[str] = None,
    ) -> str:
        if self._mock:
            return await self._mock.upload_file(
                file_path_or_bytes, destination_key, content_type
            )
        return await asyncio.to_thread(
            self._sync_upload_file, file_path_or_bytes, destination_key, content_type
        )

    async def download_file(
        self,
        source_key: str,
        destination_path: Union[str, Path],
    ) -> str:
        if self._mock:
            res = await self._mock.download_file(source_key, destination_path)
            return str(res)
        return await asyncio.to_thread(
            self._sync_download_file, source_key, destination_path
        )

    async def generate_presigned_url(
        self,
        key: str,
        expires_in: int = 3600,
        method: str = "GET",
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        if self._mock:
            return await self._mock.generate_presigned_url(
                key,
                expires_in=expires_in,
                http_method=method,
                filename=filename,
                content_type=content_type,
            )
        return await asyncio.to_thread(
            self._sync_generate_presigned_url,
            key,
            expires_in,
            method,
            filename,
            content_type,
        )

    async def delete_file(self, key: str) -> bool:
        if self._mock:
            return await self._mock.delete_file(key)
        return await asyncio.to_thread(self._sync_delete_file, key)

    async def file_exists(self, key: str) -> bool:
        if self._mock:
            return await self._mock.file_exists(key)
        return await asyncio.to_thread(self._sync_file_exists, key)

    async def ensure_bucket_exists(self) -> None:
        if self._mock:
            return
        await asyncio.to_thread(self._sync_ensure_bucket_exists)

    async def cleanup_old_files(self, prefix: str = "", days: int = 7) -> int:
        if self._mock:
            if hasattr(self._mock, "cleanup_old_files"):
                return await self._mock.cleanup_old_files(prefix=prefix, days=days)
            return 0
        return await asyncio.to_thread(self._sync_cleanup_old_files, prefix, days)

    # ─────────────────────────────────────────────────────────────────────────
    # Synchronous Worker Methods (Offloaded to Worker Thread Pool)
    # ─────────────────────────────────────────────────────────────────────────

    def _sync_upload_file(
        self,
        source: Union[str, Path, bytes, bytearray, BinaryIO],
        destination_key: str,
        content_type: Optional[str] = None,
    ) -> str:
        if not content_type:
            mime, _ = mimetypes.guess_type(destination_key)
            content_type = mime or "application/octet-stream"

        extra_args: dict[str, Any] = {"ContentType": content_type}

        if isinstance(source, (str, Path)):
            src_str = str(source)
            if not os.path.exists(src_str):
                raise FileNotFoundError(f"Local file not found: {src_str}")
            self._internal_client.upload_file(
                Filename=src_str,
                Bucket=self.bucket,
                Key=destination_key,
                ExtraArgs=extra_args,
            )
        elif isinstance(source, (bytes, bytearray)):
            self._internal_client.put_object(
                Bucket=self.bucket,
                Key=destination_key,
                Body=bytes(source),
                **extra_args,
            )
        elif hasattr(source, "read"):
            self._internal_client.upload_fileobj(
                Fileobj=source,
                Bucket=self.bucket,
                Key=destination_key,
                ExtraArgs=extra_args,
            )
        else:
            raise TypeError(f"Unsupported source type for upload_file: {type(source)}")

        return f"s3://{self.bucket}/{destination_key}"

    def _sync_download_file(
        self,
        source_key: str,
        destination_path: Union[str, Path],
    ) -> str:
        dest = Path(destination_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._internal_client.download_file(
                Bucket=self.bucket,
                Key=source_key,
                Filename=str(dest),
            )
            return str(dest)
        except botocore.exceptions.ClientError as exc:
            err_code = str(exc.response.get("Error", {}).get("Code", ""))
            if err_code in ("404", "NoSuchKey"):
                raise FileNotFoundError(
                    f"Key '{source_key}' not found in bucket '{self.bucket}'"
                ) from exc
            raise

    def _sync_generate_presigned_url(
        self,
        key: str,
        expires_in: int = 3600,
        method: str = "GET",
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        method_upper = method.upper()
        params: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
        }

        if method_upper == "GET":
            client_method = "get_object"
            if filename:
                params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
            if content_type:
                params["ResponseContentType"] = content_type
        elif method_upper == "PUT":
            client_method = "put_object"
            if content_type:
                params["ContentType"] = content_type
        else:
            raise ValueError(
                f"Unsupported presigned URL method '{method}'. Supported methods: 'GET', 'PUT'."
            )

        return self._presign_client.generate_presigned_url(
            ClientMethod=client_method,
            Params=params,
            ExpiresIn=expires_in,
            HttpMethod=method_upper,
        )

    def _sync_delete_file(self, key: str) -> bool:
        try:
            # Check existence first if desired, or directly delete
            self._internal_client.delete_object(Bucket=self.bucket, Key=key)
            return True
        except botocore.exceptions.ClientError as exc:
            log.warning("Failed to delete object '%s' in bucket '%s': %s", key, self.bucket, exc)
            return False

    def _sync_file_exists(self, key: str) -> bool:
        try:
            self._internal_client.head_object(Bucket=self.bucket, Key=key)
            return True
        except botocore.exceptions.ClientError as exc:
            err_code = str(exc.response.get("Error", {}).get("Code", ""))
            if err_code in ("404", "NoSuchKey"):
                return False
            log.warning("Error checking object existence for '%s': %s", key, exc)
            return False

    def _sync_ensure_bucket_exists(self) -> None:
        try:
            self._internal_client.head_bucket(Bucket=self.bucket)
        except botocore.exceptions.ClientError as exc:
            err_code = str(exc.response.get("Error", {}).get("Code", ""))
            if err_code in ("404", "NoSuchBucket"):
                log.info("S3 bucket '%s' does not exist. Creating...", self.bucket)
                try:
                    if self.region == "us-east-1":
                        self._internal_client.create_bucket(Bucket=self.bucket)
                    else:
                        self._internal_client.create_bucket(
                            Bucket=self.bucket,
                            CreateBucketConfiguration={"LocationConstraint": self.region},
                        )
                    log.info("S3 bucket '%s' created successfully.", self.bucket)
                except botocore.exceptions.ClientError as create_err:
                    c_code = str(create_err.response.get("Error", {}).get("Code", ""))
                    if c_code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                        log.error("Failed to auto-create bucket '%s': %s", self.bucket, create_err)
            else:
                log.warning("Unable to verify bucket '%s': %s", self.bucket, exc)

    def _sync_cleanup_old_files(self, prefix: str = "", days: int = 7) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        paginator = self._internal_client.get_paginator("list_objects_v2")
        deleted_count = 0
        to_delete: list[dict[str, str]] = []

        try:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    last_modified = obj.get("LastModified")
                    if last_modified and last_modified < cutoff:
                        to_delete.append({"Key": obj["Key"]})
                        if len(to_delete) >= 1000:
                            self._internal_client.delete_objects(
                                Bucket=self.bucket,
                                Delete={"Objects": to_delete},
                            )
                            deleted_count += len(to_delete)
                            to_delete = []

            if to_delete:
                self._internal_client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": to_delete},
                )
                deleted_count += len(to_delete)
        except botocore.exceptions.ClientError as exc:
            log.error("Error during S3 cleanup_old_files for prefix '%s': %s", prefix, exc)

        return deleted_count


# ─────────────────────────────────────────────────────────────────────────────
# Local Filesystem Backend (Development / Fallback)
# ─────────────────────────────────────────────────────────────────────────────

class LocalStorageBackend(StorageBackend):
    """
    Local filesystem fallback storage backend for offline or non-Docker environments.
    """

    def __init__(self, base_dir: Union[str, Path] = "/app/data/storage") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def upload_file(
        self,
        file_path_or_bytes: Union[str, Path, bytes, bytearray, BinaryIO],
        destination_key: str,
        content_type: Optional[str] = None,
    ) -> str:
        dest = self.base_dir / destination_key
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(file_path_or_bytes, (str, Path)):
            import shutil
            await asyncio.to_thread(shutil.copy2, str(file_path_or_bytes), str(dest))
        elif isinstance(file_path_or_bytes, (bytes, bytearray)):
            await asyncio.to_thread(dest.write_bytes, bytes(file_path_or_bytes))
        elif hasattr(file_path_or_bytes, "read"):
            data = file_path_or_bytes.read()
            if isinstance(data, str):
                data = data.encode("utf-8")
            await asyncio.to_thread(dest.write_bytes, data)
        return f"file://{dest.absolute()}"

    async def download_file(
        self,
        source_key: str,
        destination_path: Union[str, Path],
    ) -> str:
        src = self.base_dir / source_key
        if not src.exists():
            raise FileNotFoundError(f"Local storage key '{source_key}' not found.")
        dest = Path(destination_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        await asyncio.to_thread(shutil.copy2, str(src), str(dest))
        return str(dest)

    async def generate_presigned_url(
        self,
        key: str,
        expires_in: int = 3600,
        method: str = "GET",
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        target = self.base_dir / key
        return f"file://{target.absolute()}"

    async def delete_file(self, key: str) -> bool:
        src = self.base_dir / key
        if src.exists():
            try:
                src.unlink()
                return True
            except OSError:
                return False
        return False

    async def file_exists(self, key: str) -> bool:
        return (self.base_dir / key).exists()

    async def ensure_bucket_exists(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def cleanup_old_files(self, prefix: str = "", days: int = 7) -> int:
        cutoff = datetime.now().timestamp() - (days * 86400)
        target_dir = self.base_dir / prefix if prefix else self.base_dir
        deleted = 0
        if not target_dir.exists():
            return 0
        for f in target_dir.rglob("*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                try:
                    f.unlink()
                    deleted += 1
                except OSError:
                    pass
        return deleted


# ─────────────────────────────────────────────────────────────────────────────
# Global Factory / Singleton Accessors
# ─────────────────────────────────────────────────────────────────────────────

_storage_backend: Optional[StorageBackend] = None


def get_storage_backend() -> StorageBackend:
    """
    Returns the global StorageBackend singleton instance, lazily initialized.
    """
    global _storage_backend
    if _storage_backend is None:
        _storage_backend = S3StorageBackend()
    return _storage_backend


def get_storage() -> StorageBackend:
    """
    Alias for get_storage_backend() to provide uniform access across all tools.
    """
    return get_storage_backend()


def set_storage_backend(backend: Optional[StorageBackend]) -> None:
    """
    Explicitly overrides the global StorageBackend singleton (useful for testing).
    """
    global _storage_backend
    _storage_backend = backend
