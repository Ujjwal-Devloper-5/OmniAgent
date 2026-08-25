"""
File System Tools — OmniAgent v4
══════════════════════════════════
Tools for reading, writing, and listing files in the sandbox workspace.
The agent uses these to persist code, notes, and data across commands.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from core.logger import get_logger

log = get_logger(__name__)

_WORKSPACE = Path("/workspace")
_MAX_READ_BYTES = 50_000  # 50KB max read


def _safe_path(filename: str) -> Path:
    """Resolve and validate that a path stays within /workspace."""
    resolved = (_WORKSPACE / filename).resolve()
    if not str(resolved).startswith(str(_WORKSPACE)):
        raise ValueError(f"Path traversal blocked: {filename}")
    return resolved


@tool
def read_file(filename: str) -> str:
    """
    Read the contents of a file from the sandbox workspace (/workspace).
    Use this to read code, data, or notes that were previously written.
    Filename can include subdirectories (e.g. 'src/main.py').
    """
    try:
        path = _safe_path(filename)
        if not path.exists():
            return f"Error: File '{filename}' does not exist in /workspace."
        if path.is_dir():
            return f"Error: '{filename}' is a directory, not a file."
        size = path.stat().st_size
        if size > _MAX_READ_BYTES:
            return f"Error: File too large ({size} bytes). Max readable size is {_MAX_READ_BYTES} bytes."
        content = path.read_text(errors="replace")
        return f"Contents of /workspace/{filename}:\n```\n{content}\n```"
    except ValueError as e:
        return f"Security error: {e}"
    except Exception as e:
        return f"Error reading file: {e}"


@tool
def write_file(filename: str, content: str) -> str:
    """
    Write content to a file in the sandbox workspace (/workspace).
    Creates the file and any parent directories if they do not exist.
    Use this to save code, scripts, configs, or data.
    Filename can include subdirectories (e.g. 'src/utils.py').
    """
    try:
        path = _safe_path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        size = len(content.encode())
        return f"✅ Written {size} bytes to /workspace/{filename}"
    except ValueError as e:
        return f"Security error: {e}"
    except Exception as e:
        return f"Error writing file: {e}"


@tool
def list_files(directory: str = ".") -> str:
    """
    List all files and directories in the sandbox workspace (/workspace).
    Optionally specify a subdirectory. Shows file sizes.
    Use this to see what files exist before reading or writing.
    """
    try:
        path = _safe_path(directory)
        if not path.exists():
            return f"Directory '/workspace/{directory}' does not exist."
        if not path.is_dir():
            return f"'{directory}' is a file, not a directory."

        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        if not entries:
            return f"Directory '/workspace/{directory}' is empty."

        lines = [f"Contents of /workspace/{directory}:"]
        for entry in entries:
            if entry.is_dir():
                lines.append(f"  📁 {entry.name}/")
            else:
                size = entry.stat().st_size
                size_str = f"{size}B" if size < 1024 else f"{size//1024}KB"
                lines.append(f"  📄 {entry.name} ({size_str})")
        return "\n".join(lines)
    except ValueError as e:
        return f"Security error: {e}"
    except Exception as e:
        return f"Error listing directory: {e}"
