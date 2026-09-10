"""
System Status Tool — OmniAgent Phase 4
═══════════════════════════════════════
Provides real-time system health metrics: CPU, RAM, disk, Docker,
and OmniAgent-specific status (active sessions, sandbox containers).
"""

from langchain_core.tools import tool
import asyncio
import os

async def _run_host_cmd(cmd: list[str], timeout: float = 5.0) -> str:
    """Run a command on the host (not in sandbox)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(errors="replace").strip()
    except Exception as exc:
        return f"(error: {exc})"

@tool
async def system_status(detail: str = "all") -> str:
    """
    Get real-time system health metrics for the host running OmniAgent.
    
    Args:
        detail: What to report: 'all', 'cpu', 'memory', 'disk', 'docker', 'agent'
    
    Returns:
        Formatted system status report.
    
    Examples:
        system_status()              # full report
        system_status('docker')      # Docker container status
        system_status('memory')      # RAM usage
    """
    out = []
    
    if detail in ("all", "cpu"):
        out.append("### CPU Usage 🖥️")
        cpu_usage = await _run_host_cmd(["sh", "-c", "top -bn1 | grep '%Cpu' | awk '{print $2}'"])
        cores = await _run_host_cmd(["nproc"])
        out.append(f"- Usage: {cpu_usage}%")
        out.append(f"- Cores: {cores}")
        out.append("")
        
    if detail in ("all", "memory"):
        out.append("### Memory Usage 🧠")
        mem = await _run_host_cmd(["free", "-h"])
        out.append("```\n" + mem + "\n```")
        out.append("")
        
    if detail in ("all", "disk"):
        out.append("### Disk Usage 💾")
        disk = await _run_host_cmd(["df", "-h", "/"])
        out.append("```\n" + disk + "\n```")
        out.append("")
        
    if detail in ("all", "docker"):
        out.append("### Docker Containers 🐳")
        ps = await _run_host_cmd(["docker", "ps", "--format", "table {{.Names}}\\t{{.Status}}\\t{{.Image}}"])
        stats = await _run_host_cmd(["docker", "stats", "--no-stream", "--format", "table {{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}"])
        out.append("#### Status")
        out.append("```\n" + ps + "\n```")
        out.append("#### Stats")
        out.append("```\n" + stats + "\n```")
        out.append("")
        
    if detail in ("all", "agent"):
        out.append("### Agent Status 🤖")
        containers = await _run_host_cmd(["docker", "ps", "--filter", "name=omniagent-sandbox", "--format", "{{.Names}}"])
        mem_info = await _run_host_cmd(["python3", "-c", "import psutil; p = psutil.Process(); print(p.memory_info().rss / 1e6, 'MB')"])
        out.append(f"- Sandbox Containers: {containers.replace(chr(10), ', ') if containers else 'None'}")
        out.append(f"- Process Memory: {mem_info}")
        out.append("")
        
    return "\n".join(out).strip()
