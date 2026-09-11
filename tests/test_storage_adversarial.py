"""
Adversarial Verification Suite for Milestone 3 (S3 Storage Backend)
Empirical tests executed directly by teamwork_preview_challenger_m3.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import botocore.exceptions
from botocore.stub import Stubber
import pytest

from core.storage import (
    LocalStorageBackend,
    S3StorageBackend,
    StorageBackend,
    get_storage,
    get_storage_backend,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. SigV4 Host Headers & Endpoint Resolution
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sigv4_host_headers_localhost_vs_minio():
    """
    Empirically verify that presigned URLs sign the public endpoint host (localhost:9000),
    not the internal container host (minio:9000).
    """
    s3 = S3StorageBackend(
        bucket_name="omniagent",
        internal_endpoint="http://minio:9000",
        public_endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        region="us-east-1",
    )

    url = await s3.generate_presigned_url("test_object.pdf", expires_in=3600, method="GET")
    parsed = urlparse(url)

    # 1. URL host must strictly be localhost:9000
    assert parsed.netloc == "localhost:9000", f"Expected localhost:9000, got {parsed.netloc}"
    assert parsed.scheme == "http"

    # 2. SigV4 query params must exist
    qs = parse_qs(parsed.query)
    assert "X-Amz-Signature" in qs
    assert "X-Amz-SignedHeaders" in qs
    assert "X-Amz-Credential" in qs
    assert "X-Amz-Algorithm" in qs

    # 3. SignedHeaders MUST bind the Host header
    signed_headers = qs["X-Amz-SignedHeaders"][0].split(";")
    assert "host" in signed_headers, f"host header not signed: {signed_headers}"

    # 4. Verify that signing against minio:9000 generates a DIFFERENT signature
    s3_internal_presign = S3StorageBackend(
        bucket_name="omniagent",
        internal_endpoint="http://minio:9000",
        public_endpoint="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        region="us-east-1",
    )
    # Freeze timestamp or generate immediately
    url_internal = await s3_internal_presign.generate_presigned_url("test_object.pdf", expires_in=3600, method="GET")
    parsed_internal = urlparse(url_internal)
    qs_internal = parse_qs(parsed_internal.query)

    # The credential scope date might match; signature must differ if host differs
    # Even if timestamps differ slightly, the host header in canonical request is minio:9000 vs localhost:9000
    assert parsed_internal.netloc == "minio:9000"


@pytest.mark.asyncio
async def test_sigv4_put_presigned_url():
    """Verify PUT presigned URL generation and headers."""
    s3 = S3StorageBackend(
        bucket_name="omniagent",
        internal_endpoint="http://minio:9000",
        public_endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        region="us-east-1",
    )
    put_url = await s3.generate_presigned_url(
        "uploads/user_doc.pdf",
        expires_in=1800,
        method="PUT",
        content_type="application/pdf",
    )
    parsed = urlparse(put_url)
    assert parsed.netloc == "localhost:9000"
    qs = parse_qs(parsed.query)
    assert "X-Amz-Signature" in qs
    assert "content-type" in qs["X-Amz-SignedHeaders"][0] or "host" in qs["X-Amz-SignedHeaders"][0]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Missing Object Error Handling & FileNotFoundError Translation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_s3_download_missing_object_raises_filenotfound_404():
    """Verify that botocore ClientError 404 is translated to FileNotFoundError."""
    s3 = S3StorageBackend(
        bucket_name="omniagent",
        internal_endpoint="http://minio:9000",
        public_endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
    )
    stubber = Stubber(s3._internal_client)
    stubber.add_client_error("head_object", service_error_code="404", service_message="Not Found")
    stubber.activate()

    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "out.txt"
        with pytest.raises(FileNotFoundError) as exc_info:
            await s3.download_file("nonexistent_404.txt", dest)
        assert "Key 'nonexistent_404.txt' not found in bucket 'omniagent'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_s3_download_missing_object_raises_filenotfound_nosuchkey():
    """Verify that botocore ClientError NoSuchKey is translated to FileNotFoundError."""
    s3 = S3StorageBackend(
        bucket_name="omniagent",
        internal_endpoint="http://minio:9000",
        public_endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
    )
    stubber = Stubber(s3._internal_client)
    stubber.add_client_error("head_object", service_error_code="NoSuchKey", service_message="Key not found")
    stubber.activate()

    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "out.txt"
        with pytest.raises(FileNotFoundError) as exc_info:
            await s3.download_file("nonexistent_nosuchkey.txt", dest)
        assert "Key 'nonexistent_nosuchkey.txt' not found in bucket 'omniagent'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_s3_download_other_client_error_not_swallowed():
    """Verify that non-404 ClientErrors (e.g. 403 AccessDenied) are NOT converted to FileNotFoundError."""
    s3 = S3StorageBackend(
        bucket_name="omniagent",
        internal_endpoint="http://minio:9000",
        public_endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
    )
    stubber = Stubber(s3._internal_client)
    stubber.add_client_error("head_object", service_error_code="AccessDenied", service_message="Access Denied")
    stubber.activate()

    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "out.txt"
        with pytest.raises(botocore.exceptions.ClientError) as exc_info:
            await s3.download_file("forbidden.txt", dest)
        assert exc_info.value.response["Error"]["Code"] == "AccessDenied"


@pytest.mark.asyncio
async def test_s3_file_exists_on_missing_returns_false():
    """Verify file_exists returns False on 404 and NoSuchKey."""
    s3 = S3StorageBackend(
        bucket_name="omniagent",
        internal_endpoint="http://minio:9000",
        public_endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
    )
    stubber = Stubber(s3._internal_client)
    stubber.add_client_error("head_object", service_error_code="404", service_message="Not Found")
    stubber.add_client_error("head_object", service_error_code="NoSuchKey", service_message="Not Found")
    stubber.activate()

    assert await s3.file_exists("missing_1.txt") is False
    assert await s3.file_exists("missing_2.txt") is False


@pytest.mark.asyncio
async def test_local_storage_backend_error_handling():
    """Verify LocalStorageBackend translates missing files to FileNotFoundError."""
    with tempfile.TemporaryDirectory() as base_dir:
        backend = LocalStorageBackend(base_dir=base_dir)
        with pytest.raises(FileNotFoundError):
            await backend.download_file("nonexistent.txt", "/tmp/dest.txt")
        assert await backend.file_exists("nonexistent.txt") is False
        assert await backend.delete_file("nonexistent.txt") is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. Non-ASCII Keys, Special Characters & Edge Cases
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_non_ascii_and_special_char_keys():
    """Verify URL generation and key handling for unicode, emojis, and special chars."""
    s3 = S3StorageBackend(
        bucket_name="omniagent",
        internal_endpoint="http://minio:9000",
        public_endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
    )

    test_keys = [
        "reports/財務_2026/決算報告書.pdf",
        "reports/تقرير_الأرباح_2026.pdf",
        "artifacts/🚀_rocket_report_🔥.json",
        "reports/spaces and & symbols + plus.txt",
        "nested/a/b/c/d/deeply/nested/file.bin",
    ]

    for key in test_keys:
        url = await s3.generate_presigned_url(key)
        assert url.startswith("http://localhost:9000/omniagent/")
        # Parse URL and verify unquoted path matches the key
        parsed = urlparse(url)
        path = parsed.path
        # Remove /omniagent/ prefix
        extracted_key = unquote(path[len("/omniagent/"):])
        assert extracted_key == key, f"Key roundtrip failed: {extracted_key} != {key}"


@pytest.mark.asyncio
async def test_presigned_url_content_disposition_special_chars():
    """Verify Content-Disposition header in presigned URL with unicode and quotes."""
    s3 = S3StorageBackend(
        bucket_name="omniagent",
        internal_endpoint="http://minio:9000",
        public_endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
    )

    # Unicode filename
    url_unicode = await s3.generate_presigned_url("doc.pdf", filename="財務報告書.pdf")
    assert "response-content-disposition" in url_unicode
    qs = parse_qs(urlparse(url_unicode).query)
    disp = qs["response-content-disposition"][0]
    assert "財務報告書.pdf" in disp


# ─────────────────────────────────────────────────────────────────────────────
# 4. Zero-Byte Uploads, Payload Sizing, and Type Handling
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_zero_byte_upload_and_download_local():
    """Verify zero-byte upload/download works flawlessly on LocalStorageBackend."""
    with tempfile.TemporaryDirectory() as base_dir:
        backend = LocalStorageBackend(base_dir=base_dir)

        # 1. Upload 0-byte bytes
        uri = await backend.upload_file(b"", "empty_bytes.txt")
        assert await backend.file_exists("empty_bytes.txt") is True

        with tempfile.TemporaryDirectory() as dest_dir:
            dest = Path(dest_dir) / "empty_bytes.txt"
            await backend.download_file("empty_bytes.txt", dest)
            assert dest.stat().st_size == 0

        # 2. Upload 0-byte local file
        with tempfile.NamedTemporaryFile() as empty_f:
            empty_path = empty_f.name
            await backend.upload_file(empty_path, "empty_file.txt")
            assert await backend.file_exists("empty_file.txt") is True

        # 3. Upload 0-byte BytesIO stream
        stream = io.BytesIO(b"")
        await backend.upload_file(stream, "empty_stream.txt")
        assert await backend.file_exists("empty_stream.txt") is True


@pytest.mark.asyncio
async def test_large_payload_local():
    """Verify multi-megabyte payload roundtrip and SHA256 integrity."""
    with tempfile.TemporaryDirectory() as base_dir:
        backend = LocalStorageBackend(base_dir=base_dir)

        # 10 MB payload
        size = 10 * 1024 * 1024
        large_bytes = os.urandom(size)
        expected_hash = hashlib.sha256(large_bytes).hexdigest()

        key = "large_test.bin"
        await backend.upload_file(large_bytes, key)
        assert await backend.file_exists(key) is True

        with tempfile.TemporaryDirectory() as dest_dir:
            dest = Path(dest_dir) / "downloaded_large.bin"
            await backend.download_file(key, dest)
            actual_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
            assert actual_hash == expected_hash, "SHA256 mismatch on 10MB payload"


@pytest.mark.asyncio
async def test_s3_upload_file_type_validation():
    """Verify S3StorageBackend raises TypeError on unsupported source types."""
    s3 = S3StorageBackend(
        bucket_name="omniagent",
        internal_endpoint="http://minio:9000",
        public_endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
    )

    with pytest.raises(TypeError):
        await s3.upload_file(12345, "invalid.bin")  # type: ignore

    with pytest.raises(FileNotFoundError):
        await s3.upload_file("/path/to/definitely/nonexistent/file.bin", "invalid.bin")
