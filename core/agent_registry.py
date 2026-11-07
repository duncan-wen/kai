"""AgentRegistry — main agent 與所有 agent tab 的唯一 owner。

負責：配 tab id、記錄作用中的 Agent、依名稱查找、分發 MCP client。
不依賴 Textual；UI 只負責把 registry 的狀態畫出來。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from .agent import Agent
    from .mcp_client import MCPClient


class AgentRegistry:
    MAIN_ID = "chat"

    def __init__(self, main: "Agent") -> None:
        self._main = main
        self._tabs: dict[str, "Agent"] = {}
        self._next_idx = 0
        self._active_id = self.MAIN_ID
        self._mcp_client: "MCPClient | None" = None
        self._mcp_context: str = ""

    @property
    def main(self) -> "Agent":
        return self._main

    @property
    def active_id(self) -> str:
        return self._active_id

    @property
    def active(self) -> "Agent":
        return self.get(self._active_id) or self._main

    def get(self, tab_id: str) -> "Agent | None":
        if tab_id == self.MAIN_ID:
            return self._main
        return self._tabs.get(tab_id)

    def tabs(self) -> list[tuple[str, "Agent"]]:
        """所有 agent tab（不含 main），依開啟順序。"""
        return list(self._tabs.items())

    def tab_id_of(self, agent: "Agent") -> str | None:
        if agent is self._main:
            return self.MAIN_ID
        return next((tid for tid, a in self._tabs.items() if a is agent), None)

    def find_by_dir(self, agent_dir: "Path") -> str | None:
        """已開啟同一個 agent 目錄的 tab id（/load-agent 去重用）。"""
        return next((tid for tid, a in self._tabs.items() if a._agent_dir == agent_dir), None)

    def find(self, query: str) -> str:
        """依名稱找 Agent，回傳 tab id。

        規則（皆不分大小寫）：main / chat / main 的顯示名稱 → main；
        其餘先找 agent 目錄名稱或 nickname 完全相同的，再找唯一前綴。
        找不到或對到多個時 raise LookupError，訊息列出候選。
        """
        q = query.strip().lower()
        if q in ("main", "chat", self._main.display_name.lower()):
            return self.MAIN_ID

        def names(agent: "Agent") -> set[str]:
            result = {agent.display_name.lower()}
            if agent._agent_dir:
                result.add(agent._agent_dir.name.lower())
            return result

        exact = [tid for tid, a in self._tabs.items() if q in names(a)]
        if len(exact) == 1:
            return exact[0]
        candidates = exact or [
            tid for tid, a in self._tabs.items() if any(n.startswith(q) for n in names(a))
        ]
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            listed = "、".join(self._label(tid) for tid in candidates)
            raise LookupError(f"「{query}」對到多個 agent：{listed}，請指定更完整的名稱")
        opened = "、".join(self._label(tid) for tid in self._tabs) or "（無）"
        raise LookupError(f"找不到已開啟的 agent：{query}。目前開啟：{opened}")

    def _label(self, tab_id: str) -> str:
        agent = self._tabs[tab_id]
        dir_name = agent._agent_dir.name if agent._agent_dir else ""
        if dir_name and dir_name != agent.display_name:
            return f"{agent.display_name}（{dir_name}）"
        return agent.display_name

    def add(self, agent: "Agent") -> str:
        """登記一個新的 agent tab，回傳配給它的 tab id（遞增，關掉的 id 不再使用）。"""
        tab_id = f"agent-{self._next_idx}"
        self._next_idx += 1
        self._tabs[tab_id] = agent
        self.attach_mcp(agent)
        return tab_id

    def remove(self, tab_id: str) -> "Agent | None":
        """移除 agent tab；移除的是作用中的 tab 時退回 main。main 不可移除。"""
        if tab_id == self.MAIN_ID:
            raise ValueError("main agent 不能關閉")
        agent = self._tabs.pop(tab_id, None)
        if tab_id == self._active_id:
            self._active_id = self.MAIN_ID
        return agent

    def activate(self, tab_id: str) -> bool:
        """把 tab_id 設為作用中；不是 agent 的 id（例如檔案 tab）時不變，回傳 False。"""
        if self.get(tab_id) is None:
            return False
        self._active_id = tab_id
        return True

    def set_mcp(self, client: "MCPClient | None", context: str) -> None:
        """MCP 啟動、重啟或停止時呼叫：發給所有使用 MCP 的 Agent，並記住給之後開啟的 Agent。"""
        self._mcp_client = client
        self._mcp_context = context
        for agent in [self._main, *self._tabs.values()]:
            self.attach_mcp(agent)

    def attach_mcp(self, agent: "Agent") -> None:
        """依 agent.mcp_enabled 接上或拿掉目前的 MCP client（reload 改了 mcp_servers 後也呼叫）。"""
        if agent.mcp_enabled and self._mcp_client is not None:
            agent.mcp_client = self._mcp_client
            agent.mcp_context = self._mcp_context
        else:
            agent.mcp_client = None
            agent.mcp_context = ""
        agent._rebuild_system_prompt()
