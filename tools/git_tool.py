"""
Git Tool — OmniAgent Phase 4
══════════════════════════════
Run git operations inside the sandbox workspace. 
Enables the agent to clone repositories, inspect diffs,
commit work, and manage branches without leaving the sandbox.
"""

import hashlib
from langchain_core.tools import tool
from tools.sandbox_tool import _get_pool

_ALLOWED_SUBCOMMANDS = {
    'clone', 'status', 'diff', 'add', 'commit', 'log', 'branch',
    'checkout', 'pull', 'push', 'init', 'remote', 'fetch', 'stash',
    'show', 'reset', 'merge', 'rebase', 'tag', 'config'
}

@tool
async def git_run(
    subcommand: str,
    session_id: str = "default",
    args: str = "",
    working_dir: str = "/workspace",
) -> str:
    """
    Execute a git subcommand inside the sandbox workspace.
    
    Runs as: git {subcommand} {args}
    
    Supported subcommands: clone, status, diff, add, commit, log, branch,
    checkout, pull, push, init, remote, fetch, stash, show, reset, merge, rebase, tag, config
    
    Args:
        subcommand:  Git subcommand (e.g. 'status', 'clone', 'diff')
        session_id:  Session identifier
        args:        Additional arguments (e.g. 'https://github.com/...' for clone,
                     '-m "message"' for commit)
        working_dir: Working directory (default: /workspace)
    
    Returns:
        Git command output.
    
    Examples:
        git_run('clone', args='https://github.com/user/repo.git')
        git_run('status')
        git_run('add', args='.')
        git_run('commit', args='-m "Initial commit"')
        git_run('log', args='--oneline -10')
    """
    if subcommand not in _ALLOWED_SUBCOMMANDS:
        return f"❌ Subcommand '{subcommand}' not allowed"

    pool = _get_pool()
    session_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    
    try:
        container_id = await pool.get_or_create(session_key)
    except Exception as e:
        return f"❌ Sandbox error: {e}"

    # Ensure git is installed and configured
    setup_cmd = (
        "which git || apt-get install -y -qq git 2>/dev/null && "
        "git config --global user.email 'agent@omniagent.local' && "
        "git config --global user.name 'OmniAgent'"
    )
    await pool.exec_in(container_id, setup_cmd, timeout=30)
    
    # Run git command
    cmd = f"cd {working_dir} && git {subcommand} {args}"
    stdout, stderr, exit_code = await pool.exec_in(container_id, cmd, timeout=120)
    
    output = stdout
    if stderr:
        output += "\n" + stderr
        
    if len(output) > 4000:
        output = output[:4000] + "\n... (output truncated)"
        
    if exit_code == 0:
        return f"✅ Git {subcommand} succeeded:\n```\n{output.strip()}\n```"
    else:
        return f"❌ Git {subcommand} failed (exit {exit_code}):\n```\n{output.strip()}\n```"
