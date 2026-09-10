"""
OmniAgent Universal File Delivery System
═════════════════════════════════════════════════════════════════════
Gives the AI agent ONE powerful, universal tool to deliver any file
to the user's chat — regardless of format.

Philosophy:
  The AI already has a full Ubuntu sandbox with Python, pip, and the
  full filesystem at /workspace. It can generate Excel with openpyxl,
  PPTX with python-pptx, PDFs with reportlab or weasyprint, images with
  Pillow, ZIP archives, CSVs, JSON — ANYTHING.

  We don't need tools for each format. We need ONE universal upload tool.

Tools exposed to the AI agent:
  1. upload_file(content, filename, description)
     → Write any text/binary content to a temp file and upload to platform.
     → Use for text-based files the AI can generate inline (TXT, CSV, JSON,
       HTML, Markdown, Python source, YAML, etc.)

  2. run_code_and_upload(python_code, output_filename, description)
     → Execute Python code that writes to /output/<filename>, then upload
       the generated file to platform.
     → Use for binary formats: PDF (weasyprint/reportlab), Excel (openpyxl),
       PPTX (python-pptx), PNG (Pillow/matplotlib), ZIP, etc.
     → The code runs in an isolated subprocess with a 60s timeout.
     → IMPORTANT: code MUST write the file to /output/<filename>.

Platform callbacks (registered by adapters at startup):
  register_upload_callback(platform, async_fn(file_path, filename, chat_id, description))
  set_upload_context(platform, chat_id)  — called per request
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool
from core.logger import get_logger

log = get_logger(__name__)

# ─── Platform upload registry ─────────────────────────────────────────────────

import contextvars
from dataclasses import dataclass

@dataclass
class UploadContext:
    platform: str
    target_id: str
    is_internal_swarm: bool = False

_upload_callbacks: dict[str, object] = {}
_upload_ctx: contextvars.ContextVar[Optional[UploadContext]] = contextvars.ContextVar('upload_ctx', default=None)


def register_upload_callback(platform: str, callback) -> None:
    """Called by platform adapters at startup to register their upload function.
    Callback signature: async fn(file_path: str, filename: str, target_id: str, description: str)
    """
    _upload_callbacks[platform] = callback
    log.info("Upload callback registered | platform=%s", platform)


def set_upload_context(platform: str, target_id: str, is_internal_swarm: bool = False) -> None:
    """Set the upload destination for the current async task. Call this before process_message()."""
    _upload_ctx.set(UploadContext(platform=platform, target_id=str(target_id), is_internal_swarm=is_internal_swarm))


def get_upload_context() -> Optional[UploadContext]:
    return _upload_ctx.get()


def _reports_dir() -> Path:
    ctx = get_upload_context()
    if ctx and ctx.platform and ctx.target_id:
        d = Path(f"/app/data/reports/{ctx.platform}_{ctx.target_id}")
    else:
        d = Path("/app/data/reports/global")
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _deliver_file(file_path: str, filename: str, description: str = "") -> str:
    """
    Core delivery: try platform upload, fall back to saving in /app/data/reports/.
    Returns a user-facing status string.
    """
    ctx = get_upload_context()

    # DRAFT SUPPRESSION: If the swarm is running a QA loop, do NOT upload intermediate drafts to the user.
    if ctx and ctx.is_internal_swarm:
        reports = _reports_dir()
        final_path = reports / filename
        shutil.copy2(file_path, str(final_path))
        log.info("File generated internally (upload suppressed) | filename=%s", filename)
        return f"[INTERNAL_DRAFT_READY: {final_path}]"

    size_kb = os.path.getsize(file_path) / 1024

    if ctx and ctx.platform and ctx.target_id and ctx.platform in _upload_callbacks:
        try:
            cb = _upload_callbacks[ctx.platform]
            if asyncio.iscoroutinefunction(cb):
                await cb(file_path, filename, ctx.target_id, description)
            else:
                await asyncio.get_event_loop().run_in_executor(None, cb, file_path, filename, ctx.target_id, description)
            log.info("File delivered | platform=%s | filename=%s | size=%.1fKB", ctx.platform, filename, size_kb)
            return f"✅ **{filename}** ({size_kb:.1f} KB) delivered to {ctx.platform}!"
        except Exception as exc:
            log.warning("Platform upload failed, saving locally: %s", exc)

    # Fallback: save to /app/data/reports/
    dest = _reports_dir() / filename
    try:
        shutil.copy2(file_path, str(dest))
        return (
            f"✅ **{filename}** ({size_kb:.1f} KB) saved to `/app/data/reports/{filename}`.\n"
            f"Direct upload to chat was unavailable in this context."
        )
    except Exception as exc:
        return f"❌ Upload failed and could not save locally: {exc}"


@tool
async def upload_file(content: str, filename: str, description: str = "") -> str:
    """
    Upload any text-based file directly to the user's chat on Discord, Telegram, or Slack.
    Use this for: TXT, CSV, JSON, YAML, Markdown, HTML, Python/JS/SQL source code, log files, or any plain-text content.
    The 'content' parameter is the raw file content as a string.
    For binary files (PDF, Excel, images, ZIP), use run_code_and_upload instead.
    """
    if not filename:
        filename = "output.txt"
    if not content:
        return "❌ No content provided to upload."

    try:
        suffix = Path(filename).suffix or ".txt"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, encoding="utf-8", prefix="omni_"
        ) as f:
            f.write(content)
            tmp_path = f.name

        result = await _deliver_file(tmp_path, filename, description or f"📄 {filename}")

        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        return result

    except Exception as exc:
        log.error("upload_file failed: %s", exc, exc_info=True)
        return f"❌ File upload failed: {exc}"


@tool
async def deliver_sandbox_file(filepath: str, description: str = "", session_id: str = "default") -> str:
    """
    Deliver a generated file (PDF, Excel, PNG, CSV, etc.) directly from the Sandbox to the user's chat.
    Use this ONLY after you have successfully generated a file inside the sandbox.
    
    Args:
        filepath: The absolute path to the file inside the sandbox (e.g. /workspace/report.pdf).
        description: A brief message to accompany the file upload.
        session_id: Must match the session_id used in run_sandbox_command (usually 'default').
    """
    if not filepath:
        return "❌ No filepath provided."
        
    try:
        from tools.sandbox_tool import _get_pool
        pool = _get_pool()
        
        # CRITICAL: Apply the SAME SHA-256 hashing used in sandbox_tool.py
        # sandbox_tool uses: hashlib.sha256(session_id.encode()).hexdigest()[:16]
        session_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
        container_id = await pool.get_or_create(session_key)

        # 1. Verify the file actually exists inside the container
        check_proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "test", "-f", filepath,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await check_proc.wait()
        if check_proc.returncode != 0:
            # Try to list what's in the directory to help debugging
            parent = str(Path(filepath).parent)
            ls_proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container_id, "ls", "-la", parent,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            ls_out, _ = await ls_proc.communicate()
            dir_listing = ls_out.decode()[:500] if ls_out else "(empty or not found)"
            return (
                f"❌ File not found in sandbox at: `{filepath}`\n"
                f"Directory listing of `{parent}`:\n```\n{dir_listing}\n```\n"
                f"Generate the file first using `run_sandbox_command`."
            )

        filename = os.path.basename(filepath)
        size_proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "stat", "-c", "%s", filepath,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        size_out, _ = await size_proc.communicate()
        try:
            size_bytes = int(size_out.decode().strip())
        except Exception:
            size_bytes = 0

        # Check platform size limits
        max_size = 50 * 1024 * 1024  # 50MB Telegram limit
        if size_bytes > max_size:
            return f"❌ File too large: {size_bytes/1024/1024:.1f}MB (max 50MB). Compress or split the file first."
        
        # 2. Extract the file securely via docker cp
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as stable:
            stable_path = stable.name
            
        cp_proc = await asyncio.create_subprocess_exec(
            "docker", "cp", f"{container_id}:{filepath}", stable_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await cp_proc.wait()
        
        if cp_proc.returncode != 0:
            err = (await cp_proc.stderr.read()).decode()
            return f"❌ Failed to extract file from sandbox: {err}"
            
        # 3. Deliver via the unified target router (Discord, Telegram, etc.)
        result = await _deliver_file(stable_path, filename, description or f"📎 {filename}")
        
        try:
            os.unlink(stable_path)
        except Exception:
            pass
            
        return result
    except Exception as e:
        log.error("deliver_sandbox_file failed: %s", e)
        return f"❌ Sandbox delivery failed: {e}"
