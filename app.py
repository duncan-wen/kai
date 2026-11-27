"""Textual TUI for kai.  Run: uv run python app.py"""

from __future__ import annotations

import asyncio
import os
import re
import signal
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.worker import Worker

from loguru import logger
from textual import work
from textual.app import App, ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import (
    Footer,
    Header,
    Static,
    TabPane,
)

from core.agent import Agent
from core.agent_registry import AgentRegistry
from core.app_state import AgentView, InputState, SessionState, TabState, UIState
from core.model_stream import CancelToken
import core.config as _kai_cfg
from core.config import apply_kai_config, ensure_kai_dir, load_kai_config
from core.mcp_client import MCPClient, load_mcp_servers
from core.skills import discover_skills
from mixins.agents import AgentsMixin
from mixins.app_tools import AppToolsMixin
from mixins.commands import CommandsMixin
from mixins.bash import BashMixin
from mixins.mcp import MCPMixin
from mixins.skills import SkillsMixin
from mixins.session import SessionMixin
from mixins.streaming import StreamingMixin
from widgets.chat import (
    CapBar,
)
from widgets.center_tabs import CenterTabs
from widgets.chat_log import ChatLog
from widgets.commands import ChatCommands
from widgets.input import HistoryInput
from widgets.prompt_bar import PromptBar
from widgets.sidebar import ResizeHandle, Sidebar
from widgets.tree import FileTree
from widgets.utils import _IMAGE_EXTS

logger.remove()
# logger.add 在 main block 的 os.chdir 之後才真正加入，這裡只移除預設 handler


async def _fetch_db_schema(mcp: MCPClient) -> str:
    """查詢資料庫 table 清單，產生給模型的 schema hint。"""
    sql = (
        "SELECT hypertable_name AS table_name, 'hypertable' AS type "
        "FROM timescaledb_information.hypertables "
        "UNION ALL "
        "SELECT relname, 'table' FROM pg_class cl "
        "JOIN pg_namespace ns ON ns.oid = cl.relnamespace "
        "WHERE ns.nspname = 'public' AND cl.relkind = 'r' "
        "AND relname NOT IN (SELECT hypertable_name FROM timescaledb_information.hypertables) "
        "AND relname NOT LIKE '%backup%' "
        "ORDER BY table_name"
    )
    for tool_name in ("query", "execute_query", "pg_query", "read_query"):
        if tool_name in mcp.tool_names:
            try:
                result = await mcp.call(tool_name, {"sql": sql})
                return f"可查詢的資料表：\n{result}"
            except Exception as e:
                logger.warning(f"schema 查詢失敗（{tool_name}）：{e}")
                break
    return ""


class AgentMessage(Message):
    """agent 之間透過 send_to_agent 傳遞的訊息（雙向）。"""

    def __init__(self, sender: str, target: str, content: str) -> None:
        super().__init__()
        self.sender = sender
        self.target = target  # "main" / "chat" 或 agent 暱稱
        self.content = content


class ChatApp(
    CommandsMixin,
    MCPMixin,
    SkillsMixin,
    AgentsMixin,
    AppToolsMixin,
    SessionMixin,
    BashMixin,
    StreamingMixin,
    App[None],
):
    CSS_PATH = "app.tcss"
    COMMANDS = App.COMMANDS | {ChatCommands}

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出", priority=True),
        Binding("ctrl+p", "command_palette", "命令"),
        Binding("ctrl+l", "clear_screen", "清除畫面"),
        Binding("ctrl+t", "toggle_file_tree", "檔案樹"),
        Binding("ctrl+b", "toggle_sidebar", "側邊欄"),
        Binding("ctrl+w", "close_tab", "關閉標籤", priority=True),
        Binding(
            "ctrl+shift+left", "move_tab_left", "Tab 左移", priority=True, show=False
        ),
        Binding(
            "ctrl+shift+right", "move_tab_right", "Tab 右移", priority=True, show=False
        ),
        Binding("escape", "cancel_stream", "取消", priority=True),
    ]

    # ── 會畫到畫面上的狀態：widget 透過 data_bind / watcher 自己更新 ──────────
    debug_tools: reactive[bool] = reactive(False, init=False)
    """工具除錯模式（綁到 prompt 的 debug 標記）。"""
    think_override: reactive[bool | None] = reactive(None, init=False)
    """使用者手動切的 thinking；None = 跟模型預設。"""
    agent_view: reactive[AgentView] = reactive(AgentView, init=False)
    """作用中 Agent 的顯示快照；改了 agent 狀態後呼叫 refresh_agent_view()。"""
    active_tab: reactive[str] = reactive("chat", init=False)
    """畫面上的 center tab id（含檔案 tab）。"""
    busy_tabs: reactive[tuple[str, ...]] = reactive((), init=False)
    """有工作在跑的 tab id；可重複（同一 tab 同時有兩件工作）。用 mark_busy / unmark_busy 改。"""
    prompt_locked: reactive[bool] = reactive(False, init=False)
    """目前 tab 是否忙碌（compute_prompt_locked）；綁到 PromptBar.locked → HistoryInput.locked。"""

    def __init__(self) -> None:
        super().__init__()
        # Textual 8 的 MarkdownTableCellContents 在每個 header cell 呼叫 .with_tooltip()，
        # Tooltip 掛在 Screen._tooltips 層，累積多了會干擾佈局。直接 patch 成 no-op。
        try:
            from textual.widgets._markdown import MarkdownTableCellContents

            MarkdownTableCellContents.with_tooltip = lambda self, *a, **kw: self  # noqa: ARG005
        except Exception:
            pass
        # main agent 與所有 agent tab 的唯一 owner；self.agent 是 self.agents.active 的捷徑
        self.agents = AgentRegistry(Agent(model=_kai_cfg.MODEL))
        self._inject_app_tools(self.agent)
        self.scroll_sensitivity_y = 5.0
        self._at_mention_re = re.compile(r"(?:(?<=\s)|^)@(\S+)")
        self._mcp_task: asyncio.Task | None = None
        self._mcp_done: asyncio.Event | None = None
        self._last_file_copy: dict | None = None
        self._bash_proc: asyncio.subprocess.Process | None = None
        self._bash_worker: "Worker | None" = None  # type: ignore[name-defined]
        self.session = SessionState(prev_cwd=os.getcwd())
        self.input_state = InputState()
        self.ui_state = UIState()
        self.set_reactive(ChatApp.debug_tools, _kai_cfg.DEBUG_TOOLS)
        self._mcp_initializing = True
        self.tab_state = TabState()
        # 進行中 turn 的取消旗標：(tab_id, CancelToken)，由 mark_busy / unmark_busy 維護
        self._turn_cancels: list[tuple[str, CancelToken]] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="app-grid"):
            yield FileTree(id="file-tree")
            yield ResizeHandle("file-tree", id="file-tree-handle")
            with CenterTabs(initial="chat", id="center-tabs"):
                with TabPane("💬 Chat", id="chat"):
                    with ChatLog(id="messages"):
                        yield Static(
                            "[dim]輸入訊息開始對話[/dim]  ·  "
                            "[dim]Ctrl+P[/dim] 命令  "
                            "[dim]Ctrl+T[/dim] 檔案樹  "
                            "[dim]Ctrl+B[/dim] 側邊欄  "
                            "[dim]Ctrl+W[/dim] 關閉標籤  "
                            "[dim]右鍵 AI 訊息[/dim] 選取複製",
                            id="welcome",
                            markup=True,
                        )
            yield ResizeHandle("sidebar", id="sidebar-handle", right_anchor=True)
            yield Sidebar(id="sidebar").data_bind(view=ChatApp.agent_view)
        yield PromptBar(id="input-row").data_bind(
            debug_mark=ChatApp.debug_tools, locked=ChatApp.prompt_locked
        )
        yield CapBar(id="cap-bar").data_bind(view=ChatApp.agent_view)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "kai"
        self.sub_title = _kai_cfg.MODEL
        # 讓主 chat AI 能呼叫 open_agent 工具開啟副 agent tab
        _app = self

        async def _open_agent_tool(query: str) -> str:
            from core.agent_factory import find_agent, list_agents

            dir_name = find_agent(query)
            if not dir_name:
                agents = list_agents()
                names = "、".join(a["name"] for a in agents) or "（無）"
                return (
                    f"找不到符合「{query}」的 agent。"
                    f"請從以下名稱中選擇一個，再呼叫 open_agent：{names}"
                )
            _app.tab_state.pending_open_agent = dir_name
            return f"✅ 找到 agent：{dir_name}，正在開啟..."

        self.agents.main.tools_map["open_agent"] = _open_agent_tool
        # session 名稱與 resume 狀態都在磁碟上（SessionStore），這裡只需畫出來
        self.refresh_agent_view()
        self.query_one("#prompt", HistoryInput).focus()
        self._render_history()
        self.set_interval(15, lambda: self.query_one("#sidebar", Sidebar).refresh_git())
        self._mcp_done = asyncio.Event()
        self._refresh_skills_sidebar()
        self._mcp_task = asyncio.get_event_loop().create_task(self._mcp_lifecycle())

    @work
    async def _render_history(self) -> None:
        """啟動時把 agent.history 畫在歡迎訊息後面。"""
        await self._active_msgs().render_history(self.agent, clear=False)

    async def _mcp_lifecycle(self) -> None:
        """Single long-lived asyncio task: MCP start → keep-alive → stop.

        Keeping start() and stop() in the same task avoids anyio cross-task
        cancel scope errors that occur when stop() is called from on_unmount.
        """
        servers = load_mcp_servers()
        if not servers:
            self._mcp_ready()
            return
        mcp = MCPClient(servers, errlog=open(os.devnull, "w"))
        try:
            await mcp.start()
            if not mcp.tool_names:
                self._mcp_ready()
                return
            ctx = await _fetch_db_schema(mcp)
            fs_paths = [
                arg
                for name, cfg in servers.items()
                if not name.startswith("_") and cfg.get("command") in ("npx", "node")
                for arg in cfg.get("args", [])
                if arg.startswith("/")
            ]
            if fs_paths:
                ctx += f"\n檔案系統可存取目錄：{', '.join(fs_paths)}"
            self.agents.set_mcp(mcp, ctx)
            self._mcp_ready()
            logger.info(f"MCP 初始化完成：{mcp.tool_names}")
            assert self._mcp_done
            await self._mcp_done.wait()
        except asyncio.CancelledError:
            pass
        finally:
            self.agents.set_mcp(None, "")
            await mcp.stop()

    def _mcp_ready(self) -> None:
        """MCP 初始化結束（不論有無 server）：sidebar 從「初始化中」換成實際狀態。"""
        self._mcp_initializing = False
        self.refresh_agent_view()

    async def action_quit(self) -> None:
        if self._mcp_done:
            self._mcp_done.set()
        if self._mcp_task and not self._mcp_task.done():
            try:
                await asyncio.wait_for(self._mcp_task, timeout=2.0)
            except BaseException:
                self._mcp_task.cancel()
        # 只有串流進行中才需要在退出時存檔；正常情況 agent.chat 已在每輪末尾呼叫 save_history，
        # 若無條件存檔會覆蓋使用者對 history.json 的手動編輯。

        if self.prompt_locked:
            hist = self.agent.history
            if hist and hist[-1]["role"] == "user":
                hist.pop()
            self.agent.save_history()
        if self._bash_proc and self._bash_proc.returncode is None:
            try:
                os.killpg(os.getpgid(self._bash_proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        # 退出前 flush Langfuse 待上傳的 span，避免 TUI 關閉掉資料
        from core.langfuse_client import flush

        flush()
        self.exit()

    # ── 輸入事件 ──────────────────────────────────────────────────────────────

    async def on_prompt_bar_submitted(self, event: PromptBar.Submitted) -> None:
        """輸入列送出：`!` → bash，`/` → slash 指令，其他展開 @檔案後送給 Agent。"""
        text = event.text
        if text.startswith("!"):
            cmd = text[1:].strip()
            if cmd:
                self._bash_worker = self._run_bash(cmd)
            return

        if text.startswith("/"):
            await self.run_slash(text)
            return

        await self._active_msgs().add_user(text)

        at_files = self._parse_at_files(text)
        if at_files:
            file_blocks = "\n\n".join(
                f"# 檔案：{p}\n```\n{c}\n```" for p, c in at_files
            )
            full_text = f"{file_blocks}\n\n{text}"
        else:
            full_text = text

        self._send(full_text, original_text=text)

    # ── Actions ───────────────────────────────────────────────────────────────

    _AT_SKIP_DIRS = frozenset(
        {
            ".git",
            ".venv",
            "venv",
            "env",
            "__pycache__",
            ".mypy_cache",
            ".ruff_cache",
            ".pytest_cache",
            "dist",
            "build",
            ".next",
            ".nuxt",
            "node_modules",
            ".idea",
            ".vscode",
        }
    )
    _AT_SKIP_SUFFIXES = frozenset(
        {
            ".pyc",
            ".pyo",
            ".so",
            ".dll",
            ".exe",
            ".o",
            ".a",
            ".lib",
            ".obj",
        }
    )
    _AT_IMAGE_SUFFIXES = frozenset(
        {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".bmp",
            ".ico",
            ".tiff",
            ".svg",
        }
    )

    def _dir_tree(self, root: Path, prefix: str = "", depth: int = 0) -> list[str]:
        if depth > 4:
            return [prefix + "…"]
        lines: list[str] = []
        try:
            entries = sorted(
                root.iterdir(), key=lambda e: (e.is_file(), e.name.lower())
            )
        except PermissionError:
            return [prefix + "(permission denied)"]
        for entry in entries:
            if entry.name.startswith(".") and entry.name not in {".env", ".env.local"}:
                continue
            if entry.name in self._AT_SKIP_DIRS:
                continue
            if entry.is_file() and entry.suffix.lower() in self._AT_SKIP_SUFFIXES:
                continue
            connector = "├── " if entry != entries[-1] else "└── "
            lines.append(
                prefix + connector + entry.name + ("/" if entry.is_dir() else "")
            )
            if entry.is_dir():
                ext = "│   " if entry != entries[-1] else "    "
                lines.extend(self._dir_tree(entry, prefix + ext, depth + 1))
        return lines

    def _parse_at_files(self, text: str) -> list[tuple[str, str]]:
        """解析訊息中的 @path：檔案注入內容，目錄注入 tree 結構。"""
        files: list[tuple[str, str]] = []
        seen: set[str] = set()
        for m in self._at_mention_re.finditer(text):
            raw = m.group(1)
            try:
                p = Path(raw).expanduser().resolve()
                if str(p) in seen:
                    continue
                seen.add(str(p))
                if p.is_file():
                    if p.suffix.lower() in self._AT_IMAGE_SUFFIXES:
                        self.notify(
                            f"@{p.name} 是圖片，請改用 /image 指令",
                            severity="warning",
                            timeout=3,
                        )
                        continue
                    files.append(
                        (str(p), p.read_text(encoding="utf-8", errors="replace"))
                    )
                elif p.is_dir():
                    tree_lines = [p.name + "/"] + self._dir_tree(p)
                    files.append((str(p), "[目錄結構]\n" + "\n".join(tree_lines)))
            except Exception:
                pass
        return files

    def _do_tts_switch(self, arg: str) -> None:
        _ENGINES = ("edge-tts", "azure-tts", "kokoro", "kokoro-api", "melotts", "off")
        engine = arg.strip().lower()
        if not engine:
            cfg = getattr(self.agent, "tts_config", None)
            if cfg:
                self.notify(
                    f"TTS 目前：{cfg.get('engine', 'edge-tts')}  voice={cfg.get('voice', '—')}",
                    timeout=4,
                )
            else:
                self.notify("TTS 已關閉（此 agent 無 tts 設定）", timeout=3)
            return
        if engine not in _ENGINES:
            self.notify(
                f"未知引擎：{engine}  可用：{', '.join(_ENGINES)}", severity="warning"
            )
            return
        if engine == "off":
            self.agent.tts_config = None
            self.notify("TTS 已關閉", timeout=3)
            return
        _defaults: dict[str, dict] = {
            "kokoro": {"voice": "af_sky", "lang": "en-us"},
            "kokoro-api": {"voice": "af_bella", "lang": "en-us", "port": 8885},
            "melotts": {"voice": "EN-US", "lang": "en", "port": 8886},
            "edge-tts": {"voice": "en-US-AriaNeural", "lang": "en-us"},
            "azure-tts": {"voice": "zh-TW-HsiaoChenNeural", "lang": "zh-TW"},
        }
        cfg = dict(getattr(self.agent, "tts_config", None) or {})
        cfg.update({"engine": engine, **_defaults[engine]})
        self.agent.tts_config = cfg
        voice_info = cfg["voice"]
        if engine == "azure-tts" and not (cfg.get("key") or _kai_cfg.AZURE_TTS_KEY):
            voice_info += "  ⚠️ 需設定 AZURE_TTS_KEY"
        self.notify(f"TTS 切換為：{engine}  voice={voice_info}", timeout=4)

    def _thinking_active(self) -> bool:
        if self.think_override is not None:
            return self.think_override
        return self.agent.profile.supports_thinking

    def refresh_agent_view(self) -> None:
        """從作用中 Agent 重算顯示快照；值沒變就不會觸發任何重畫。"""
        agent = self.agent
        profile = agent.profile
        if self._mcp_initializing:
            mcp = None
        else:
            summary = agent.mcp_client.server_summary if agent.mcp_client else {}
            mcp = tuple(summary.items())
        self.agent_view = AgentView(
            display_name=agent.display_name,
            model=profile.name,
            supports_thinking=profile.supports_thinking,
            supports_vision=profile.supports_vision,
            thinking_on=self._thinking_active(),
            mcp=mcp,
            session_name=self._session_store().name or "",
        )

    def watch_agent_view(self, old: AgentView, new: AgentView) -> None:
        self.sub_title = (
            f"{new.display_name} · {new.session_name}"
            if new.session_name
            else new.display_name
        )
        if old.display_name != new.display_name:
            self.query_one("#prompt", HistoryInput).set_base_placeholder(
                f"傳訊息給 {new.display_name}..."
            )

    def compute_prompt_locked(self) -> bool:
        return self.active_tab in self.busy_tabs

    def mark_busy(self, tab_id: str, cancel: "CancelToken | None" = None) -> None:
        """標記 tab 開始一件工作；該 tab 在畫面上時 prompt 會鎖住。

        帶 cancel 的工作（turn）在使用者於該 tab 按 Esc 時會被取消。
        """
        self.busy_tabs = (*self.busy_tabs, tab_id)
        if cancel is not None:
            self._turn_cancels.append((tab_id, cancel))

    def unmark_busy(self, tab_id: str, cancel: "CancelToken | None" = None) -> None:
        """結束 tab 的一件工作；全部結束且該 tab 在畫面上時 prompt 解鎖並 focus。"""
        busy = list(self.busy_tabs)
        if tab_id in busy:
            busy.remove(tab_id)
        self.busy_tabs = tuple(busy)
        if cancel is not None and (tab_id, cancel) in self._turn_cancels:
            self._turn_cancels.remove((tab_id, cancel))

    def cancel_turns(self, tab_id: str) -> None:
        """取消該 tab 上所有進行中的 turn。"""
        for tid, cancel in self._turn_cancels:
            if tid == tab_id:
                cancel.cancel()

    def watch_think_override(self) -> None:
        self.refresh_agent_view()

    def action_toggle_think(self) -> None:
        self.think_override = not self._thinking_active()
        label = "開啟 💡" if self.think_override else "關閉"
        self.notify(f"Thinking：{label}", severity="information", timeout=2)

    def on_cap_bar_think_toggled(self, event: CapBar.ThinkToggled) -> None:
        self.action_toggle_think()

    def action_toggle_file_tree(self) -> None:
        ft = self.query_one("#file-tree", FileTree)
        ft.display = not ft.display
        self.query_one("#file-tree-handle", ResizeHandle).display = ft.display

    def on_center_tabs_active_changed(self, event: CenterTabs.ActiveChanged) -> None:
        """切換到 agent tab（或 chat tab）時把它設為作用中的 Agent；檔案 tab 不改變作用中的 Agent。"""
        if self.agents.activate(event.tab_id):
            self._on_active_agent_changed()
        # prompt 鎖定由 prompt_locked（active_tab ∈ busy_tabs）決定
        self.active_tab = event.tab_id
        if not self.prompt_locked:
            self.query_one("#prompt", HistoryInput).focus()

    def on_center_tabs_tab_closed(self, event: CenterTabs.TabClosed) -> None:
        if event.tab_id != AgentRegistry.MAIN_ID and self.agents.get(event.tab_id):
            self.agents.remove(event.tab_id)

    def _switch_to_tab(self, tab_id: str) -> None:
        """程式切換 tab：設 tc.active，並同步設定作用中 Agent（不等非同步的 TabActivated）。"""
        self.query_one(CenterTabs).active = tab_id
        self.active_tab = tab_id
        if self.agents.activate(tab_id):
            self._on_active_agent_changed()

    def _on_active_agent_changed(self) -> None:
        self.refresh_agent_view()
        self._refresh_skills_sidebar()

    def _refresh_skills_sidebar(self) -> None:
        """把目前 cwd 可用的 skills 同步到側邊欄。"""
        self.query_one("#sidebar", Sidebar).set_skills(
            [s.name for s in discover_skills()]
        )

    def on_file_tree_file_chosen(self, event: FileTree.FileChosen) -> None:
        path = event.path
        if path.suffix.lower() in _IMAGE_EXTS:
            self.query_one(PromptBar).attach(path)
        else:
            self.query_one(CenterTabs).open_file(path)

    def action_move_tab_left(self) -> None:
        self.query_one(CenterTabs).move_active(-1)

    def action_move_tab_right(self) -> None:
        self.query_one(CenterTabs).move_active(1)

    async def action_close_tab(self) -> None:
        tabs = self.query_one(CenterTabs)
        await tabs.request_close(tabs.active)

    async def _maybe_close_tab(self, tab_id: str) -> None:
        """關閉 tab（未存檔檔案會先確認）；agent tab 關掉後由 on_center_tabs_tab_closed 清 registry。"""
        await self.query_one(CenterTabs).request_close(tab_id)

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.display = not sidebar.display
        self.query_one("#sidebar-handle", ResizeHandle).display = sidebar.display

    def _active_msgs(self) -> ChatLog:
        """回傳作用中 Agent 對應的訊息容器。"""
        return self.query_one(CenterTabs).messages_for(self.agents.active_id)

    @property
    def agent(self) -> Agent:
        """作用中的 Agent（由 self.agents 決定）。"""
        return self.agents.active

    def action_cancel_stream(self) -> None:
        from textual.screen import ModalScreen

        if isinstance(self.screen, ModalScreen):
            self.screen.dismiss()
            return
        ac = self.query_one(PromptBar).autocomplete
        if ac.display:
            ac.action_hide()
            return
        prompt = self.query_one("#prompt", HistoryInput)
        if self.prompt_locked:
            # 正在串流：取消並標記回填
            self.input_state.restore_input = True
            self.cancel_turns(self.active_tab)
            if self._bash_proc and self._bash_proc.returncode is None:
                try:
                    os.killpg(os.getpgid(self._bash_proc.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            if self._bash_worker and self._bash_worker.is_running:
                self._bash_worker.cancel()
        else:
            # 未串流：清除樹多選 or 聚焦輸入框
            try:
                from widgets.tree import FileTree

                ft = self.query_one("#file-tree", FileTree)
                if ft._multi_selected:
                    ft.clear_multi_select()
                else:
                    prompt.focus()
            except Exception:
                prompt.focus()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="kai — terminal AI agent TUI")
    parser.add_argument("directory", nargs="?", help="啟動目錄（預設為當前目錄）")
    args = parser.parse_args()
    if args.directory:
        target = Path(args.directory).expanduser().resolve()
        if not target.is_dir():
            print(f"錯誤：{target} 不是有效的目錄")
            raise SystemExit(1)
        os.chdir(target)
    # chdir 後才建 .kai/ 和設定 log，確保落在正確的目錄
    ensure_kai_dir()
    apply_kai_config(load_kai_config())
    logger.add(
        ".kai/agent_tui.log",
        level="DEBUG",
        rotation="5 MB",
        retention=3,
        format="{time:HH:mm:ss} | {level:<5} | {message}",
    )
    ChatApp().run()
