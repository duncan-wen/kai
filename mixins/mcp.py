"""MCPMixin — /mcp-list, /mcp-reload, /mcp-add, /mcp-del 指令邏輯。"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app import ChatApp as _MixinBase
else:
    _MixinBase = object  # type: ignore[assignment,misc]


class MCPMixin(_MixinBase):
    async def _do_mcp_list(self) -> None:
        from widgets.mcp_list import MCPListScreen

        mcp = self.agent.mcp_client
        if not mcp or not mcp.tool_names:
            self.notify("目前沒有連線中的 MCP server", severity="warning")
            return
        self.push_screen(MCPListScreen(mcp.tools_by_server))

    async def _do_mcp_reload(self) -> None:
        self.notify("🔄 重新連線 MCP...", severity="information")
        if self._mcp_done:
            self._mcp_done.set()
        if self._mcp_task and not self._mcp_task.done():
            try:
                await asyncio.wait_for(self._mcp_task, timeout=3.0)
            except BaseException:
                self._mcp_task.cancel()
        self._mcp_done = asyncio.Event()
        self._mcp_task = asyncio.get_event_loop().create_task(self._mcp_lifecycle())
        self.notify("✅ MCP 重新載入完成", severity="information")

    async def _do_mcp_add(self, arg: str) -> None:
        from core.mcp_client import GLOBAL_MCP_FILE

        if not arg:
            self.notify(
                "用法：/mcp-add <name> <command> [args...]  或  /mcp-add <name> --url <url>",
                severity="warning",
            )
            return
        parts = arg.split()
        if len(parts) < 2:
            self.notify(
                "用法：/mcp-add <name> <command> [args...]  或  /mcp-add <name> --url <url>",
                severity="warning",
            )
            return
        name, *rest = parts
        data: dict = {}
        if GLOBAL_MCP_FILE.exists():
            try:
                data = json.loads(GLOBAL_MCP_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        if rest[0] == "--url":
            if len(rest) < 2:
                self.notify("用法：/mcp-add <name> --url <url>", severity="warning")
                return
            entry: dict = {"type": "http", "url": rest[1]}
        else:
            command, *extra_args = rest
            entry = {"command": command, "args": extra_args}
        data.setdefault("mcpServers", {})[name] = entry
        GLOBAL_MCP_FILE.parent.mkdir(parents=True, exist_ok=True)
        GLOBAL_MCP_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.notify(
            f"✅ 已新增 {name} 到 ~/.kai/mcp_servers.json，執行 /mcp-reload 生效",
            severity="information",
        )

    async def _do_mcp_del(self, arg: str) -> None:
        from core.mcp_client import GLOBAL_MCP_FILE

        if not arg:
            self.notify("用法：/mcp-del <name>", severity="warning")
            return
        name = arg.strip()
        if not GLOBAL_MCP_FILE.exists():
            self.notify("~/.kai/mcp_servers.json 不存在", severity="error")
            return
        try:
            data = json.loads(GLOBAL_MCP_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            self.notify(f"讀取失敗：{e}", severity="error")
            return
        servers = data.get("mcpServers", {})
        if name not in servers:
            self.notify(
                f"找不到 server：{name}（只能刪除 ~/.kai/mcp_servers.json 內的 server）",
                severity="error",
            )
            return
        del servers[name]
        GLOBAL_MCP_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.notify(
            f"✅ 已從 ~/.kai/mcp_servers.json 移除 {name}，執行 /mcp-reload 生效",
            severity="information",
        )
