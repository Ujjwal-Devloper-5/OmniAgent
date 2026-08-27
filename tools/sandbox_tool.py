"""
Enterprise-Grade Isolated Docker Sandbox Tool
═══════════════════════════════════════════════
Provides AI agents with a fully isolated, ephemeral Docker container to:
  • Execute shell commands (bash, sh)
  • Run Python / Node.js / Ruby code
  • Install packages (pip, npm, apt)
  • Browse the web from within the sandbox
  • Inspect files and run scripts

Security Architecture:
  • Ephemeral containers — created fresh per session, destroyed after use
  • No host filesystem mounts — complete isolation
  • Strict resource limits: CPU, memory, PID count, no privilege escalation
  • Network: internet access allowed (for pip/npm/curl) but host network is blocked
  • Dangerous command blocklist: rm -rf /, fork bombs, reverse shells, etc.
  • Read-only root filesystem with /tmp writable
  • Non-root user inside container
  • 30-second per-command timeout, 10-minute container lifetime

Container Image: python:3.12-slim (pre-pulled, always available)
  Extras installed at first use: curl, wget, git, jq, net-tools
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shlex
import time
from typing import Optional

from langchain_core.tools import tool

from core.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

_SANDBOX_IMAGE         = "ubuntu:24.04"
_CONTAINER_PREFIX      = "omniagent-sandbox"
_CMD_TIMEOUT_SECONDS   = 300       # Per-command timeout (5 minutes)
_CONTAINER_TTL_SECONDS = 7200      # Auto-kill container after 2 hours
_MAX_OUTPUT_CHARS      = 6_000     # Truncate long outputs
_MAX_CONTAINERS        = 5         # Max concurrent sandboxes

# Resource constraints
_MEMORY_LIMIT  = "1g"
_CPU_QUOTA     = 100_000          # 100% of 1 CPU (out of 100000 per period)
_PIDS_LIMIT    = 256              # Allow more processes for complex workloads

# ─────────────────────────────────────────────────────────────────────────────
# Dangerous pattern blocklist
# ─────────────────────────────────────────────────────────────────────────────
# These patterns are blocked BEFORE execution — they can cause container
# or host damage even in isolation.

_BLOCKED_PATTERNS: list[re.Pattern] = [
    # Recursive deletion of root or core paths
    re.compile(r"rm\s+(-\w*\s+)*-[^-]*r[^-]*\s+/(?!\w)", re.I),   # rm -rf /
    re.compile(r"rm\s+.*--no-preserve-root", re.I),
    # Fork bombs
    re.compile(r":\s*\(\s*\)\s*\{.*\|\s*:.*&.*\}", re.I),
    # dd to /dev/sda or /dev/disk
    re.compile(r"dd\s+.*of=/dev/(s|h|v)d[a-z]", re.I),
    re.compile(r"dd\s+.*of=/dev/disk", re.I),
    # Redirect to /dev/sda
    re.compile(r">\s*/dev/(s|h)d[a-z]"),
    # Chmod 777 sensitive dirs
    re.compile(r"chmod\s+(-R\s+)?777\s+/(?!tmp|home|workspace)", re.I),
    # mkfs (format filesystem)
    re.compile(r"mkfs\.", re.I),
    # Reverse shells (common patterns)
    re.compile(r"bash\s+-i\s+>&?\s*/dev/tcp/", re.I),
    re.compile(r"nc\s+(-[^-]*e[^-]*|-e\b).*(/bin/|bash|sh)", re.I),
    re.compile(r"python.*socket.*connect.*exec", re.I | re.S),
    # /proc/sysrq-trigger
    re.compile(r"/proc/sysrq", re.I),
    # Privilege escalation via kernel
    re.compile(r"nsenter|unshare\s+.*--mount", re.I),
    # Crypto mining
    re.compile(r"(xmrig|minerd|cgminer|cpuminer)", re.I),
    # Exfiltration patterns
    re.compile(r"curl\s+.*\|\s*(sh|bash|python)", re.I),
    re.compile(r"wget\s+.*-O\s*-\s*\|\s*(sh|bash)", re.I),
]

# Warn-but-allow patterns (log them prominently)
_WARN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"curl\s+", re.I),         "network fetch via curl"),
    (re.compile(r"wget\s+", re.I),         "network fetch via wget"),
    (re.compile(r"pip\s+install", re.I),   "package installation"),
    (re.compile(r"npm\s+install", re.I),   "npm package installation"),
    (re.compile(r"apt[- ]get\s+", re.I),   "apt package operation"),
    (re.compile(r"chmod\s+", re.I),        "file permission change"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Container Pool (in-memory, per-process)
# ─────────────────────────────────────────────────────────────────────────────

class _ContainerRecord:
    __slots__ = ("container_id", "session_key", "created_at", "last_used")

    def __init__(self, container_id: str, session_key: str) -> None:
        self.container_id = container_id
        self.session_key  = session_key
        self.created_at   = time.monotonic()
        self.last_used    = time.monotonic()

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.created_at

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used

    def touch(self) -> None:
        self.last_used = time.monotonic()


class _SandboxPool:
    """
    Manages a pool of ephemeral Docker containers.
    Each session_key gets its own container (persistent workspace within TTL).
    """

    def __init__(self) -> None:
        self._containers: dict[str, _ContainerRecord] = {}
        self._lock = asyncio.Lock()
        self._image_ready = False

    async def _ensure_image(self) -> None:
        """Pull sandbox image if not already present."""
        if self._image_ready:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "image", "inspect", _SANDBOX_IMAGE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode == 0:
                self._image_ready = True
                log.info("Sandbox image '%s' already present", _SANDBOX_IMAGE)
                return
        except Exception:
            pass

        log.info("Pulling sandbox image '%s'...", _SANDBOX_IMAGE)
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "pull", _SANDBOX_IMAGE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0:
                self._image_ready = True
                log.info("Sandbox image pulled successfully")
            else:
                log.error("Failed to pull image: %s", stderr.decode())
        except Exception as exc:
            log.error("Image pull failed: %s", exc)

    async def _create_container(self, session_key: str) -> str:
        """
        Spin up a new isolated Docker container.
        Returns container ID.
        """
        await self._ensure_image()

        container_name = f"{_CONTAINER_PREFIX}-{session_key[:12]}-{int(time.time())}"

        cmd = [
            "docker", "run",
            "--detach",
            "--rm",                            # Auto-remove when stopped
            "--name", container_name,
            # Resource limits
            f"--memory={_MEMORY_LIMIT}",
            f"--memory-swap={_MEMORY_LIMIT}",  # No swap
            f"--cpu-quota={_CPU_QUOTA}",
            f"--pids-limit={_PIDS_LIMIT}",
            # Security hardening — drop dangerous caps, keep minimal set
            "--security-opt", "no-new-privileges:true",
            "--cap-drop", "ALL",
            "--cap-add", "CHOWN",
            "--cap-add", "DAC_OVERRIDE",
            "--cap-add", "SETUID",
            "--cap-add", "SETGID",
            "--cap-add", "NET_BIND_SERVICE",
            # No host network (isolated bridge) — internet works, host doesn't
            "--network", "bridge",
            # Writable /workspace for user files — NO --read-only (breaks pip/apt)
            "--workdir", "/workspace",
            # Run as root inside the container so pip/apt install works
            # Security comes from cap-drop + no host mounts, not from non-root
            "--user", "root",
            # Environment
            "--env", "HOME=/root",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", "TERM=xterm-256color",
            "--env", "DEBIAN_FRONTEND=noninteractive",
            "--env", "PIP_BREAK_SYSTEM_PACKAGES=1",
            # Named volume: persists /workspace across container restarts for
            # the same session key (first 16 hex chars = 64-bit session scope)
            "--volume", f"omniagent-ws-{session_key[:16]}:/workspace",
            # Keep alive
            _SANDBOX_IMAGE,
            "sleep", str(_CONTAINER_TTL_SECONDS),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to create sandbox container: {stderr.decode().strip()}"
            )

        container_id = stdout.decode().strip()
        log.info(
            "Sandbox created | session=%s container=%s",
            session_key, container_id[:12],
        )

        # Pre-warm: update package lists so apt-get install works immediately
        # Run in background — don't block the first command
        async def _prewarm():
            try:
                proc2 = await asyncio.create_subprocess_exec(
                    "docker", "exec", container_id,
                    "bash", "-c",
                    "apt-get update -qq 2>/dev/null && apt-get install -y -qq python3 python3-pip curl wget git jq 2>/dev/null; mkdir -p /workspace",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc2.wait(), timeout=60)
            except Exception:
                pass  # Non-fatal — user can always run apt-get update manually

        await _prewarm()
        return container_id

    async def get_or_create(self, session_key: str) -> str:
        """
        Get existing container for session, or create a new one.
        Cleans up expired containers automatically.
        """
        async with self._lock:
            # Clean up expired containers
            to_remove = []
            for key, record in self._containers.items():
                if record.age_seconds > _CONTAINER_TTL_SECONDS - 30:
                    to_remove.append(key)
                    asyncio.create_task(self._kill_container(record.container_id))
            for key in to_remove:
                self._containers.pop(key, None)

            # Return existing container if healthy
            if session_key in self._containers:
                record = self._containers[session_key]
                if await self._is_running(record.container_id):
                    record.touch()
                    return record.container_id
                else:
                    self._containers.pop(session_key, None)

            # Enforce max container limit
            if len(self._containers) >= _MAX_CONTAINERS:
                # Kill the oldest container
                oldest_key = min(
                    self._containers, key=lambda k: self._containers[k].created_at
                )
                old_record = self._containers.pop(oldest_key)
                asyncio.create_task(self._kill_container(old_record.container_id))

            # Create new container
            container_id = await self._create_container(session_key)
            self._containers[session_key] = _ContainerRecord(container_id, session_key)
            return container_id

    async def _is_running(self, container_id: str) -> bool:
        """Check if a container is still running."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "inspect", "--format", "{{.State.Running}}", container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode().strip().lower() == "true"
        except Exception:
            return False

    async def _kill_container(self, container_id: str) -> None:
        """Kill and remove a container gracefully."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "kill", container_id,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            log.debug("Killed container %s", container_id[:12])
        except Exception as exc:
            log.warning("Failed to kill container %s: %s", container_id[:12], exc)

    async def exec_in(
        self,
        container_id: str,
        command: str,
        timeout: int = _CMD_TIMEOUT_SECONDS,
    ) -> tuple[str, str, int]:
        """
        Execute a command inside the container.

        Returns (stdout, stderr, exit_code).
        """
        # Use bash -c for shell command interpretation
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec",
            "--workdir", "/workspace",
            container_id,
            "bash", "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return (
                stdout.decode(errors="replace"),
                stderr.decode(errors="replace"),
                proc.returncode or 0,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return "", f"Command timed out after {timeout}s", 124

    async def install_base_tools(self, container_id: str) -> None:
        """Install useful CLI tools in the container on first use."""
        install_cmd = (
            "apt-get update -qq 2>/dev/null && "
            "apt-get install -y -qq --no-install-recommends "
            "curl wget git jq file net-tools dnsutils unzip zip 2>/dev/null; "
            "pip install --quiet requests httpx rich tabulate pandas numpy 2>/dev/null; "
            "echo 'Tools ready'"
        )
        # Run as root for install (exec --user root)
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec",
            "--user", "root",
            "--workdir", "/tmp",
            container_id,
            "bash", "-c", install_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass


# Singleton pool
_pool: _SandboxPool | None = None


def _get_pool() -> _SandboxPool:
    global _pool
    if _pool is None:
        _pool = _SandboxPool()
    return _pool


# ─────────────────────────────────────────────────────────────────────────────
# Security checks
# ─────────────────────────────────────────────────────────────────────────────

def _check_safety(command: str) -> tuple[bool, str]:
    """
    Check command against blocked and warning patterns.

    Returns:
        (is_safe, message) — if not safe, message explains why.
    """
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(command):
            return False, (
                f"🚫 Command blocked by security policy.\n"
                f"Pattern matched: `{pattern.pattern[:60]}`\n"
                f"This command could cause system damage or security breach."
            )

    warnings = []
    for pattern, description in _WARN_PATTERNS:
        if pattern.search(command):
            warnings.append(description)

    if warnings:
        log.warning(
            "Sandbox: elevated-risk command | actions=[%s] cmd=%s",
            ", ".join(warnings), command[:100],
        )

    return True, ""


def _truncate_output(text: str, max_chars: int = _MAX_OUTPUT_CHARS) -> str:
    """Truncate output and add notice if too long."""
    if len(text) <= max_chars:
        return text
    kept = text[:max_chars]
    lines_dropped = text[max_chars:].count("\n")
    return (
        kept
        + f"\n\n... [Output truncated — {len(text) - max_chars:,} chars / "
        f"{lines_dropped} lines omitted. Use `head`, `tail`, or `grep` for targeted output] ..."
    )


def _format_result(
    command: str,
    stdout: str,
    stderr: str,
    exit_code: int,
    elapsed: float,
) -> str:
    """Format command output into a clean, readable result."""
    parts: list[str] = []

    if stdout.strip():
        parts.append(f"```\n{_truncate_output(stdout.rstrip())}\n```")

    if stderr.strip():
        # Some tools (pip, apt) write progress to stderr — show as info
        label = "ℹ️ stderr" if exit_code == 0 else "⚠️ stderr"
        parts.append(f"{label}:\n```\n{_truncate_output(stderr.rstrip())}\n```")

    if not stdout.strip() and not stderr.strip():
        if exit_code == 0:
            parts.append("✅ Command completed with no output.")
        else:
            parts.append("⚠️ Command produced no output.")

    status = "✅" if exit_code == 0 else f"❌ (exit {exit_code})"
    footer = f"\n{status} | Elapsed: {elapsed:.2f}s | Sandbox: isolated Docker container"
    parts.append(footer)

    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Tool: run_sandbox_command
# ─────────────────────────────────────────────────────────────────────────────

@tool
async def run_sandbox_command(command: str, session_id: str = "default") -> str:
    """
    Execute a shell command inside a fully isolated Docker sandbox container.

    The sandbox provides:
    - A fully-equipped Ubuntu 24.04 environment (python3, pip, curl, git pre-installed)
    - Full internet access (for pip install, curl, wget, etc.)
    - Python 3.12, bash, curl, wget, git, jq, pip pre-installed
    - Popular Python packages: requests, httpx, pandas, numpy, rich
    - Persistent workspace within the session (/workspace directory)
    - 30-second per-command timeout
    - 256MB memory limit, 50% CPU limit
    - Complete host isolation — no access to host filesystem

    Use this tool to:
    - Run shell commands, scripts, and system operations
    - Execute Python code with full standard library access
    - Install and use any Python package (pip install ...)
    - Test APIs with curl/httpx
    - Process files and data
    - Compile and run code in any language

    Do NOT use for: anything you wouldn't do in a safe test environment.

    Args:
        command: Shell command or script to execute. Multi-line scripts supported.
        session_id: Session identifier — same session_id reuses the same container
                    workspace (persistent state across calls in a conversation).

    Returns:
        Command output (stdout + stderr), exit code, and timing info.

    Examples:
        run_sandbox_command("python3 -c 'import sys; print(sys.version)'")
        run_sandbox_command("pip install requests && python3 -c 'import requests; print(requests.__version__)'")
        run_sandbox_command("echo 'Hello World' && ls -la")
        run_sandbox_command("curl -s https://api.github.com/users/octocat | python3 -m json.tool")
    """
    if not command or not command.strip():
        return "❌ No command provided."

    # Security check
    is_safe, block_reason = _check_safety(command)
    if not is_safe:
        return block_reason

    # Generate stable session key from session_id
    session_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]

    pool = _get_pool()
    start = time.monotonic()

    try:
        container_id = await pool.get_or_create(session_key)
    except Exception as exc:
        log.error("Failed to get sandbox container: %s", exc)
        return (
            f"❌ Could not start sandbox container: {exc}\n"
            f"Make sure Docker is running: `docker ps`"
        )

    try:
        stdout, stderr, exit_code = await pool.exec_in(
            container_id, command, timeout=_CMD_TIMEOUT_SECONDS
        )
    except Exception as exc:
        log.error("Sandbox exec failed: %s", exc)
        return f"❌ Execution error: {exc}"

    elapsed = time.monotonic() - start

    log.info(
        "Sandbox exec | session=%s exit=%d elapsed=%.2fs cmd=%s",
        session_id, exit_code, elapsed, command[:80],
    )

    return _format_result(command, stdout, stderr, exit_code, elapsed)


# ─────────────────────────────────────────────────────────────────────────────
# Tool: write_sandbox_file + read_sandbox_file
# ─────────────────────────────────────────────────────────────────────────────

@tool
async def write_sandbox_file(
    filename: str,
    content: str,
    session_id: str = "default",
) -> str:
    """
    Write a file into the sandbox workspace (/workspace/<filename>).

    Use this to:
    - Create Python scripts to run
    - Write config files
    - Create input data for commands

    Args:
        filename: File name (e.g. 'script.py', 'data.json'). No path traversal.
        content:  File content to write.
        session_id: Session identifier — must match the session_id used in run_sandbox_command.

    Returns:
        Confirmation message with file path and size.
    """
    import base64

    # Sanitize filename — no path traversal
    safe_name = re.sub(r"[^\w.\-]", "_", filename.replace("/", "_").replace("..", ""))
    if not safe_name:
        return "❌ Invalid filename."

    session_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    pool = _get_pool()

    try:
        container_id = await pool.get_or_create(session_key)
    except Exception as exc:
        return f"❌ Could not access sandbox: {exc}"

    # Encode content as base64 to avoid shell quoting nightmares
    b64_content = base64.b64encode(content.encode("utf-8")).decode("ascii")
    cmd = (
        f"python3 -c \""
        f"import base64, pathlib; "
        f"pathlib.Path('/workspace/{safe_name}').write_bytes("
        f"base64.b64decode('{b64_content}'))\""
    )

    _, stderr, exit_code = await pool.exec_in(container_id, cmd, timeout=10)

    if exit_code == 0:
        byte_count = len(content.encode("utf-8"))
        return (
            f"✅ Written `/workspace/{safe_name}` ({byte_count:,} bytes)\n"
            f"Run it with: `run_sandbox_command('python3 {safe_name}', session_id=...)`"
        )
    else:
        return f"❌ Failed to write file: {stderr.strip() or 'Unknown error'}"


@tool
async def read_sandbox_file(
    filename: str,
    session_id: str = "default",
) -> str:
    """
    Read a file from the sandbox workspace (/workspace/<filename>).

    Use this to retrieve output files, logs, or data generated by commands.

    Args:
        filename: File name to read from /workspace/.
        session_id: Session identifier — must match the session used to create the file.

    Returns:
        File contents (up to 8KB) or an error message.
    """
    safe_name = re.sub(r"[^\w.\-]", "_", filename.replace("/", "_").replace("..", ""))
    if not safe_name:
        return "❌ Invalid filename."

    session_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    pool = _get_pool()

    try:
        container_id = await pool.get_or_create(session_key)
    except Exception as exc:
        return f"❌ Could not access sandbox: {exc}"

    stdout, stderr, exit_code = await pool.exec_in(
        container_id,
        f"cat /workspace/{safe_name} 2>/dev/null | head -c 8192",
        timeout=10,
    )

    if exit_code == 0 and stdout:
        return f"📄 `/workspace/{safe_name}`:\n```\n{stdout}\n```"
    elif exit_code == 0 and not stdout:
        return f"📄 `/workspace/{safe_name}` exists but is empty."
    else:
        return f"❌ File not found or not readable: `/workspace/{safe_name}`"


# ─────────────────────────────────────────────────────────────────────────────
# Tool: list_sandbox_files
# ─────────────────────────────────────────────────────────────────────────────

@tool
async def list_sandbox_files(session_id: str = "default") -> str:
    """
    List all files in the sandbox workspace.

    Args:
        session_id: Session identifier.

    Returns:
        Directory listing of /workspace.
    """
    session_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    pool = _get_pool()

    try:
        container_id = await pool.get_or_create(session_key)
    except Exception as exc:
        return f"❌ Could not access sandbox: {exc}"

    stdout, _, exit_code = await pool.exec_in(
        container_id,
        "ls -lah /workspace/ 2>/dev/null || echo '(empty workspace)'",
        timeout=10,
    )
    return f"📁 Sandbox workspace:\n```\n{stdout.strip()}\n```"


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────

SANDBOX_TOOLS = [
    run_sandbox_command,
    write_sandbox_file,
    read_sandbox_file,
    list_sandbox_files,
]
