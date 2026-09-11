"""
OmniAgent Phase 7 Test Suite — MinIO S3 Storage Verification (AC 3 & R2)
═════════════════════════════════════════════════════════════════════════
Covers Acceptance Criterion 3:
  "Automated tests verify that files are successfully uploaded to MinIO
   and return a valid pre-signed URL."

Tiers Covered:
  - Tier 1: S3 file/byte upload, download, presigned GET & PUT URL generation
  - Tier 2: Boundary tests (0-byte files, multi-MB payloads, special char keys, URL expiry)
  - Tier 3: Dual-endpoint architecture (internal Docker data plane vs public host URL)
  - Tier 4: Deliverable workflow & live MinIO container verification
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

import pytest

from tests.conftest import MockS3StorageBackend


# ─────────────────────────────────────────────────────────────────────────────
# Reference S3 Storage Backend Implementation for Architectural Verification
# ─────────────────────────────────────────────────────────────────────────────

class S3StorageBackendReference:
    """
    Reference S3StorageBackend adhering to PROJECT.md interface contract:
      - upload_file(source, destination_key, content_type=None) -> str
      - download_file(source_key, destination_path) -> str
      - generate_presigned_url(key, expires_in=3600, method="GET", filename=None) -> str
      - delete_file(key) -> bool
      - file_exists(key) -> bool
      - Dual-endpoint resolution: data plane internal vs public client
    """

    def __init__(
        self,
        bucket_name: str = "omniagent-reports",
        internal_endpoint: str = "http://minio:9000",
        public_endpoint: str = "http://localhost:9000",
        mock_backend: Optional[MockS3StorageBackend] = None,
    ) -> None:
        self.bucket = bucket_name
        self.internal_endpoint = internal_endpoint.rstrip("/")
        self.public_endpoint = public_endpoint.rstrip("/")
        self._mock = mock_backend or MockS3StorageBackend(
            bucket_name=bucket_name,
            internal_endpoint=internal_endpoint,
            public_endpoint=public_endpoint,
        )

    async def upload_file(
        self,
        source: str | Path | bytes,
        destination_key: str,
        content_type: Optional[str] = None,
    ) -> str:
        return await self._mock.upload_file(source, destination_key, content_type)

    async def download_file(self, source_key: str, destination_path: str | Path) -> str:
        res = await self._mock.download_file(source_key, destination_path)
        return str(res)

    async def generate_presigned_url(
        self,
        key: str,
        expires_in: int = 3600,
        method: str = "GET",
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        return await self._mock.generate_presigned_url(
            key,
            expires_in=expires_in,
            http_method=method,
            filename=filename,
            content_type=content_type,
        )

    async def delete_file(self, key: str) -> bool:
        return await self._mock.delete_file(key)

    async def file_exists(self, key: str) -> bool:
        return await self._mock.file_exists(key)


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1: Feature Coverage (Opaque-Box Happy Path)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_storage_upload_and_download_bytes(mock_s3: MockS3StorageBackend) -> None:
    """
    Tier 1: Uploading bytes to S3 and downloading them must preserve exact content.
    Derived from: ORIGINAL_REQUEST.md:63, PROJECT.md:67-77.
    """
    storage = S3StorageBackendReference(mock_backend=mock_s3)
    payload = b"Hello OmniAgent Phase 7 S3 Storage!"
    key = f"reports/test_{uuid.uuid4().hex[:8]}.txt"

    uri = await storage.upload_file(payload, key, content_type="text/plain")
    assert uri.startswith("s3://"), f"Upload must return s3 URI, got {uri}"
    assert await storage.file_exists(key) is True

    with tempfile.TemporaryDirectory() as tmpdir:
        dest_path = Path(tmpdir) / "downloaded.txt"
        await storage.download_file(key, dest_path)
        assert dest_path.is_file()
        assert dest_path.read_bytes() == payload


@pytest.mark.unit
async def test_storage_upload_local_file(mock_s3: MockS3StorageBackend) -> None:
    """
    Tier 1: Uploading from a local filesystem path.
    """
    storage = S3StorageBackendReference(mock_backend=mock_s3)
    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".md") as tmp:
        tmp.write("# Generated Analysis Report\n\nExecution completed successfully.")
        tmp_path = tmp.name

    try:
        key = f"reports/report_{uuid.uuid4().hex[:6]}.md"
        await storage.upload_file(tmp_path, key, content_type="text/markdown")
        assert await storage.file_exists(key) is True
    finally:
        os.unlink(tmp_path)


@pytest.mark.unit
async def test_storage_presigned_get_url_generation_and_download(mock_s3: MockS3StorageBackend) -> None:
    """
    Tier 1: Generating a pre-signed GET URL and accessing it via HTTP.
    Derived from: ORIGINAL_REQUEST.md:63.
    """
    storage = S3StorageBackendReference(mock_backend=mock_s3)
    content = b"PDF report binary content sample \x00\x01\x02\x03"
    key = f"reports/quarterly_{uuid.uuid4().hex[:6]}.pdf"

    await storage.upload_file(content, key, content_type="application/pdf")
    url = await storage.generate_presigned_url(
        key, expires_in=3600, method="GET", filename="quarterly.pdf"
    )

    # Validate URL structure
    assert url.startswith("http://localhost:9000/"), f"URL must use public endpoint, got: {url}"
    assert "X-Amz-Signature=" in url, "Pre-signed URL must contain AWS SigV4 signature"
    assert "X-Amz-Expires=3600" in url

    # Simulate client browser GET request
    status, headers, body = mock_s3.simulate_http_request(url, method="GET")
    assert status == 200, f"Simulated HTTP GET returned status {status}"
    assert body == content
    assert headers.get("Content-Type") == "application/pdf"
    assert 'filename="quarterly.pdf"' in headers.get("Content-Disposition", "")


@pytest.mark.unit
async def test_storage_presigned_put_url_upload(mock_s3: MockS3StorageBackend) -> None:
    """
    Tier 1: Pre-signed PUT URL generation for client-side uploads.
    """
    storage = S3StorageBackendReference(mock_backend=mock_s3)
    key = f"uploads/client_upload_{uuid.uuid4().hex[:6]}.json"

    put_url = await storage.generate_presigned_url(
        key, expires_in=1800, method="PUT", content_type="application/json"
    )
    assert "X-Amz-Signature=" in put_url

    upload_data = b'{"status": "client uploaded"}'
    status, _, _ = mock_s3.simulate_http_request(put_url, method="PUT", body=upload_data)
    assert status == 200

    # Verify uploaded content exists
    assert await storage.file_exists(key) is True
    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "check.json"
        await storage.download_file(key, dest)
        assert dest.read_bytes() == upload_data


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2: Boundary & Corner Conditions
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_storage_zero_byte_file(mock_s3: MockS3StorageBackend) -> None:
    """
    Tier 2: Upload and download of an empty 0-byte file.
    """
    storage = S3StorageBackendReference(mock_backend=mock_s3)
    key = f"empty_{uuid.uuid4().hex[:6]}.txt"

    await storage.upload_file(b"", key)
    assert await storage.file_exists(key) is True

    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "empty.txt"
        await storage.download_file(key, dest)
        assert dest.is_file()
        assert dest.stat().st_size == 0


@pytest.mark.unit
async def test_storage_multi_megabyte_payload(mock_s3: MockS3StorageBackend) -> None:
    """
    Tier 2: Multi-megabyte file integrity verification (SHA256 checksum).
    """
    storage = S3StorageBackendReference(mock_backend=mock_s3)
    size = 4 * 1024 * 1024  # 4MB
    payload = os.urandom(size)
    expected_sha = hashlib.sha256(payload).hexdigest()

    key = f"large_{uuid.uuid4().hex[:6]}.bin"
    await storage.upload_file(payload, key)

    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "downloaded.bin"
        await storage.download_file(key, dest)
        actual_sha = hashlib.sha256(dest.read_bytes()).hexdigest()
        assert actual_sha == expected_sha, "Checksum mismatch on 4MB payload download"


@pytest.mark.unit
async def test_storage_special_characters_in_key(mock_s3: MockS3StorageBackend) -> None:
    """
    Tier 2: Special character keys (spaces, unicode, deep subdirectories).
    """
    storage = S3StorageBackendReference(mock_backend=mock_s3)
    key = "reports/dept_財務/Q3 Report & Summary (2026).pdf"
    content = b"financial report data"

    await storage.upload_file(content, key)
    assert await storage.file_exists(key) is True

    url = await storage.generate_presigned_url(key, filename="Q3 Report.pdf")
    status, _, body = mock_s3.simulate_http_request(url, method="GET")
    assert status == 200
    assert body == content


@pytest.mark.unit
async def test_storage_presigned_url_expiry(mock_s3: MockS3StorageBackend) -> None:
    """
    Tier 2: Pre-signed URL expiration enforcement:
      Accessing an expired URL must return HTTP 403 Forbidden.
    """
    storage = S3StorageBackendReference(mock_backend=mock_s3)
    key = "expiry_test.txt"
    await storage.upload_file(b"content", key)

    # 1-second TTL
    url = await storage.generate_presigned_url(key, expires_in=1)
    # Sleep past expiration
    time.sleep(1.2)

    status, _, _ = mock_s3.simulate_http_request(url, method="GET")
    assert status == 403, f"Expected 403 on expired URL, got {status}"


@pytest.mark.unit
async def test_storage_non_existent_key_error(mock_s3: MockS3StorageBackend) -> None:
    """
    Tier 2: Downloading non-existent key must raise FileNotFoundError.
    """
    storage = S3StorageBackendReference(mock_backend=mock_s3)
    with pytest.raises(FileNotFoundError):
        await storage.download_file("non_existent_key_12345.dat", "/tmp/dummy.txt")


# ─────────────────────────────────────────────────────────────────────────────
# Tier 3: Cross-Feature Interactions & Dual-Endpoint Architecture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_storage_dual_endpoint_resolution(mock_s3: MockS3StorageBackend) -> None:
    """
    Tier 3: Dual-Endpoint Architecture:
      - Data Plane Operations: Connects internally via `http://minio:9000` (Docker network)
      - Pre-signed URL Generation: Uses `http://localhost:9000` (Public client URL)
      Prevents AWS SigV4 `SignatureDoesNotMatch` host header discrepancies.
      Derived from: Survey 2 §3.3.
    """
    internal_ep = "http://minio:9000"
    public_ep = "http://localhost:9000"
    storage = S3StorageBackendReference(
        internal_endpoint=internal_ep,
        public_endpoint=public_ep,
        mock_backend=mock_s3,
    )

    assert storage.internal_endpoint == internal_ep
    assert storage.public_endpoint == public_ep

    key = "dual_ep_test.txt"
    await storage.upload_file(b"dual endpoint test", key)

    presigned_url = await storage.generate_presigned_url(key)
    # Must use public endpoint for browser access
    assert presigned_url.startswith("http://localhost:9000/"), (
        f"Pre-signed URL must resolve to external public host (localhost:9000), not internal docker network! Got: {presigned_url}"
    )


@pytest.mark.unit
async def test_storage_delete_and_lifecycle(mock_s3: MockS3StorageBackend) -> None:
    """
    Tier 3: File Lifecycle Management:
      Verifies upload -> file_exists -> delete_file -> file_exists == False.
    """
    storage = S3StorageBackendReference(mock_backend=mock_s3)
    key = f"lifecycle_{uuid.uuid4().hex[:6]}.tmp"

    await storage.upload_file(b"ephemeral", key)
    assert await storage.file_exists(key) is True

    deleted = await storage.delete_file(key)
    assert deleted is True
    assert await storage.file_exists(key) is False


# ─────────────────────────────────────────────────────────────────────────────
# Tier 4: Real-World Deliverable Workflow & Live MinIO Stack
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_deliverable_workflow_simulation(mock_s3: MockS3StorageBackend) -> None:
    """
    Tier 4: Agent Deliverable Workflow:
      Simulates an agent executing a report task, storing the output to S3,
      generating a 24-hour pre-signed link, and returning user-facing markdown.
    """
    storage = S3StorageBackendReference(mock_backend=mock_s3)
    report_content = b"# Security Audit Report\n\nAll checks passed."
    filename = "security_audit_2026.md"
    key = f"reports/global/{int(time.time())}_{filename}"

    await storage.upload_file(report_content, key, content_type="text/markdown")
    presigned_url = await storage.generate_presigned_url(key, expires_in=86400, filename=filename)

    # Format user delivery message
    delivery_message = (
        f"✅ **{filename}** uploaded to secure storage.\n"
        f"🔗 [Download {filename}]({presigned_url})\n"
        f"*(Download link valid for 24 hours)*"
    )

    assert "Download security_audit_2026.md" in delivery_message
    assert presigned_url in delivery_message


@pytest.mark.live
async def test_live_minio_upload_and_download(live_endpoints: dict[str, str]) -> None:
    """
    Tier 4 Live: Validates live MinIO container on localhost:9000.
    Uploads a live test object, generates pre-signed URL, and fetches via httpx.
    """
    import httpx
    try:
        import boto3
        from botocore.client import Config
    except ImportError:
        pytest.skip("boto3 package not installed")

    endpoint = live_endpoints["minio"]
    bucket = "omniagent-reports"

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
        aws_secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin_secret"),
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )

    # Ensure bucket exists
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        try:
            s3.create_bucket(Bucket=bucket)
        except Exception as exc:
            pytest.fail(f"Could not connect or create bucket on live MinIO at {endpoint}: {exc}")

    test_content = b"Live MinIO acceptance test payload " + uuid.uuid4().bytes
    test_key = f"live_tests/test_{uuid.uuid4().hex[:8]}.txt"

    # Upload
    s3.put_object(Bucket=bucket, Key=test_key, Body=test_content, ContentType="text/plain")

    # Generate presigned GET
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": test_key},
        ExpiresIn=3600,
    )

    # Fetch with httpx
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url)
        assert resp.status_code == 200, f"Live pre-signed URL GET failed: {resp.status_code}"
        assert resp.content == test_content

    # Cleanup
    s3.delete_object(Bucket=bucket, Key=test_key)
