"""
Enterprise MCP Client Manager — OmniAgent Phase 5
══════════════════════════════════════════════════════════════════════════
Key features:
  • Dual config format: mcp.json (official) OR env vars (legacy)
  • Per-server fault isolation: one failure never kills others
  • Per-server circuit breaker: 3 failures -> disabled, others keep running
  • Graceful shutdown: no zombie processes
  • Safe environment: only whitelisted env vars to subprocesses
  • Hot-reload ready: reinitialize() without restart

Config precedence:
  1. mcp.json file (official MCP standard)
  2. MCP_SERVERS env var (JSON string)
  3. Per-server env vars MCP_{NAME}_COMMAND etc. (legacy)
"""

from __future__ import annotations
import asyncio
import os
import json
from dataclasses import dataclass, field
from typing import Any, Optional
from core.logger import get_logger

log = get_logger(__name__)

_SAFE_ENV_KEYS = {
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "LC_CTYPE",
    "TZ", "NODE_ENV", "NODE_PATH", "NPM_CONFIG_PREFIX",
    "TMPDIR", "TEMP", "TMP", "TERM"
}

@dataclass
class _ServerRecord:
    name: str
    config: dict
    tools: list = field(default_factory=list)
    failures: int = 0
    disabled: bool = False
    client: Any = None

    @property
    def is_healthy(self) -> bool:
        return not self.disabled and self.failures < 3

    def record_failure(self, exc: Exception) -> None:
        self.failures += 1
        log.warning(f"MCP: Server '{self.name}' failed ({self.failures}/3): {exc}")
        if self.failures >= 3:
            self.disabled = True
            log.error(f"MCP: Circuit breaker tripped for server '{self.name}'. Disabled.")

class MCPManager:
    def __init__(self) -> None:
        self._servers: dict[str, _ServerRecord] = {}
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._init_lock:
            if self._initialized:
                return
            configs = self._load_configs()
            if not configs:
                log.info("MCP: No servers configured.")
                self._initialized = True
                return
                
            log.info(f"MCP: Initializing {len(configs)} server(s): {list(configs.keys())}")
            
            await asyncio.gather(*[self._connect_server(name, cfg) for name, cfg in configs.items()])
            self._initialized = True

    def _load_configs(self) -> dict[str, dict]:
        configs: dict[str, dict] = {}
        
        # 1. Try mcp.json
        mcp_json_paths = ["/app/mcp.json", "mcp.json"]
        for path in mcp_json_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r") as f:
                        data = json.load(f)
                    servers = data.get("mcpServers") or data.get("servers") or {}
                    for name, cfg in servers.items():
                        configs[name] = self._normalize_config(cfg)
                    return configs
                except Exception as e:
                    log.error(f"MCP: Failed to parse {path}: {e}")
                    
        # 2. Try MCP_SERVERS env var as JSON
        mcp_servers_env = os.getenv("MCP_SERVERS", "").strip()
        if mcp_servers_env and mcp_servers_env.startswith("{"):
            try:
                servers = json.loads(mcp_servers_env)
                for name, cfg in servers.items():
                    configs[name] = self._normalize_config(cfg)
                return configs
            except Exception as e:
                log.error(f"MCP: Failed to parse MCP_SERVERS JSON: {e}")
                
        # 3. Legacy per-server env vars
        if mcp_servers_env:
            # Maybe it's comma-separated names
            server_names = [s.strip() for s in mcp_servers_env.split(",") if s.strip()]
            for name in server_names:
                prefix = f"MCP_{name.upper()}_"
                transport = os.getenv(f"{prefix}TRANSPORT", "stdio").lower()
                if transport == "stdio":
                    command = os.getenv(f"{prefix}COMMAND", "").strip()
                    if command:
                        args_raw = os.getenv(f"{prefix}ARGS", "").strip()
                        args = [a.strip() for a in args_raw.split(",") if a.strip()] if args_raw else []
                        cfg = {"command": command, "args": args}
                        configs[name] = self._normalize_config(cfg)
                elif transport in ("http", "sse", "streamable_http"):
                    url = os.getenv(f"{prefix}URL", "").strip()
                    if url:
                        cfg = {"transport": "streamable_http", "url": url}
                        configs[name] = self._normalize_config(cfg)
        return configs

    def _normalize_config(self, cfg: dict) -> dict:
        norm = {}
        transport = cfg.get("transport", "stdio")
        if transport == "stdio":
            norm["transport"] = "stdio"
            norm["command"] = cfg.get("command", "")
            norm["args"] = cfg.get("args", [])
            
            # Safe base env
            safe_base = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
            server_env = cfg.get("env", {})
            
            env = dict(safe_base)
            env.update(server_env)
            norm["env"] = env
        elif transport in ("http", "sse", "streamable_http"):
            norm["transport"] = "streamable_http"
            norm["url"] = cfg.get("url", "")
        return norm

    async def _connect_server(self, name: str, cfg: dict) -> None:
        record = _ServerRecord(name=name, config=cfg)
        self._servers[name] = record
        
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
            
            # MultiServerMCPClient expects a dict of configs {name: config}
            client = MultiServerMCPClient({name: cfg})
            record.client = client
            
            # Call get_tools with 30s timeout
            tools = await asyncio.wait_for(client.get_tools(), timeout=30.0)
            record.tools = tools
            
            log.info(f"MCP: Server '{name}' loaded {len(tools)} tools.")
        except Exception as exc:
            record.record_failure(exc)

    def get_tools(self) -> list[Any]:
        tools = []
        for record in self._servers.values():
            if record.is_healthy:
                tools.extend(record.tools)
        return tools

    def get_server_status(self) -> dict:
        status = {}
        for name, record in self._servers.items():
            tool_names = [getattr(t, "name", str(t)) for t in record.tools]
            status[name] = {
                "healthy": record.is_healthy,
                "disabled": record.disabled,
                "failures": record.failures,
                "tools_count": len(record.tools),
                "tool_names": tool_names
            }
        return status

    async def shutdown(self) -> None:
        async with self._init_lock:
            for name, record in self._servers.items():
                if record.client:
                    try:
                        # Attempt to close the client gracefully
                        if hasattr(record.client, "aclose"):
                            await asyncio.wait_for(record.client.aclose(), timeout=5.0)
                        elif hasattr(record.client, "close"):
                            if asyncio.iscoroutinefunction(record.client.close):
                                await asyncio.wait_for(record.client.close(), timeout=5.0)
                            else:
                                record.client.close()
                    except Exception as e:
                        log.warning(f"MCP: Error shutting down server '{name}': {e}")
            
            self._servers.clear()
            self._initialized = False
            log.info("MCP: Shutdown complete")

    async def reinitialize(self) -> None:
        await self.shutdown()
        await self.initialize()

_manager: Optional[MCPManager] = None

def get_mcp_manager() -> MCPManager:
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager

async def initialize_mcp() -> None:
    await get_mcp_manager().initialize()

def get_mcp_tools() -> list[Any]:
    return get_mcp_manager().get_tools()

async def shutdown_mcp() -> None:
    await get_mcp_manager().shutdown()

async def reinitialize_mcp() -> None:
    await get_mcp_manager().reinitialize()
