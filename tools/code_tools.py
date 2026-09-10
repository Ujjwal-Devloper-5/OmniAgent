"""
Elite Coding Tools — OmniAgent Phase 4
═══════════════════════════════════════════════════════════════
Professional-grade file manipulation tools that operate inside
the Docker sandbox workspace. Every tool is idempotent, atomic,
and returns structured output the agent can parse.

Tools:
  view_file      — Line-sliced file viewer (no context flooding)
  edit_file      — Surgical string replacement with diff output
  grep_search    — ripgrep-powered content search across workspace
  find_files     — Glob/extension file discovery
  manage_file    — delete / move / copy / mkdir operations
"""

import hashlib
import re
from langchain_core.tools import tool
from tools.sandbox_tool import _get_pool, _truncate_output


def _validate_path(filepath: str) -> str:
    """Validate path to prevent path traversal and ensure it's relative."""
    if filepath.startswith("/") or ".." in filepath:
        raise ValueError(f"Invalid path: '{filepath}'. Use relative paths without '..' components.")
    # Allow letters, digits, dots, hyphens, underscores, and forward slashes
    safe_path = re.sub(r"[^\w.\-/]", "_", filepath)
    if not safe_path:
        raise ValueError("Invalid filename.")
    return safe_path

@tool
async def view_file(
    filepath: str,
    session_id: str = "default",
    start_line: int = 1,
    end_line: int = 0,  # 0 = until end of file
) -> str:
    """
    Read a file from the sandbox workspace with optional line range.
    
    Perfect for reading large files without flooding context — read just
    the relevant section using start_line and end_line.
    
    Args:
        filepath:   Path relative to /workspace (e.g. 'src/main.py')
        session_id: Session identifier (must match run_sandbox_command session)
        start_line: First line to return (1-indexed, default: 1)
        end_line:   Last line to return inclusive (0 = entire file from start_line)
    
    Returns:
        File content with line numbers prefixed. Example:
          1: import os
          2: import sys
          ...
    
    Examples:
        view_file('main.py')                    # view full file
        view_file('main.py', start_line=50, end_line=100)  # view lines 50-100
    """
    try:
        safe_path = _validate_path(filepath)
    except ValueError as e:
        return f"❌ {e}"

    session_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    pool = _get_pool()

    try:
        container_id = await pool.get_or_create(session_key)
    except Exception as exc:
        return f"❌ Could not access sandbox: {exc}"

    if end_line == 0:
        cmd = f"cat -n /workspace/{safe_path} | tail -n +{start_line}"
    else:
        cmd = f"sed -n '{start_line},{end_line}p' /workspace/{safe_path} | nl -ba -v{start_line}"

    stdout, stderr, exit_code = await pool.exec_in(container_id, cmd, timeout=10)

    if exit_code != 0:
        return f"❌ File not found or not readable: `/workspace/{safe_path}`\nError: {stderr}"
    
    output = _truncate_output(stdout, max_chars=8000)
    
    return f"📄 {safe_path} (lines {start_line}-{end_line if end_line != 0 else 'end'}):\n```\n{output}\n```"

@tool
async def edit_file(
    filepath: str,
    target_content: str,
    replacement_content: str,
    session_id: str = "default",
) -> str:
    """
    Surgically replace specific text in a file inside the sandbox workspace.
    
    Finds the FIRST occurrence of `target_content` and replaces it with
    `replacement_content`. Returns a diff showing exactly what changed.
    
    IMPORTANT: `target_content` must match EXACTLY (character-for-character,
    including whitespace and indentation). If the content is not found, the
    file is NOT modified and an error is returned.
    
    Args:
        filepath:            Path relative to /workspace
        target_content:      Exact string to find and replace (must be unique or first match)
        replacement_content: String to replace target_content with
        session_id:          Session identifier
    
    Returns:
        Unified diff of the changes made, or an error if target not found.
    
    Examples:
        edit_file('app.py', 'def old_function():', 'def new_function():')
    """
    try:
        safe_path = _validate_path(filepath)
    except ValueError as e:
        return f"❌ {e}"

    session_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    pool = _get_pool()

    try:
        container_id = await pool.get_or_create(session_key)
    except Exception as exc:
        return f"❌ Could not access sandbox: {exc}"

    import base64
    b64_target = base64.b64encode(target_content.encode("utf-8")).decode("ascii")
    b64_replacement = base64.b64encode(replacement_content.encode("utf-8")).decode("ascii")

    python_script = f"""
import base64, sys, difflib, pathlib
filepath = pathlib.Path('/workspace/{safe_path}')
if not filepath.exists():
    print(f"File not found")
    sys.exit(1)

content = filepath.read_text(encoding='utf-8')
target = base64.b64decode('{b64_target}').decode('utf-8')
replacement = base64.b64decode('{b64_replacement}').decode('utf-8')

if target not in content:
    print(f"Target content not found")
    sys.exit(2)

new_content = content.replace(target, replacement, 1)
filepath.write_text(new_content, encoding='utf-8')

diff = difflib.unified_diff(
    content.splitlines(keepends=True),
    new_content.splitlines(keepends=True),
    fromfile='a/{safe_path}',
    tofile='b/{safe_path}',
)
print("".join(diff))
"""
    b64_script = base64.b64encode(python_script.encode("utf-8")).decode("ascii")
    cmd = f"python3 -c \"import base64, os; exec(base64.b64decode('{b64_script}').decode('utf-8'))\""
    
    stdout, stderr, exit_code = await pool.exec_in(container_id, cmd, timeout=15)

    if exit_code == 1:
        return f"❌ File not found: `/workspace/{safe_path}`"
    elif exit_code == 2:
        return f"❌ Target content not found in `/workspace/{safe_path}`"
    elif exit_code != 0:
        return f"❌ Edit failed for `/workspace/{safe_path}`: {stderr}"

    return f"✅ Edit applied to {safe_path}\n\n```diff\n{stdout.strip()}\n```"


@tool
async def grep_search(
    query: str,
    session_id: str = "default",
    path: str = ".",
    is_regex: bool = False,
    case_insensitive: bool = True,
    file_pattern: str = "",
) -> str:
    """
    Search for text or patterns across files in the sandbox workspace.
    
    Uses ripgrep (rg) if available, falls back to grep. Returns matching
    lines with file path and line number.
    
    Args:
        query:            Text to search for (literal string or regex pattern)
        session_id:       Session identifier
        path:             Directory to search (default: entire workspace '.')
        is_regex:         If True, treat query as a regex pattern
        case_insensitive: If True (default), case-insensitive search
        file_pattern:     Optional glob pattern to filter files (e.g. '*.py', '*.json')
    
    Returns:
        List of matches in format: filepath:line_number:matching_content
        Capped at 50 matches to avoid flooding context.
    
    Examples:
        grep_search('def process_message')     # find function definitions
        grep_search('import', file_pattern='*.py')  # find all Python imports
        grep_search(r'\\d{3}-\\d{4}', is_regex=True)  # regex search
    """
    try:
        safe_path = _validate_path(path)
    except ValueError as e:
        return f"❌ {e}"

    session_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    pool = _get_pool()

    try:
        container_id = await pool.get_or_create(session_key)
    except Exception as exc:
        return f"❌ Could not access sandbox: {exc}"

    # Install ripgrep if not available (background/quietly)
    await pool.exec_in(container_id, "if ! command -v rg &> /dev/null; then apt-get install -y -qq ripgrep 2>/dev/null; fi", timeout=30)
    
    import shlex
    escaped_query = shlex.quote(query)
    
    rg_flags = "-n"
    if case_insensitive:
        rg_flags += " -i"
    if not is_regex:
        rg_flags += " -F"
    if file_pattern:
        escaped_pattern = shlex.quote(file_pattern)
        rg_flags += f" --glob {escaped_pattern}"
        
    grep_flags = "-rn"
    if case_insensitive:
        grep_flags += " -i"
    if not is_regex:
        grep_flags += " -F"
    else:
        grep_flags += " -E"
    
    if file_pattern:
        escaped_pattern = shlex.quote(file_pattern)
        grep_flags += f" --include={escaped_pattern}"

    cmd = (
        f"cd /workspace && "
        f"(if command -v rg &> /dev/null; then "
        f"rg {rg_flags} {escaped_query} {safe_path}; else "
        f"grep {grep_flags} {escaped_query} {safe_path}; fi) | head -50"
    )

    stdout, stderr, exit_code = await pool.exec_in(container_id, cmd, timeout=15)

    if exit_code == 0 and stdout:
        lines = stdout.strip().split('\n')
        return f"🔍 Found {len(lines)} matches:\n```\n{stdout.strip()}\n```"
    elif exit_code == 1:
        return "🔍 No matches found."
    else:
        return f"❌ Search failed: {stderr}"


@tool
async def find_files(
    pattern: str = "*",
    session_id: str = "default",
    path: str = ".",
    extensions: str = "",
    max_depth: int = 10,
) -> str:
    """
    Discover files in the sandbox workspace matching a glob pattern.
    
    Args:
        pattern:    Glob pattern to match filenames (e.g. '*.py', 'test_*', '*config*')
        session_id: Session identifier
        path:       Directory to search (default: entire workspace '.')
        extensions: Comma-separated file extensions to filter (e.g. 'py,js,ts')
        max_depth:  Maximum directory depth to search (default: 10)
    
    Returns:
        Sorted list of matching file paths with sizes and modification times.
        Capped at 100 results.
    
    Examples:
        find_files('*.py')                        # all Python files
        find_files('test_*', extensions='py')     # Python test files
        find_files('*', path='src/', max_depth=2) # files in src/
    """
    try:
        safe_path = _validate_path(path)
    except ValueError as e:
        return f"❌ {e}"

    session_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    pool = _get_pool()

    try:
        container_id = await pool.get_or_create(session_key)
    except Exception as exc:
        return f"❌ Could not access sandbox: {exc}"

    import shlex
    escaped_pattern = shlex.quote(pattern)
    
    ext_cond = ""
    if extensions:
        exts = [e.strip() for e in extensions.split(",") if e.strip()]
        if exts:
            ext_parts = [f"-name '*.{e}'" for e in exts]
            ext_cond = f" -\\( {' -o '.join(ext_parts)} -\\)"

    cmd = (
        f"find /workspace/{safe_path} -maxdepth {max_depth} -type f -name {escaped_pattern}{ext_cond} "
        f"-printf '%s\\t%TY-%Tm-%Td\\t%p\\n' | sort | head -100"
    )

    stdout, stderr, exit_code = await pool.exec_in(container_id, cmd, timeout=10)

    if exit_code == 0:
        if stdout.strip():
            # Clean up paths to be relative to /workspace/
            cleaned = stdout.replace("/workspace/", "")
            lines = cleaned.strip().split('\n')
            return f"📁 Found {len(lines)} files:\n```text\n{cleaned}\n```"
        else:
            return "📁 No files found."
    else:
        return f"❌ Find failed: {stderr}"


@tool
async def manage_file(
    action: str,
    filepath: str,
    session_id: str = "default",
    destination: str = "",
) -> str:
    """
    Perform file system operations inside the sandbox workspace.
    
    Args:
        action:      Operation to perform: 'delete', 'move', 'copy', 'mkdir', 'rename'
        filepath:    Source path relative to /workspace
        session_id:  Session identifier
        destination: Destination path (required for 'move', 'copy', 'rename')
    
    Returns:
        Success confirmation or descriptive error message.
    
    Actions:
        'delete' — Remove file or directory (recursive for directories)
        'move'   — Move file/directory to destination path
        'copy'   — Copy file/directory to destination path  
        'mkdir'  — Create directory at filepath (including parents)
        'rename' — Rename file (destination is new filename in same directory)
    
    Examples:
        manage_file('mkdir', 'src/components')
        manage_file('copy', 'template.py', destination='new_module.py')
        manage_file('delete', 'old_file.txt')
        manage_file('move', 'draft.py', destination='src/main.py')
    """
    valid_actions = {"delete", "move", "copy", "mkdir", "rename"}
    if action not in valid_actions:
        return f"❌ Invalid action '{action}'. Must be one of: {', '.join(valid_actions)}"

    try:
        safe_path = _validate_path(filepath)
    except ValueError as e:
        return f"❌ Source path error: {e}"

    safe_dest = ""
    if action in {"move", "copy", "rename"}:
        if not destination:
            return "❌ Destination is required for move/copy/rename."
        try:
            safe_dest = _validate_path(destination)
        except ValueError as e:
            return f"❌ Destination path error: {e}"

    if action == "delete" and safe_path in {".", "/workspace"}:
        return "❌ Cannot delete root workspace directory."

    session_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    pool = _get_pool()

    try:
        container_id = await pool.get_or_create(session_key)
    except Exception as exc:
        return f"❌ Could not access sandbox: {exc}"

    if action == "delete":
        cmd = f"rm -rf /workspace/{safe_path}"
        msg = f"Deleted `{safe_path}`"
    elif action == "move":
        cmd = f"mkdir -p $(dirname /workspace/{safe_dest}) && mv /workspace/{safe_path} /workspace/{safe_dest}"
        msg = f"Moved `{safe_path}` to `{safe_dest}`"
    elif action == "copy":
        cmd = f"mkdir -p $(dirname /workspace/{safe_dest}) && cp -r /workspace/{safe_path} /workspace/{safe_dest}"
        msg = f"Copied `{safe_path}` to `{safe_dest}`"
    elif action == "mkdir":
        cmd = f"mkdir -p /workspace/{safe_path}"
        msg = f"Created directory `{safe_path}`"
    elif action == "rename":
        import os
        dir_name = os.path.dirname(safe_path)
        if dir_name:
            full_dest = f"{dir_name}/{safe_dest}"
        else:
            full_dest = safe_dest
        cmd = f"mv /workspace/{safe_path} /workspace/{full_dest}"
        msg = f"Renamed `{safe_path}` to `{full_dest}`"

    stdout, stderr, exit_code = await pool.exec_in(container_id, cmd, timeout=10)

    if exit_code == 0:
        return f"✅ {msg}"
    else:
        return f"❌ Operation failed: {stderr}"
