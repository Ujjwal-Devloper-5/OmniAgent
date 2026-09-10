"""
Background Process Management Tools — OmniAgent Phase 4
═══════════════════════════════════════════════════════════
Allows the agent to run persistent background processes inside the
sandbox (web servers, build watchers, long-running scripts) and
manage them (check status, read logs, send input, kill).

Processes are tracked per session in a registry keyed by name.
"""

import hashlib
from typing import Any
from langchain_core.tools import tool
from tools.sandbox_tool import _get_pool

_PROCESS_REGISTRY: dict[str, int] = {}

@tool
async def spawn_background_process(
    name: str,
    command: str,
    session_id: str = "default",
    working_dir: str = "/workspace",
) -> str:
    """
    Start a persistent background process inside the sandbox.
    
    The process runs detached and continues until killed or the sandbox
    TTL expires. Logs are captured to /tmp/proc_{name}.log inside the sandbox.
    
    Args:
        name:        Unique identifier for this process (e.g. 'web_server', 'build_watcher')
        command:     Shell command to run (e.g. 'python3 app.py', 'npm run dev')
        session_id:  Session identifier
        working_dir: Working directory inside sandbox (default: /workspace)
    
    Returns:
        Confirmation with PID and log file location.
    
    Examples:
        spawn_background_process('server', 'python3 -m http.server 8080')
        spawn_background_process('build', 'npm run dev', working_dir='/workspace/frontend')
    """
    pool = _get_pool()
    session_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    
    try:
        container_id = await pool.get_or_create(session_key)
    except Exception as e:
        return f"❌ Sandbox error: {e}"

    # Build nohup command
    cmd = f"cd {working_dir} && nohup bash -c '{command}' > /tmp/proc_{name}.log 2>&1 & echo $!"
    
    stdout, stderr, exit_code = await pool.exec_in(container_id, cmd)
    
    if exit_code != 0:
        return f"❌ Failed to start process: {stderr}"
        
    try:
        pid = int(stdout.strip())
    except ValueError:
        return f"❌ Failed to parse PID from output: {stdout}"
        
    _PROCESS_REGISTRY[f"{session_id}:{name}"] = pid
    
    return (
        f"✅ Process '{name}' started (PID: {pid})\n"
        f"Logs: /tmp/proc_{name}.log\n"
        f"Check status: manage_process('status', '{name}', session_id='{session_id}')"
    )

@tool
async def manage_process(
    action: str,
    name: str,
    session_id: str = "default",
    input_text: str = "",
) -> str:
    """
    Manage a background process running inside the sandbox.
    
    Args:
        action:     'status', 'logs', 'kill', 'list', 'send_input'
        name:       Process name (as given to spawn_background_process). Ignored for 'list'.
        session_id: Session identifier
        input_text: Text to send to process stdin (only for 'send_input' action)
    
    Actions:
        'status'  — Check if process is running (shows PID and runtime)
        'logs'    — Read the last 100 lines from process log file
        'kill'    — Terminate the process gracefully (SIGTERM then SIGKILL after 5s)
        'list'    — List all background processes for this session
    
    Returns:
        Process status, logs, or confirmation of action taken.
    """
    pool = _get_pool()
    session_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    
    try:
        container_id = await pool.get_or_create(session_key)
    except Exception as e:
        return f"❌ Sandbox error: {e}"

    if action == "list":
        pids = []
        for key, pid in list(_PROCESS_REGISTRY.items()):
            if key.startswith(f"{session_id}:"):
                pids.append(str(pid))
        
        if not pids:
            return "No background processes running in this session."
            
        cmd = f"ps -p {','.join(pids)} -o pid,etime,cmd"
        stdout, stderr, exit_code = await pool.exec_in(container_id, cmd)
        return f"Background Processes:\n{stdout}"

    registry_key = f"{session_id}:{name}"
    pid = _PROCESS_REGISTRY.get(registry_key)
    
    if not pid:
        return f"❌ Process '{name}' not found in registry."

    if action == "status":
        cmd = f"ps -p {pid} -o pid,etime,cmd --no-headers"
        stdout, stderr, exit_code = await pool.exec_in(container_id, cmd)
        if exit_code == 0 and stdout.strip():
            return f"✅ Process '{name}' (PID {pid}) is running:\n{stdout.strip()}"
        else:
            return f"❌ Process '{name}' (PID {pid}) is not running."

    elif action == "logs":
        cmd = f"tail -100 /tmp/proc_{name}.log"
        stdout, stderr, exit_code = await pool.exec_in(container_id, cmd)
        if exit_code == 0:
            return f"Logs for '{name}':\n```\n{stdout}\n```"
        else:
            return f"❌ Failed to fetch logs: {stderr}"

    elif action == "kill":
        cmd = f"kill -TERM {pid}; sleep 2; kill -KILL {pid} 2>/dev/null; echo killed"
        stdout, stderr, exit_code = await pool.exec_in(container_id, cmd)
        _PROCESS_REGISTRY.pop(registry_key, None)
        return f"✅ Process '{name}' (PID {pid}) killed."

    elif action == "send_input":
        return "❌ send_input not implemented for nohup processes."
        
    else:
        return f"❌ Unknown action: {action}"
