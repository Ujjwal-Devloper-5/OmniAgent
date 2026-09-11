"""
OmniAgent Phase 7 Test Suite — Central Fixtures & Configuration
═══════════════════════════════════════════════════════════════
Provides:
  - Custom pytest markers (live, unit, integration)
  - Seamless async test runner (independent of pytest-asyncio plugin)
  - In-memory mock Redis engine with sliding window & atomic Lua simulation
  - Mock S3 storage backend with dual-endpoint presigned URL generation
  - Mock Docker Socket Proxy and subprocess interceptors
  - Live stack availability probes and clients
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import socket
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple, Union

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Pytest Markers & Hooks
# ─────────────────────────────────────────────────────────────────────────────

def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "live: Mark test as requiring the live running docker-compose stack"
    )
    config.addinivalue_line(
        "markers", "unit: Fast isolated unit test with mock dependencies"
    )
    config.addinivalue_line(
        "markers", "integration: Multi-component integration test"
    )


def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> Optional[bool]:
    """
    Hook to execute async test functions cleanly without requiring pytest-asyncio.
    If the test function is an async coroutine, run it inside asyncio.run().
    """
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Resolve fixtures for the function
            funcargs = {
                arg: pyfuncitem.funcargs[arg]
                for arg in pyfuncitem._fixtureinfo.argnames
                if arg in pyfuncitem.funcargs
            }
            loop.run_until_complete(pyfuncitem.obj(**funcargs))
        finally:
            loop.close()
            asyncio.set_event_loop(None)
        return True
    return None


# ─────────────────────────────────────────────────────────────────────────────
# In-Memory Mock Redis Engine
# ─────────────────────────────────────────────────────────────────────────────

class MockRedisSortedSet:
    """Simulates Redis Sorted Set (ZSET)."""

    def __init__(self) -> None:
        # member -> score
        self._items: Dict[str, float] = {}

    def zadd(self, mapping: Dict[str, float]) -> int:
        added = 0
        for member, score in mapping.items():
            if member not in self._items:
                added += 1
            self._items[member] = float(score)
        return added

    def zremrangebyscore(self, min_score: float, max_score: float) -> int:
        to_remove = [
            m for m, s in self._items.items()
            if min_score <= s <= max_score
        ]
        for m in to_remove:
            del self._items[m]
        return len(to_remove)

    def zcard(self) -> int:
        return len(self._items)

    def zrange(self, start: int, stop: int, withscores: bool = False) -> list:
        sorted_items = sorted(self._items.items(), key=lambda x: x[1])
        # Handle negative indexes
        if stop < 0:
            stop = len(sorted_items) + stop + 1
        else:
            stop = stop + 1
        sliced = sorted_items[start:stop]
        if withscores:
            res = []
            for m, s in sliced:
                res.extend([m, s])
            return res
        return [m for m, _ in sliced]


class MockRedisClient:
    """
    High-fidelity in-memory Redis client supporting:
      - String counters (INCRBY, GET, SET)
      - Key expiration & TTL
      - Sorted sets (ZADD, ZREMRANGEBYSCORE, ZCARD, ZRANGE)
      - Atomic Lua scripts evaluation (sliding-window rate limiting)
      - Pipelines
      - Simulated network failure / circuit breaking
    """

    def __init__(self) -> None:
        self._strings: Dict[str, str] = {}
        self._zsets: Dict[str, MockRedisSortedSet] = {}
        self._ttls: Dict[str, float] = {}
        self._connected: bool = True
        self._scripts: Dict[str, str] = {}

    def set_connected(self, connected: bool) -> None:
        """Simulate network partition or Redis service failure."""
        self._connected = connected

    def _check_connection(self) -> None:
        if not self._connected:
            raise ConnectionRefusedError("Connection to Redis at localhost:6379 refused (mock error)")

    async def ping(self) -> bool:
        self._check_connection()
        return True

    async def get(self, key: str) -> Optional[str]:
        self._check_connection()
        self._check_expiry(key)
        return self._strings.get(key)

    async def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        self._check_connection()
        self._strings[key] = str(value)
        if ex is not None:
            self._ttls[key] = time.time() + ex
        return True

    async def incrby(self, key: str, amount: int = 1) -> int:
        self._check_connection()
        self._check_expiry(key)
        val = int(self._strings.get(key, "0")) + amount
        self._strings[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int) -> bool:
        self._check_connection()
        self._ttls[key] = time.time() + seconds
        return True

    def _check_expiry(self, key: str) -> None:
        if key in self._ttls and time.time() > self._ttls[key]:
            self._strings.pop(key, None)
            self._zsets.pop(key, None)
            self._ttls.pop(key, None)

    def _get_zset(self, key: str) -> MockRedisSortedSet:
        self._check_expiry(key)
        if key not in self._zsets:
            self._zsets[key] = MockRedisSortedSet()
        return self._zsets[key]

    async def zadd(self, key: str, mapping: Dict[str, float]) -> int:
        self._check_connection()
        return self._get_zset(key).zadd(mapping)

    async def zremrangebyscore(self, key: str, min_score: Union[str, float], max_score: Union[str, float]) -> int:
        self._check_connection()
        min_f = float("-inf") if min_score in ("-inf", float("-inf")) else float(min_score)
        max_f = float("inf") if max_score in ("+inf", float("inf")) else float(max_score)
        return self._get_zset(key).zremrangebyscore(min_f, max_f)

    async def zcard(self, key: str) -> int:
        self._check_connection()
        return self._get_zset(key).zcard()

    async def zrange(self, key: str, start: int, stop: int, withscores: bool = False) -> list:
        self._check_connection()
        return self._get_zset(key).zrange(start, stop, withscores)

    async def keys(self, pattern: str = "*") -> list[str]:
        self._check_connection()
        import fnmatch
        all_keys = set(self._strings.keys()) | set(self._zsets.keys())
        for k in list(all_keys):
            self._check_expiry(k)
        all_keys = set(self._strings.keys()) | set(self._zsets.keys())
        return [k for k in sorted(all_keys) if fnmatch.fnmatch(k, pattern)]

    async def delete(self, *keys: str) -> int:
        self._check_connection()
        deleted = 0
        for k in keys:
            if k in self._strings or k in self._zsets:
                deleted += 1
            self._strings.pop(k, None)
            self._zsets.pop(k, None)
            self._ttls.pop(k, None)
        return deleted

    def register_script(self, script: str) -> Any:
        sha = str(uuid.uuid4())
        self._scripts[sha] = script

        class ScriptRunner:
            def __init__(self, parent: MockRedisClient, lua: str) -> None:
                self.parent = parent
                self.lua = lua

            async def __call__(self, keys: list, args: list) -> Any:
                return await self.parent.eval(self.lua, len(keys), *keys, *args)

        return ScriptRunner(self, script)

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        """
        Executes sliding-window Lua script natively in Python.
        """
        self._check_connection()
        keys = keys_and_args[:numkeys]
        args = keys_and_args[numkeys:]

        # Sliding window Lua script simulation:
        key = keys[0]
        now = float(args[0])
        window = float(args[1])
        limit = int(args[2])
        member = str(args[3])
        cost = int(args[4]) if len(args) > 4 else 1
        clear_before = now - window

        zset = self._get_zset(key)
        # 1. Evict outside sliding window
        zset.zremrangebyscore(float("-inf"), clear_before)

        # 2. Count requests
        count = zset.zcard()

        # 3. Check quota
        if count + cost <= limit:
            for i in range(cost):
                zset.zadd({f"{member}:{i+1}": now})
            self._ttls[key] = time.time() + (window * 2)
            return [1, limit - (count + cost), 0]
        else:
            oldest = zset.zrange(0, 0, withscores=True)
            wait_time = int(window)
            if len(oldest) >= 2:
                wait_time = max(1, int(float(oldest[1]) + window - now + 0.999))
            return [0, count, wait_time]

    def pipeline(self, transaction: bool = True) -> MockPipeline:
        return MockPipeline(self)

    async def close(self) -> None:
        pass


class MockPipeline:
    def __init__(self, client: MockRedisClient) -> None:
        self.client = client
        self._commands: list[Callable] = []

    def incrby(self, key: str, amount: int = 1) -> MockPipeline:
        self._commands.append(lambda: self.client.incrby(key, amount))
        return self

    def expire(self, key: str, seconds: int) -> MockPipeline:
        self._commands.append(lambda: self.client.expire(key, seconds))
        return self

    async def execute(self) -> list:
        results = []
        for cmd in self._commands:
            results.append(await cmd())
        return results

    async def __aenter__(self) -> MockPipeline:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# In-Memory Mock S3 Storage Engine
# ─────────────────────────────────────────────────────────────────────────────

class MockS3StorageBackend:
    """
    Simulates S3StorageBackend with:
      - In-memory object store (bytes)
      - Dual-endpoint presigned URL generation (internal vs public URL)
      - Pre-signed GET and PUT URL verification
      - TTL/expiry simulation
    """

    def __init__(
        self,
        bucket_name: str = "omniagent-reports",
        internal_endpoint: str = "http://minio:9000",
        public_endpoint: str = "http://localhost:9000",
    ) -> None:
        self.bucket = bucket_name
        self.internal_endpoint = internal_endpoint.rstrip("/")
        self.public_endpoint = public_endpoint.rstrip("/")
        self._store: Dict[str, bytes] = {}
        self._metadata: Dict[str, dict] = {}
        self._presigned_tokens: Dict[str, dict] = {}

    async def upload_file(
        self,
        source: Union[str, Path, bytes],
        destination_key: str,
        content_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        if isinstance(source, (str, Path)):
            with open(source, "rb") as f:
                data = f.read()
        else:
            data = bytes(source)

        self._store[destination_key] = data
        self._metadata[destination_key] = {
            "content_type": content_type or "application/octet-stream",
            "size": len(data),
            "created_at": time.time(),
            "metadata": metadata or {},
        }
        return f"s3://{self.bucket}/{destination_key}"

    async def download_file(self, key: str, destination_path: Union[str, Path]) -> Path:
        if key not in self._store:
            raise FileNotFoundError(f"Key '{key}' not found in bucket '{self.bucket}'")
        dest = Path(destination_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._store[key])
        return dest

    async def file_exists(self, key: str) -> bool:
        return key in self._store

    async def delete_file(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            self._metadata.pop(key, None)
            return True
        return False

    async def generate_presigned_url(
        self,
        key: str,
        expires_in: int = 3600,
        http_method: str = "GET",
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Generates an externally resolvable pre-signed URL using public_endpoint.
        Embeds SigV4-compliant parameters in query string.
        """
        token = uuid.uuid4().hex[:16]
        self._presigned_tokens[token] = {
            "key": key,
            "method": http_method.upper(),
            "expires_at": time.time() + expires_in,
            "filename": filename,
            "content_type": content_type,
        }

        # Format URL matching AWS SigV4 path-style:
        # http://localhost:9000/omniagent-reports/<key>?X-Amz-Algorithm=...
        query = (
            f"X-Amz-Algorithm=AWS4-HMAC-SHA256"
            f"&X-Amz-Credential=minioadmin%2F20260911%2Fus-east-1%2Fs3%2Faws4_request"
            f"&X-Amz-Date=20260911T013500Z"
            f"&X-Amz-Expires={expires_in}"
            f"&X-Amz-SignedHeaders=host"
            f"&X-Amz-Signature={token}"
        )
        if filename:
            query += f"&response-content-disposition=attachment%3B%20filename%3D%22{filename}%22"

        return f"{self.public_endpoint}/{self.bucket}/{key}?{query}"

    async def cleanup_old_files(self, prefix: str = "", days: int = 7) -> int:
        now = time.time()
        cutoff = now - (days * 86400)
        to_delete = []
        for key, meta in self._metadata.items():
            if key.startswith(prefix) and meta["created_at"] < cutoff:
                to_delete.append(key)
        for k in to_delete:
            await self.delete_file(k)
        return len(to_delete)

    # Simulated HTTP GET/PUT handler for tests
    def simulate_http_request(self, url: str, method: str = "GET", body: bytes = b"") -> Tuple[int, dict, bytes]:
        """Simulates external client calling a presigned URL."""
        if not url.startswith(self.public_endpoint):
            return 403, {}, b"Invalid endpoint host"

        # Parse key and signature
        match = re.search(r"X-Amz-Signature=([a-f0-9]+)", url)
        if not match:
            return 403, {}, b"Missing signature"
        token = match.group(1)

        token_info = self._presigned_tokens.get(token)
        if not token_info:
            return 403, {}, b"SignatureDoesNotMatch"

        if time.time() > token_info["expires_at"]:
            return 403, {}, b"Request has expired"

        key = token_info["key"]
        expected_method = token_info["method"]

        if method.upper() != expected_method:
            return 405, {}, b"Method Not Allowed"

        if method.upper() == "GET":
            if key not in self._store:
                return 404, {}, b"NoSuchKey"
            headers = {
                "Content-Type": self._metadata[key]["content_type"],
                "Content-Length": str(len(self._store[key])),
            }
            if token_info.get("filename"):
                headers["Content-Disposition"] = f'attachment; filename="{token_info["filename"]}"'
            return 200, headers, self._store[key]

        elif method.upper() == "PUT":
            self._store[key] = body
            self._metadata[key] = {
                "content_type": token_info.get("content_type") or "application/octet-stream",
                "size": len(body),
                "created_at": time.time(),
                "metadata": {},
            }
            return 200, {}, b""

        return 400, {}, b"Bad Request"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis() -> MockRedisClient:
    """Provides an isolated MockRedisClient."""
    return MockRedisClient()


@pytest.fixture
def mock_s3() -> MockS3StorageBackend:
    """Provides an isolated MockS3StorageBackend."""
    return MockS3StorageBackend()


@pytest.fixture
def is_live_stack_running() -> bool:
    """
    Checks if the live Docker stack is responding on OmniAgent port 8080.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        res = sock.connect_ex(("127.0.0.1", 8080))
        return res == 0
    except Exception:
        return False
    finally:
        sock.close()


@pytest.fixture
def live_endpoints() -> Dict[str, str]:
    """Returns authoritative endpoints for the live stack."""
    return {
        "omniagent": "http://localhost:8080",
        "redis": "redis://localhost:6379/0",
        "minio": "http://localhost:9000",
        "prometheus": "http://localhost:9090",
        "grafana": "http://localhost:3001",
        "dashboard": "http://localhost:3000",
    }
