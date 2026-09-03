"""
Platform file upload tools — enables the agent to upload files (PDFs, code, data)
directly to the user's chat on Discord, Telegram, or Slack.

How it works:
  1. Platform adapters register upload callbacks at startup via register_upload_callback()
  2. Platform adapters set the current context (platform + chat_id) per-request via set_upload_context()
  3. The agent calls generate_and_upload_pdf() or upload_text_file() as LangChain tools
  4. These tools call the registered callback to deliver the file
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool
from core.logger import get_logger

log = get_logger(__name__)

# ── Platform upload callbacks ─────────────────────────────────────────────────
_upload_callbacks: dict[str, object] = {}
_ctx_platform: str = ""
_ctx_target: str = ""


def register_upload_callback(platform: str, callback) -> None:
    """Called by platform adapters at startup to register their file upload function."""
    _upload_callbacks[platform] = callback
    log.info("Upload callback registered | platform=%s", platform)


def set_upload_context(platform: str, target_id: str) -> None:
    """Set the upload destination for the current request. Call this before process_message()."""
    global _ctx_platform, _ctx_target
    _ctx_platform = platform
    _ctx_target = str(target_id)


def get_upload_context() -> tuple[str, str]:
    """Get the current upload destination (platform, target_id)."""
    return _ctx_platform, _ctx_target


def _ensure_reports_dir() -> Path:
    reports = Path("/app/data/reports")
    reports.mkdir(parents=True, exist_ok=True)
    return reports


async def _do_upload(file_path: str, filename: str, description: str = "") -> str:
    """Attempt platform upload; fall back to saving to /app/data/reports/."""
    platform, target_id = get_upload_context()
    if platform and target_id and platform in _upload_callbacks:
        try:
            cb = _upload_callbacks[platform]
            if asyncio.iscoroutinefunction(cb):
                await cb(file_path, filename, target_id, description)
            else:
                cb(file_path, filename, target_id, description)
            return f"✅ <b>{filename}</b> uploaded to {platform} successfully!"
        except Exception as exc:
            log.warning("Platform upload failed, saving locally: %s", exc)

    # Fallback: save to reports dir
    reports = _ensure_reports_dir()
    dest = reports / filename
    try:
        import shutil
        shutil.copy2(file_path, str(dest))
        return f"✅ File saved to <code>/app/data/reports/{filename}</code> (direct upload unavailable)."
    except Exception as exc:
        return f"❌ Upload failed and could not save locally: {exc}"


@tool
async def generate_and_upload_pdf(title: str, content: str, filename: str = "") -> str:
    """Generate a beautiful PDF from title and markdown content, then upload it to the user's chat (Discord/Telegram/Slack). Use this when the user wants a report, document, or PDF output."""
    if not filename:
        safe_title = title.replace(' ', '_').replace('/', '-')[:50]
        filename = f"{safe_title}.pdf"
    elif not filename.endswith('.pdf'):
        filename += '.pdf'

    try:
        import markdown2
        from weasyprint import HTML
    except ImportError as e:
        return f"❌ PDF generation unavailable (missing library: {e}). Try asking for a text file instead."

    try:
        html_body = markdown2.markdown(
            content,
            extras=['tables', 'fenced-code-blocks', 'header-ids', 'strike', 'task_list']
        )

        full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=JetBrains+Mono&display=swap');
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: Inter, Arial, sans-serif; color: #1a1a2e; line-height: 1.7; font-size: 14px; }}
  .header {{ background: linear-gradient(135deg, #1e40af 0%, #3b82f6 100%); color: white; padding: 40px; margin-bottom: 40px; }}
  .header h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 8px; }}
  .header .subtitle {{ opacity: 0.85; font-size: 14px; }}
  .content {{ padding: 0 40px 40px 40px; }}
  h1 {{ font-size: 22px; color: #1e40af; border-bottom: 2px solid #bfdbfe; padding-bottom: 8px; margin: 32px 0 16px; }}
  h2 {{ font-size: 18px; color: #1e3a8a; margin: 24px 0 12px; }}
  h3 {{ font-size: 15px; color: #1d4ed8; margin: 20px 0 8px; }}
  p {{ margin: 12px 0; }}
  ul, ol {{ padding-left: 24px; margin: 12px 0; }}
  li {{ margin: 6px 0; }}
  code {{ font-family: 'JetBrains Mono', monospace; background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-size: 12px; color: #0f172a; }}
  pre {{ background: #0f172a; color: #e2e8f0; padding: 20px; border-radius: 8px; overflow-x: auto; margin: 16px 0; font-size: 12px; }}
  pre code {{ background: none; color: inherit; padding: 0; }}
  blockquote {{ border-left: 4px solid #3b82f6; padding: 12px 16px; background: #eff6ff; margin: 16px 0; border-radius: 0 8px 8px 0; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
  th {{ background: #1e40af; color: white; padding: 10px 14px; text-align: left; font-size: 13px; }}
  td {{ border: 1px solid #e2e8f0; padding: 10px 14px; font-size: 13px; }}
  tr:nth-child(even) {{ background: #f8fafc; }}
  .footer {{ margin-top: 48px; padding: 20px 40px; border-top: 1px solid #e2e8f0; color: #94a3b8; font-size: 11px; display: flex; justify-content: space-between; }}
</style>
</head>
<body>
<div class="header">
  <h1>{title}</h1>
  <div class="subtitle">Generated by OmniAgent AI Research System</div>
</div>
<div class="content">{html_body}</div>
<div class="footer">
  <span>OmniAgent — AI-Powered Research</span>
  <span>Confidential</span>
</div>
</body>
</html>"""

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False, prefix='omni_') as f:
            pdf_path = f.name

        HTML(string=full_html).write_pdf(pdf_path)
        size_kb = os.path.getsize(pdf_path) / 1024
        log.info("PDF generated | path=%s | size=%.1fKB", pdf_path, size_kb)

        result = await _do_upload(pdf_path, filename, f"📄 {title}")

        try:
            os.unlink(pdf_path)
        except Exception:
            pass

        return result

    except Exception as exc:
        log.error("PDF generation failed: %s", exc, exc_info=True)
        return f"❌ PDF generation failed: {exc}"


@tool
async def upload_text_file(content: str, filename: str = "output.txt", description: str = "") -> str:
    """Upload any text content (code, data, notes, markdown) as a file to the user's current platform. Use when the user wants to receive output as a downloadable file."""
    if not filename:
        filename = "output.txt"

    try:
        suffix = Path(filename).suffix or '.txt'
        with tempfile.NamedTemporaryFile(
            mode='w', suffix=suffix, delete=False, encoding='utf-8', prefix='omni_'
        ) as f:
            f.write(content)
            tmp_path = f.name

        result = await _do_upload(tmp_path, filename, description or f"📎 {filename}")

        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        return result

    except Exception as exc:
        log.error("Text file upload failed: %s", exc, exc_info=True)
        return f"❌ File upload failed: {exc}"
