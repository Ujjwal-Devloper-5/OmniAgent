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

_upload_callbacks: dict[str, object] = {}
_ctx_platform: str = ""
_ctx_target: str = ""


def register_upload_callback(platform: str, callback) -> None:
    """Called by platform adapters at startup to register their upload function.
    Callback signature: async fn(file_path: str, filename: str, target_id: str, description: str)
    """
    _upload_callbacks[platform] = callback
    log.info("Upload callback registered | platform=%s", platform)


def set_upload_context(platform: str, target_id: str) -> None:
    """Set the upload destination for the current request. Call this before process_message()."""
    global _ctx_platform, _ctx_target
    _ctx_platform = platform
    _ctx_target = str(target_id)


def get_upload_context() -> tuple[str, str]:
    return _ctx_platform, _ctx_target


def _reports_dir() -> Path:
    d = Path("/app/data/reports")
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _deliver_file(file_path: str, filename: str, description: str = "") -> str:
    """
    Core delivery: try platform upload, fall back to saving in /app/data/reports/.
    Returns a user-facing status string.
    """
    platform, target_id = get_upload_context()
    size_kb = os.path.getsize(file_path) / 1024

    if platform and target_id and platform in _upload_callbacks:
        try:
            cb = _upload_callbacks[platform]
            if asyncio.iscoroutinefunction(cb):
                await cb(file_path, filename, target_id, description)
            else:
                await asyncio.get_event_loop().run_in_executor(None, cb, file_path, filename, target_id, description)
            log.info("File delivered | platform=%s | filename=%s | size=%.1fKB", platform, filename, size_kb)
            return f"✅ **{filename}** ({size_kb:.1f} KB) delivered to {platform}!"
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
async def run_code_and_upload(python_code: str, output_filename: str, description: str = "") -> str:
    """
    Execute Python code that generates a file, then upload the result to the user's chat.
    Use for binary formats: PDF (weasyprint/reportlab), Excel (openpyxl), PowerPoint (python-pptx),
    images (Pillow/matplotlib), ZIP archives, or any format that requires code to generate.

    CRITICAL RULES your code MUST follow:
    1. Write the output file to exactly: /output/<filename>  (e.g. /output/report.pdf)
    2. The /output/ directory is pre-created for you — do NOT create it.
    3. Install any needed packages at the top with: import subprocess; subprocess.run(['pip', 'install', 'packagename', '-q'], check=True)
    4. Your code runs in an isolated subprocess with a 60-second timeout.
    5. If your code raises an exception, the upload will fail and the error will be shown.

    Example code for a PDF:
        from weasyprint import HTML
        HTML(string='<h1>Report</h1><p>Content</p>').write_pdf('/output/report.pdf')

    Example code for an Excel file:
        import subprocess; subprocess.run(['pip', 'install', 'openpyxl', '-q'], check=True)
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws['A1'] = 'Name'; ws['B1'] = 'Score'
        ws.append(['Alice', 95]); ws.append(['Bob', 87])
        wb.save('/output/scores.xlsx')
    """
    if not python_code or not python_code.strip():
        return "❌ No Python code provided."
    if not output_filename:
        return "❌ No output_filename specified."

    # Create isolated temp directory for /output/
    with tempfile.TemporaryDirectory(prefix="omni_codegen_") as tmpdir:
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        output_path = output_dir / output_filename

        # Write the code to a temp script, patching /output to the real tmpdir path
        patched_code = python_code.replace("/output/", str(output_dir) + "/")
        script_path = Path(tmpdir) / "gen_script.py"
        script_path.write_text(patched_code, encoding="utf-8")

        log.info("run_code_and_upload | script=%s | output=%s", script_path, output_path)

        try:
            proc = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [sys.executable, str(script_path)],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        cwd=tmpdir,
                    )
                ),
                timeout=65.0,
            )
        except asyncio.TimeoutError:
            return "❌ Code execution timed out (60s limit)."

        if proc.returncode != 0:
            stderr = (proc.stderr or "")[:800]
            stdout = (proc.stdout or "")[:400]
            return (
                f"❌ Code execution failed (exit code {proc.returncode}).\n"
                f"```\nSTDERR: {stderr}\nSTDOUT: {stdout}\n```"
            )

        # Check the output file was actually created
        if not output_path.exists():
            # Maybe the code used a different path — scan for any file in output_dir
            files = list(output_dir.iterdir())
            if files:
                output_path = files[0]
                output_filename = output_path.name
                log.info("run_code_and_upload | found output at: %s", output_path)
            else:
                return (
                    f"❌ Code ran successfully but did not create `/output/{output_filename}`.\n"
                    f"Make sure your code writes to `/output/{output_filename}`.\n"
                    f"STDOUT: {(proc.stdout or '')[:400]}"
                )

        size_kb = output_path.stat().st_size / 1024
        log.info("run_code_and_upload | file ready: %s (%.1f KB)", output_filename, size_kb)

        # Copy to a stable temp file before tmpdir is cleaned up
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(output_filename).suffix) as stable:
            stable_path = stable.name
        shutil.copy2(str(output_path), stable_path)

        result = await _deliver_file(stable_path, output_filename, description or f"📎 {output_filename}")

        try:
            os.unlink(stable_path)
        except Exception:
            pass

        # Include any stdout as bonus context
        stdout_note = f"\n\n_Code output: {proc.stdout[:200]}_" if proc.stdout and proc.stdout.strip() else ""
        return result + stdout_note
