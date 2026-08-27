"""
MCP Client Manager — OmniAgent v4
Connects to MCP servers defined in .env, discovers tools, wraps as LangChain tools.
"""
from __future__ import annotations
import asyncio
import os
from typing import Any, Optional
from core.logger import get_logger
log = get_logger(__name__)

class MCPManager:
    def __init__(self) -> None:
        self._tools: list[Any] = []
        self._clients: list[Any] = []
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._init_lock:
            if self._initialized:
                return
            await self._load_all_servers()
            self._initialized = True

    async def _load_all_servers(self) -> None:
        mcp_servers_raw = os.getenv("MCP_SERVERS", "").strip()
        if not mcp_servers_raw:
            log.info("MCP: No MCP_SERVERS configured — MCP integration disabled")
            return
        server_names = [s.strip() for s in mcp_servers_raw.split(",") if s.strip()]
        log.info("MCP: Initializing %d server(s): %s", len(server_names), server_names)
        server_configs: dict[str, dict] = {}
        for name in server_names:
            config = self._parse_server_config(name)
            if config:
                server_configs[name] = config
        if not server_configs:
            return
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
            client = MultiServerMCPClient(server_configs)
            tools = await client.get_tools()
            self._tools.extend(tools)
            self._clients.append(client)
            tool_names = [getattr(t, "name", str(t)) for t in tools]
            log.info("MCP: Loaded %d tools: %s", len(tools), tool_names)
        except ImportError:
            log.error("MCP: langchain-mcp-adapters not installed. Run: pip install langchain-mcp-adapters mcp")
        except Exception as exc:
            log.error("MCP: Failed to initialize: %s", exc, exc_info=True)

    def _parse_server_config(self, server_name: str) -> Optional[dict]:
        prefix = f"MCP_{server_name.upper()}_"
        transport = os.getenv(f"{prefix}TRANSPORT", "stdio").lower()
        if transport == "stdio":
            command = os.getenv(f"{prefix}COMMAND", "").strip()
            if not command:
                return None
            args_raw = os.getenv(f"{prefix}ARGS", "").strip()
            args = [a.strip() for a in args_raw.split(",") if a.strip()] if args_raw else []
            return {"transport": "stdio", "command": command, "args": args, "env": dict(os.environ)}
        elif transport in ("http", "sse", "streamable_http"):
            url = os.getenv(f"{prefix}URL", "").strip()
            return {"transport": "streamable_http", "url": url} if url else None
        return None

    def get_tools(self) -> list[Any]:
        return list(self._tools)

    def is_available(self) -> bool:
        return len(self._tools) > 0

    async def shutdown(self) -> None:
        self._clients.clear()
        log.info("MCP: Shutdown complete")

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
