"""ChatApp 狀態群組 — 將 ChatApp.__init__ 的零散 self._xxx 依語意分組。

每個 dataclass 代表一個狀態域，掛在 ChatApp 上以具名屬性存取：
  self.session    — SessionState
  self.input_state — InputState
  self.ui_state   — UIState
  self.tab_state  — TabState

asyncio 生命週期物件（_mcp_task、_bash_proc、_tts_future 等）
不屬於任何狀態域，仍以 self._xxx 存放於 ChatApp。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SessionState:
    """目錄導航狀態。session 名稱與 resume 狀態在磁碟上，見 core.session_store。"""

    prev_cwd: str = ""


@dataclass
class InputState:
    """送出 / 取消串流之間的輸入暫存（輸入歷史由 widgets.prompt_bar.PromptBar 持有）。"""

    last_send_text: str = ""
    restore_input: bool = False
    last_ephemeral: list[dict] | None = None


@dataclass
class UIState:
    """不需要畫到畫面上的 UI 旗標（會顯示的狀態是 ChatApp 的 reactive）。"""

    greeting_enabled: bool = True


@dataclass(frozen=True)
class AgentView:
    """作用中 Agent 的顯示快照：sidebar / cap-bar / prompt / subtitle 都從這裡畫。

    ChatApp.agent_view 是 reactive；值相等就不觸發 watcher，所以重算很便宜。
    """

    display_name: str = ""
    model: str = ""
    supports_thinking: bool = False
    supports_vision: bool = False
    thinking_on: bool = False
    mcp: tuple[tuple[str, int], ...] | None = None
    """(server, 工具數)；None 代表 MCP 還在初始化。"""
    session_name: str = ""


@dataclass
class TabState:
    """Tab 相關的 pending 工具排程。檔案 tab 由 widgets.center_tabs 管理，agent tab 由 core.agent_registry 管理。"""

    pending_open_agent: str | None = None
