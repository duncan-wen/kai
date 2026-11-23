import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from typing import TextIO

from loguru import logger
from mcp import ClientSession, StdioServerParameters
import httpx
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from pathlib import Path

from .config import BUNDLED_MCP_FILE, MCP_TOOL_TIMEOUT

GLOBAL_MCP_FILE = Path.home() / ".kai" / "mcp_servers.json"
LOCAL_MCP_FILE = Path(".kai") / "mcp_servers.json"  # cwd-relative，每次呼叫時才 resolve


class MCPClient:
    def __init__(self, servers: dict, errlog: TextIO = sys.stderr):
        # errlog is passed to stdio_client as the MCP subprocess stderr destination.
        # TUI callers should pass open(os.devnull, 'w') to suppress server startup
        # messages that would otherwise appear on the terminal behind Textual.
        self._errlog = errlog
        self._servers = servers
        self._servers = servers
        self._sessions: dict[str, ClientSession] = {}
        self._tool_to_server: dict[str, str] = {}
        self._tool_schemas: list[dict] = []
        self._stack = AsyncExitStack()

    @property
    def tools(self) -> list[dict]:
        return self._tool_schemas  # function calling schema，與後端無關

    @property
    def tool_names(self) -> set[str]:
        return set(self._tool_to_server)

    @property
    def tools_by_server(self) -> dict[str, list[dict]]:
        """回傳 {server_name: [{name, description}, ...]}，供 /mcp-list 顯示。"""
        result: dict[str, list[dict]] = {}
        for tool in self._tool_schemas:
            fn = tool["function"]
            server = self._tool_to_server.get(fn["name"], "unknown")
            result.setdefault(server, []).append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                }
            )
        return result

    @property
    def server_summary(self) -> dict[str, int]:
        """Connected server → tool count."""
        counts: dict[str, int] = {}
        for server in self._tool_to_server.values():
            counts[server] = counts.get(server, 0) + 1
        return counts

    async def start(self):
        await self._stack.__aenter__()
        for server_name, cfg in self._servers.items():
            if server_name.startswith("_"):
                continue
            try:
                transport_type = cfg.get("type", "stdio")
                if transport_type == "http":
                    url = cfg["url"]
                    raw_headers = cfg.get("headers", {})

                    def _expand(v: str) -> str:
                        import re

                        v = re.sub(
                            r"\$\{env:([^}]+)\}",
                            lambda m: os.getenv(m.group(1), m.group(0)),
                            v,
                        )
                        return os.path.expandvars(v)

                    headers = {k: _expand(v) for k, v in raw_headers.items()}
                    http_client = httpx.AsyncClient(headers=headers)
                    await self._stack.enter_async_context(http_client)
                    read, write, _ = await self._stack.enter_async_context(
                        streamable_http_client(url, http_client=http_client)
                    )
                else:
                    cfg_env = cfg.get("env")
                    merged_env = {**os.environ, **cfg_env} if cfg_env else None
                    params = StdioServerParameters(
                        command=cfg["command"],
                        args=cfg.get("args", []),
                        env=merged_env,
                    )
                    read, write = await self._stack.enter_async_context(
                        stdio_client(params, errlog=self._errlog)
                    )
                session: ClientSession = await self._stack.enter_async_context(
                    ClientSession(read, write)
                )
                await session.initialize()
                self._sessions[server_name] = session

                tools_result = await session.list_tools()
                for tool in tools_result.tools:
                    self._tool_to_server[tool.name] = server_name
                    self._tool_schemas.append(
                        {
                            "type": "function",
                            "function": {
                                "name": tool.name,
                                "description": tool.description or "",
                                "parameters": tool.inputSchema,
                            },
                        }
                    )
                logger.info(
                    f"MCP [{server_name}] 已連線，{len(tools_result.tools)} 個工具"
                )
            except BaseException as e:
                causes = e.exceptions if isinstance(e, BaseExceptionGroup) else [e]
                for cause in causes:
                    logger.warning(
                        f"MCP [{server_name}] 連線失敗：{type(cause).__name__}: {cause}"
                    )

    async def stop(self):
        await self._stack.aclose()

    def force_kill(self) -> None:
        """Synchronously kill all direct child processes (MCP stdio servers).

        Used when async stop() would deadlock due to anyio cross-task cancel scope.
        pkill -P <ppid> sends SIGTERM to all direct children of this process.
        """
        import signal
        import subprocess as sp

        my_pid = os.getpid()
        try:
            sp.run(
                ["pkill", "-TERM", "-P", str(my_pid)], capture_output=True, timeout=2
            )
        except Exception:
            # pkill not available — fall back to /proc scan (Linux only)
            try:
                from pathlib import Path

                for entry in Path("/proc").iterdir():
                    if not entry.name.isdigit():
                        continue
                    try:
                        stat = (entry / "stat").read_text()
                        ppid = int(stat.split(")", 1)[1].split()[1])
                        if ppid == my_pid:
                            os.kill(int(entry.name), signal.SIGTERM)
                    except (
                        ValueError,
                        IndexError,
                        PermissionError,
                        FileNotFoundError,
                        ProcessLookupError,
                    ):
                        pass
            except Exception as e:
                logger.warning(f"force_kill 失敗：{e}")

    async def call(self, tool_name: str, arguments: dict) -> str:
        server_name = self._tool_to_server[tool_name]
        session = self._sessions[server_name]
        logger.debug(f"MCP call [{server_name}] {tool_name}({arguments})")
        try:
            async with asyncio.timeout(MCP_TOOL_TIMEOUT):
                result = await session.call_tool(tool_name, arguments)
        except TimeoutError:
            logger.warning(f"MCP timeout [{tool_name}] 超過 {MCP_TOOL_TIMEOUT}s")
            return f"[MCP Timeout] {tool_name} 超過 {MCP_TOOL_TIMEOUT:.0f}s 未回應"
        parts = [item.text for item in result.content if hasattr(item, "text")]
        output = "\n".join(parts) or "[no output]"
        if result.isError:
            import json as _json

            args_preview = _json.dumps(arguments, ensure_ascii=False)[:300]
            logger.warning(f"MCP error [{tool_name}](args={args_preview}): {output}")
            return f"[MCP Error] {output}"
        else:
            logger.debug(f"MCP result [{tool_name}] len={len(output)}")
        return output


def _ensure_global_mcp() -> None:
    """首次啟動：若 ~/.kai/mcp_servers.json 不存在，從 package 內建檔複製。"""
    if GLOBAL_MCP_FILE.exists():
        return
    GLOBAL_MCP_FILE.parent.mkdir(parents=True, exist_ok=True)
    if BUNDLED_MCP_FILE.exists():
        import shutil

        shutil.copy(BUNDLED_MCP_FILE, GLOBAL_MCP_FILE)
        logger.info(f"初始化全域 MCP 設定：{GLOBAL_MCP_FILE}")


def _read_mcp_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})
    except Exception as e:
        logger.warning(f"載入 MCP 設定失敗 {path}：{e}")
        return {}


def load_mcp_servers() -> dict:
    """讀取 ~/.kai/mcp_servers.json（全域）+ cwd/.kai/mcp_servers.json（專案），shallow merge。
    同名 server 以專案設定優先；_prefix 停用邏輯保持不變。
    """
    _ensure_global_mcp()
    global_srv = _read_mcp_file(GLOBAL_MCP_FILE)
    local_srv = _read_mcp_file(Path(".kai") / "mcp_servers.json")
    return {**global_srv, **local_srv}
