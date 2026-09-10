"""
OmniAgent Automated Janitor Daemon
════════════════════════════════════
Responsible for sweeping expired Multi-Tenant storage and dangling
isolated Docker volumes to prevent data sprawl and host disk exhaustion.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from config import settings
from core.logger import get_logger

log = get_logger(__name__)


def _get_dir_last_modified(path: Path) -> float:
    """Get the most recent modification time of any file within the directory (recursive)."""
    try:
        mtimes = [path.stat().st_mtime]  # include the dir itself
        for f in path.rglob('*'):
            try:
                mtimes.append(f.stat().st_mtime)
            except (OSError, PermissionError):
                pass
        return max(mtimes) if mtimes else 0.0
    except (OSError, PermissionError):
        return 0.0

async def _sweep_reports() -> None:
    """Recursively delete tenant folders in /app/data/reports older than TTL."""
    reports_dir = Path("/app/data/reports")
    if not reports_dir.exists():
        return

    now = time.time()
    retention_seconds = settings.retention_reports_days * 86400
    deleted_count = 0

    for tenant_dir in reports_dir.iterdir():
        if not tenant_dir.is_dir():
            continue

        try:
            mtime = _get_dir_last_modified(tenant_dir)
            if now - mtime > retention_seconds:
                shutil.rmtree(tenant_dir)
                deleted_count += 1
                log.info("Janitor: Purged expired tenant directory | path=%s", tenant_dir.name)
        except Exception as e:
            log.warning("Janitor: Failed to purge tenant directory %s: %s", tenant_dir, e)

    if deleted_count > 0:
        log.info("Janitor: Cleared %d expired report directories.", deleted_count)


async def _sweep_sandbox_volumes() -> None:
    """Remove Docker volumes matching omniagent-ws-* that exceed TTL."""
    from datetime import timezone
    retention_seconds = settings.retention_sandbox_volumes_days * 86400
    now = datetime.now(tz=timezone.utc).timestamp()
    deleted_count = 0

    try:
        # Get list of our sandbox volumes
        proc = await asyncio.create_subprocess_exec(
            "docker", "volume", "ls", "-q", "--filter", "name=omniagent-ws-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        
        if not stdout:
            return
            
        volumes = stdout.decode().strip().split("\n")
        
        for vol in volumes:
            vol = vol.strip()
            if not vol:
                continue
                
            # Inspect creation date
            inspect_proc = await asyncio.create_subprocess_exec(
                "docker", "volume", "inspect", vol, "--format", "{{.CreatedAt}}",
                stdout=asyncio.subprocess.PIPE
            )
            inspect_out, _ = await inspect_proc.communicate()
            
            created_str = inspect_out.decode().strip()
            if not created_str:
                continue
                
            # Docker returns ISO8601 like '2026-09-05T01:30:56Z'
            try:
                from datetime import timezone
                # Handle both 'Z' suffix and '+00:00' format
                ts_clean = created_str.strip().replace('Z', '+00:00')
                created_dt = datetime.fromisoformat(ts_clean[:26] + '+00:00' if '+' not in ts_clean else ts_clean[:32])
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                created_ts = created_dt.timestamp()
            except (ValueError, TypeError) as e:
                log.warning("Janitor: Could not parse volume date: %s", created_str)
                continue
                
            if now - created_ts > retention_seconds:
                rm_proc = await asyncio.create_subprocess_exec(
                    "docker", "volume", "rm", vol,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await rm_proc.wait()
                if rm_proc.returncode == 0:
                    deleted_count += 1
                    log.info("Janitor: Purged expired sandbox volume | volume=%s", vol)
                else:
                    err = (await rm_proc.stderr.read()).decode().strip()
                    if "in use" not in err:  # Ignore volumes currently actively attached
                        log.warning("Janitor: Failed to remove volume %s: %s", vol, err)

        if deleted_count > 0:
            log.info("Janitor: Cleared %d expired sandbox volumes.", deleted_count)
            
    except Exception as e:
        log.error("Janitor: Sandbox sweep failed: %s", e)


async def _sweep_all() -> None:
    log.info("Janitor: Beginning scheduled multi-tenant sweep...")
    await _sweep_reports()
    await _sweep_sandbox_volumes()

async def run_janitor_loop() -> None:
    """Background daemon loop that runs every 24 hours."""
    log.info(
        "Janitor Daemon started | reports_ttl=%dd | volumes_ttl=%dd",
        settings.retention_reports_days,
        settings.retention_sandbox_volumes_days
    )
    
    # Run immediately on startup
    await _sweep_all()

    while True:
        try:
            # Then repeat every 24 hours
            await asyncio.sleep(86400)
            await _sweep_all()
            
        except asyncio.CancelledError:
            log.info("Janitor Daemon shutting down.")
            break
        except Exception as e:
            log.error("Janitor Daemon encountered critical error: %s", e)
            await asyncio.sleep(300)  # Sleep 5 mins on crash before retry
