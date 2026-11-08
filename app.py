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
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.message import Message
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Footer,
    Header,
    Input,
    Static,
    TabbedContent,
    TabPane,
)

from core.agent import Agent
from core.agent_registry import AgentRegistry
from core.app_state import InputState, SessionState, TabState, UIState
import core.config as _kai_cfg
from core.config import USERNAME, apply_kai_config, ensure_kai_dir, load_kai_config
from core.mcp_client import MCPClient, load_mcp_servers
from core.models import REGISTRY
from mixins.agents import AgentsMixin
from mixins.app_tools import AppToolsMixin
from mixins.bash import BashMixin
from mixins.mcp import MCPMixin
from mixins.session import SessionMixin
from mixins.streaming import StreamingMixin
from textual_fspicker import FileOpen, Filters, SelectDirectory
from widgets.chat import AIMessage, CapabilityBadge, CapLabel, ConfirmCloseScreen, FileViewer, ImageBubble
from widgets.commands import ChatCommands
from widgets.input import ChatAutoComplete, HistoryInput
from widgets.sidebar import ResizeHandle, Sidebar
from widgets.tree import FileTree
from widgets.utils import _IMAGE_EXTS

logger.remove()
# logger.add 在 main block 的 os.chdir 之後才真正加入，這裡只移除預設 handler

PROMPT_HISTORY_FILE = Path(".kai/.prompt_history")
# Mouse event debris that leaks into Input when Playwright/WSLg resets terminal mouse
# tracking state. Textual consumes ESC and [ but the rest falls through as keystrokes:
#   SGR full:    ESC[<35;80;25M  →  <35;80;25M  or  35;80;25M
#   SGR partial: 80;25M
# Pattern: optional ESC, optional [, optional <, then semicolon-separated numbers + M/m
_MOUSE_SEQ = re.compile(r"(?:\x1b\[?)?<?(?:\d+;)+\d+[Mm]")


def _load_prompt_history() -> list[str]:
    """讀取 prompt_toolkit FileHistory 格式，回傳最舊→最新排列的清單。"""
    if not PROMPT_HISTORY_FILE.exists():
        return []
    entries: list[str] = []
    current: list[str] = []
    for line in PROMPT_HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("+"):
            current.append(line[1:])
        elif not line.strip() and current:
            entries.append("\n".join(current))
            current = []
    if current:
        entries.append("\n".join(current))
    return entries


def _append_prompt_history(text: str) -> None:
    """將一筆輸入追加到 prompt_toolkit FileHistory 格式的檔案。"""
    from datetime import datetime

    lines = "\n".join(f"+{ln}" for ln in text.splitlines())
    entry = f"# {datetime.now()}\n{lines}\n\n"
    try:
        with PROMPT_HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        logger.warning(f"寫入 prompt history 失敗：{e}")


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
        self.target = target   # "main" / "chat" 或 agent 暱稱
        self.content = content


class ChatApp(MCPMixin, AgentsMixin, AppToolsMixin, SessionMixin, BashMixin, StreamingMixin, App[None]):
    CSS_PATH = "app.tcss"
    COMMANDS = App.COMMANDS | {ChatCommands}

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出", priority=True),
        Binding("ctrl+p", "command_palette", "命令"),
        Binding("ctrl+l", "clear_screen", "清除畫面"),
        Binding("ctrl+t", "toggle_file_tree", "檔案樹"),
        Binding("ctrl+b", "toggle_sidebar", "側邊欄"),
        Binding("ctrl+w", "close_tab", "關閉標籤", priority=True),
        Binding("ctrl+shift+left",  "move_tab_left",  "Tab 左移", priority=True, show=False),
        Binding("ctrl+shift+right", "move_tab_right", "Tab 右移", priority=True, show=False),
        Binding("escape", "cancel_stream", "取消", priority=True),
    ]

    SLASH_COMMANDS: list[tuple[str, str]] = []

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
        self._at_mention_re = re.compile(r'(?:(?<=\s)|^)@(\S+)')
        self._mcp_task: asyncio.Task | None = None
        self._mcp_done: asyncio.Event | None = None
        self._open_file_tabs: dict[str, Path] = {}
        self._last_file_copy: dict | None = None
        self._bash_proc: asyncio.subprocess.Process | None = None
        self._bash_worker: "Worker | None" = None  # type: ignore[name-defined]
        self.session = SessionState(prev_cwd=os.getcwd())
        self.input_state = InputState(history=_load_prompt_history())
        self.ui_state = UIState(debug_tools=_kai_cfg.DEBUG_TOOLS)
        self.tab_state = TabState()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="app-grid"):
            yield FileTree(id="file-tree")
            yield ResizeHandle("file-tree", id="file-tree-handle")
            with TabbedContent(initial="chat", id="center-tabs"):
                with TabPane("💬 Chat", id="chat"):
                    with VerticalScroll(id="messages"):
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
            yield Sidebar(id="sidebar")
        with Horizontal(id="input-row"):
            yield Static("📎", id="attach-btn", classes="attach-btn")
            yield HistoryInput(placeholder=f"傳訊給 {_kai_cfg.MODEL}...", id="prompt")
        yield ChatAutoComplete(target="#prompt", candidates=None)
        with Horizontal(id="cap-bar"):
            yield CapabilityBadge("think", "think")
            yield CapLabel("vision", "vision")
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
        # session 名稱與 resume 狀態都在磁碟上（SessionStore），這裡只需更新標題
        self._update_subtitle()
        self.query_one("#prompt", HistoryInput).focus()
        self._update_prompt_title()
        self._render_history()
        self.__class__.SLASH_COMMANDS = [
            ("/clear", "清除畫面"),
            ("/ask", "臨時提問，不污染對話歷史  /ask <問題>"),
            ("/save", "將上一次 /ask 的回答存入對話歷史"),
            ("/cls", "清除記憶並封存（可 /resume 恢復）"),
            ("/resume", "列出 archive  /resume [編號]"),
            ("/remember", "記住規則  /remember <內容>"),
            ("/compact", "用 Gemini 壓縮對話歷史（釋放 context）"),
            ("/image", "傳送圖片給模型  /image <路徑> [問題]"),
            ("/debug", "切換工具除錯模式（顯示工具參數與回應）"),
            ("/think", "切換 Thinking 模式開／關"),
            ("/cd", "切換工作目錄  /cd <路徑>"),
            ("/mcp-list", "列出所有 MCP server 與工具說明"),
            ("/mcp-reload", "重新載入 MCP servers（不需重啟）"),
            ("/mcp-add", "新增 MCP server  /mcp-add <name> <cmd> [args...]  或  /mcp-add <name> --url <url>"),
            ("/mcp-del", "移除 MCP server  /mcp-del <name>"),
            ("/rename", "命名目前 session  /rename <名稱>"),
            ("/nickname", "查詢或設定目前模型的暱稱  /nickname [名字]"),
            ("/create-agent", "用 AI 設計新 agent  /create-agent [--global] <描述>"),
            ("/load-agent", "載入 agent 設定  /load-agent <name> [name2 ...]"),
            ("/list-agents", "列出所有已建立的 agents"),
            ("/reload-agent", "重新載入目前 agent 的 config.json（system prompt 熱更新）"),
            ("/delete-agent", "刪除 agent  /delete-agent [name]（省略 name 開面板選）"),
            ("/close-agent", "關閉 agent tab  /close-agent [name]（省略 name 關目前 tab）"),
            ("/reload-config", "重新載入 .kai/config.json 並套用所有設定"),
            ("/init-config", "互動式 config 面板：選取 key，L→local G→global 匯出"),
            ("/tts", "切換 TTS 引擎  /tts <edge-tts|azure-tts|melotts|kokoro-api|kokoro|off>"),
            ("/tts edge-tts", "切換 TTS 為 edge-tts（雲端）"),
            ("/tts azure-tts", "切換 TTS 為 Azure Cognitive Services TTS"),
            ("/tts melotts", "切換 TTS 為 melotts（Docker 8886）"),
            ("/tts kokoro-api", "切換 TTS 為 kokoro-api（Docker 8885）"),
            ("/tts kokoro", "切換 TTS 為 kokoro（本地 ONNX）"),
            ("/tts off", "關閉 TTS 語音"),
            ("/greeting on", "開啟 agent 開場白"),
            ("/greeting off", "關閉 agent 開場白"),
        ] + [(f"/model {m}", f"切換到 {m}") for m in REGISTRY]
        self._sync_cap_bar()
        self.set_interval(15, lambda: self.query_one("#sidebar", Sidebar).refresh_git())
        self._mcp_done = asyncio.Event()
        self._mcp_task = asyncio.get_event_loop().create_task(self._mcp_lifecycle())

    @work
    async def _render_history(self) -> None:
        """把 agent.history 渲染到 chat 視窗（啟動 / /cd 後呼叫）。"""
        msgs = self.query_one("#messages", VerticalScroll)
        await self._render_history_into(msgs, self.agent)

    async def _render_history_into(self, msgs: VerticalScroll, agent: "Agent") -> None:
        """核心渲染邏輯，可指定目標 scroll 與 agent（供 agent tab 直接呼叫）。"""
        widgets: list = []
        ai_updates: list[tuple[AIMessage, str]] = []
        nickname = agent.display_name

        for msg in agent.history:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "system":
                continue
            if role == "user":
                if isinstance(content, list):
                    text = next(
                        (p.get("text", "") for p in content if p.get("type") == "text"),
                        "",
                    )
                else:
                    text = content
                inbox_sender = msg.get("_inbox_sender")
                if inbox_sender and text:
                    from textual.widgets import Collapsible
                    preview = text[:300] + ("…" if len(text) > 300 else "")
                    widgets.append(Collapsible(
                        Static(preview, markup=False),
                        title=f"📨 來自 {inbox_sender}",
                        collapsed=True,
                        classes="inbox-bubble",
                    ))
                elif text:
                    widgets.append(Static(
                        f"[bold]{USERNAME}[/bold]\n{text}",
                        markup=True,
                        classes="user-bubble",
                    ))
            elif role == "assistant" and content:
                widgets.append(Static(f"🤖 {nickname}", classes="ai-header", markup=False))
                ai_msg = AIMessage()
                widgets.append(ai_msg)
                ai_updates.append((ai_msg, content))

        if widgets:
            await msgs.mount(*widgets)  # 一次 layout pass，而非 N 次

        for ai_msg, content in ai_updates:
            await ai_msg.update_content(content, final=True)

        msgs.scroll_end(animate=False)

    async def _mcp_lifecycle(self) -> None:
        """Single long-lived asyncio task: MCP start → keep-alive → stop.

        Keeping start() and stop() in the same task avoids anyio cross-task
        cancel scope errors that occur when stop() is called from on_unmount.
        """
        sidebar = self.query_one("#sidebar", Sidebar)
        servers = load_mcp_servers()
        if not servers:
            sidebar.set_mcp({})
            return
        mcp = MCPClient(servers, errlog=open(os.devnull, "w"))
        try:
            await mcp.start()
            if not mcp.tool_names:
                sidebar.set_mcp({})
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
            sidebar.set_mcp(mcp.server_summary)
            logger.info(f"MCP 初始化完成：{mcp.tool_names}")
            assert self._mcp_done
            await self._mcp_done.wait()
        except asyncio.CancelledError:
            pass
        finally:
            self.agents.set_mcp(None, "")
            await mcp.stop()

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
        from widgets.input import HistoryInput
        prompt = self.query_one("#prompt", HistoryInput)
        if prompt.has_class("streaming"):
            hist = self.agent.history
            if hist and hist[-1]["role"] == "user":
                hist.pop()
            self.agent.save_history()
        if self._bash_proc and self._bash_proc.returncode is None:
            try:
                os.killpg(os.getpgid(self._bash_proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.exit()

    # ── 輸入事件 ──────────────────────────────────────────────────────────────

    def action_history_prev(self) -> None:
        prompt = self.query_one("#prompt", HistoryInput)
        if not prompt.has_focus or prompt.disabled:
            return
        ac = self.query_one(ChatAutoComplete)
        if ac.display:
            return
        if not self.input_state.history:
            return
        if self.input_state.history_idx == -1:
            self.input_state.history_draft = prompt.value
        self.input_state.history_idx = min(self.input_state.history_idx + 1, len(self.input_state.history) - 1)
        _hval = self.input_state.history[-(self.input_state.history_idx + 1)]
        if (len(_hval) > prompt._COMPRESS_THRESHOLD or "\n" in _hval) and not prompt._is_marker(_hval):
            prompt._compress(_hval)
        else:
            prompt.value = _hval
            prompt.cursor_position = len(_hval)

    def action_history_next(self) -> None:
        prompt = self.query_one("#prompt", HistoryInput)
        if not prompt.has_focus or prompt.disabled:
            return
        ac = self.query_one(ChatAutoComplete)
        if ac.display:
            return
        if self.input_state.history_idx == -1:
            return
        self.input_state.history_idx -= 1
        if self.input_state.history_idx == -1:
            prompt.value = self.input_state.history_draft
            prompt.cursor_position = len(prompt.value)
        else:
            _hval = self.input_state.history[-(self.input_state.history_idx + 1)]
            if (len(_hval) > prompt._COMPRESS_THRESHOLD or "\n" in _hval) and not prompt._is_marker(_hval):
                prompt._compress(_hval)
            else:
                prompt.value = _hval
                prompt.cursor_position = len(_hval)

    @on(events.Click, "#attach-btn")
    async def _on_attach_clicked(self, event: events.Click) -> None:
        event.stop()
        from textual_fspicker import FileOpen, Filters
        _filters = Filters(
            ("圖片", lambda p: p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}),
            ("文字", lambda p: p.suffix.lower() in {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ts", ".js", ".html", ".css"}),
            ("所有檔案", lambda p: True),
        )

        async def _on_attach(path) -> None:
            if path:
                from pathlib import Path as _Path
                p = _Path(path)
                prompt = self.query_one("#prompt", HistoryInput)
                if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
                    prompt.value = f"/image {p} "
                else:
                    cur = prompt.value
                    insert = f"@{p} "
                    if cur and not cur.endswith(" "):
                        insert = " " + insert
                    prompt.value = cur + insert
                prompt.cursor_position = len(prompt.value)
                prompt.focus()

        self.push_screen(
            FileOpen(os.getcwd(), title="選擇附件", filters=_filters),
            callback=_on_attach,
        )

    def on_paste(self, event: events.Paste) -> None:
        if _MOUSE_SEQ.search(event.text):
            event.prevent_default()

    def on_input_changed(self, event: Input.Changed) -> None:
        if _MOUSE_SEQ.search(event.value):
            event.input.value = ""
            event.input.cursor_position = 0

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = self.query_one("#prompt", HistoryInput)
        if prompt._multiline_paste is not None:
            paste = prompt._multiline_paste
            marker = prompt._multiline_marker or ""
            prompt._multiline_paste = None
            prompt._multiline_marker = None
            raw_value = event.value
            if marker and marker in raw_value:
                text = raw_value.replace(marker, paste, 1).strip()
            else:
                text = paste
        else:
            text = event.value.strip()
        if not text:
            return
        event.input.clear()
        if not self.input_state.history or self.input_state.history[-1] != text:
            self.input_state.history.append(text)
            _append_prompt_history(text)
        self.input_state.history_idx = -1
        self.input_state.history_draft = ""

        if text.startswith("!"):
            cmd = text[1:].strip()
            if cmd:
                self._bash_worker = self._run_bash(cmd)
            return

        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            if cmd == "/ask":
                if not arg:
                    self.notify("用法：/ask <問題>", severity="warning")
                else:
                    self._send(arg, ephemeral=True)
            elif cmd == "/save":
                if not self.input_state.last_ephemeral:
                    self.notify("沒有可保存的 /ask 回答", severity="warning")
                else:
                    # 存入的 /ask 回答算新對話：session 不再是 resumed session
                    self._session_store().mark_active()
                    self.agent.history.extend(self.input_state.last_ephemeral)
                    self.agent.save_history()
                    self.input_state.last_ephemeral = None
                    self.notify("已存入對話歷史", timeout=2)
            elif cmd == "/clear":
                await self.action_clear_screen()
            elif cmd == "/cls":
                await self.action_clear()
            elif cmd == "/resume":
                await self._handle_resume(arg)
            elif cmd == "/remember":
                if not arg:
                    self.notify("用法：/remember <要記住的內容>", severity="warning")
                else:
                    notes_root = self.agent._agent_dir or Path(os.getcwd()) / ".kai"
                    notes_root.mkdir(parents=True, exist_ok=True)
                    notes_path = notes_root / "notes.md"
                    existing = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""
                    if f"- {arg}" in existing:
                        self.notify("已存在相同記錄，略過", severity="warning", timeout=3)
                    else:
                        with open(notes_path, "a", encoding="utf-8") as _f:
                            _f.write(f"\n- {arg}")
                        self.agent._rebuild_system_prompt()
                        self.notify("✅ 已記錄到 .kai/notes.md", severity="information")
                        self.query_one("#file-tree", FileTree).refresh_tree()
            elif cmd == "/compact":
                await self.action_compact()
            elif cmd == "/model" and arg:
                await self.action_switch_model(arg)
            elif cmd == "/model":
                self.notify("用法：/model <模型名稱>", severity="warning")
            elif cmd == "/debug":
                self.ui_state.debug_tools = not self.ui_state.debug_tools
                label = "🔧 開啟" if self.ui_state.debug_tools else "關閉"
                self.notify(f"工具除錯模式：{label}", severity="information", timeout=3)
                self._update_prompt_title()
            elif cmd == "/think":
                self.action_toggle_think()
            elif cmd == "/cd":
                if arg:
                    try:
                        await self._do_cd(arg)
                    except Exception as e:
                        self.notify(f"切換失敗：{e}", severity="error")
                else:
                    async def _on_dir_selected(path: Path | None) -> None:
                        if path:
                            try:
                                await self._do_cd(str(path))
                            except Exception as e:
                                self.notify(f"切換失敗：{e}", severity="error")
                    self.push_screen(
                        SelectDirectory(os.getcwd(), title="切換目錄"),
                        callback=_on_dir_selected,
                    )
            elif cmd == "/mcp-list":
                await self._do_mcp_list()
            elif cmd == "/mcp-reload":
                await self._do_mcp_reload()
            elif cmd == "/mcp-add":
                await self._do_mcp_add(arg)
            elif cmd == "/mcp-del":
                await self._do_mcp_del(arg)
            elif cmd == "/rename":
                self._do_rename(arg)
            elif cmd == "/nickname":
                self._do_nickname(arg)
            elif cmd == "/create-agent":
                if not arg:
                    self.notify("用法：/create-agent <描述>", severity="warning")
                else:
                    self._do_create_agent(arg)
            elif cmd == "/load-agent":
                if not arg:
                    self.notify("用法：/load-agent <name> [name2 ...]", severity="warning")
                else:
                    for _ag_name in arg.split():
                        await self._do_load_agent(_ag_name)
            elif cmd == "/list-agents":
                await self._do_list_agents()
            elif cmd == "/reload-agent":
                await self._do_reload_agent()
            elif cmd == "/delete-agent":
                await self._do_delete_agent(arg)
            elif cmd == "/close-agent":
                await self._app_close_agent(arg)
            elif cmd == "/reload-config":
                await self.action_reload_config()
            elif cmd == "/init-config":
                await self.action_init_config(arg)
            elif cmd == "/image":
                if not self.agent.profile.supports_vision:
                    self.notify(
                        f"{self.agent.profile.name} 不支援圖片輸入", severity="error"
                    )
                elif not arg:
                    _img_filters = Filters(
                        ("圖片", lambda p: p.suffix.lower() in {
                            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
                        }),
                    )
                    async def _on_image_selected(path: Path | None) -> None:
                        if path:
                            prompt.value = f"/image {path} "
                            prompt.cursor_position = len(prompt.value)
                            prompt.focus()
                    self.push_screen(
                        FileOpen(os.getcwd(), title="選擇圖片", filters=_img_filters),
                        callback=_on_image_selected,
                    )
                else:
                    img_parts = arg.split(" ", 1)
                    img_path = img_parts[0].strip()
                    question = (
                        img_parts[1].strip() if len(img_parts) > 1 else "請分析這張圖片"
                    )
                    p = Path(img_path).expanduser().resolve()
                    if not p.exists():
                        self.notify(f"找不到圖片：{img_path}", severity="error")
                    else:
                        msgs = self._active_msgs()
                        await msgs.mount(ImageBubble(str(p), question))
                        msgs.scroll_end(animate=False)
                        self._send(question, images=[str(p)])
            elif cmd == "/tts":
                self._do_tts_switch(arg)
            elif cmd == "/greeting":
                val = arg.strip().lower()
                if val in ("on", "off"):
                    self.ui_state.greeting_enabled = (val == "on")
                    label = "開啟" if self.ui_state.greeting_enabled else "關閉"
                    self.notify(f"Greeting：{label}", severity="information", timeout=3)
                else:
                    state = "開啟" if self.ui_state.greeting_enabled else "關閉"
                    self.notify(f"用法：/greeting on|off（目前：{state}）", severity="warning")
            else:
                self.notify(f"未知指令：{cmd}", severity="warning")
            return

        msgs = self._active_msgs()
        await msgs.mount(
            Static(f"[bold]{USERNAME}[/bold]\n{text}", markup=True, classes="user-bubble")
        )
        msgs.scroll_end(animate=False)

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

    _AT_SKIP_DIRS = frozenset({
        ".git", ".venv", "venv", "env", "__pycache__",
        ".mypy_cache", ".ruff_cache", ".pytest_cache",
        "dist", "build", ".next", ".nuxt",
        "node_modules", ".idea", ".vscode",
    })
    _AT_SKIP_SUFFIXES = frozenset({
        ".pyc", ".pyo", ".so", ".dll", ".exe",
        ".o", ".a", ".lib", ".obj",
    })
    _AT_IMAGE_SUFFIXES = frozenset({
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff", ".svg",
    })

    def _dir_tree(self, root: Path, prefix: str = "", depth: int = 0) -> list[str]:
        if depth > 4:
            return [prefix + "…"]
        lines: list[str] = []
        try:
            entries = sorted(root.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
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
            lines.append(prefix + connector + entry.name + ("/" if entry.is_dir() else ""))
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
                        self.notify(f"@{p.name} 是圖片，請改用 /image 指令", severity="warning", timeout=3)
                        continue
                    files.append((str(p), p.read_text(encoding="utf-8", errors="replace")))
                elif p.is_dir():
                    tree_lines = [p.name + "/"] + self._dir_tree(p)
                    files.append((str(p), "[目錄結構]\n" + "\n".join(tree_lines)))
            except Exception:
                pass
        return files

    def _update_prompt_title(self) -> None:
        base = f"[bold]{USERNAME}[/bold] [dim]❯[/dim]"
        suffix = "  [dim]🔧 debug[/dim]" if self.ui_state.debug_tools else ""
        self.query_one("#prompt", HistoryInput).border_title = base + suffix

    def _do_tts_switch(self, arg: str) -> None:
        _ENGINES = ("edge-tts", "azure-tts", "kokoro", "kokoro-api", "melotts", "off")
        engine = arg.strip().lower()
        if not engine:
            cfg = getattr(self.agent, "tts_config", None)
            if cfg:
                self.notify(f"TTS 目前：{cfg.get('engine', 'edge-tts')}  voice={cfg.get('voice', '—')}", timeout=4)
            else:
                self.notify("TTS 已關閉（此 agent 無 tts 設定）", timeout=3)
            return
        if engine not in _ENGINES:
            self.notify(f"未知引擎：{engine}  可用：{', '.join(_ENGINES)}", severity="warning")
            return
        if engine == "off":
            self.agent.tts_config = None
            self.notify("TTS 已關閉", timeout=3)
            return
        _defaults: dict[str, dict] = {
            "kokoro":     {"voice": "af_sky",                 "lang": "en-us"},
            "kokoro-api": {"voice": "af_bella",               "lang": "en-us", "port": 8885},
            "melotts":    {"voice": "EN-US",                  "lang": "en",    "port": 8886},
            "edge-tts":   {"voice": "en-US-AriaNeural",       "lang": "en-us"},
            "azure-tts":  {"voice": "zh-TW-HsiaoChenNeural",  "lang": "zh-TW"},
        }
        cfg = dict(getattr(self.agent, "tts_config", None) or {})
        cfg.update({"engine": engine, **_defaults[engine]})
        self.agent.tts_config = cfg
        voice_info = cfg["voice"]
        if engine == "azure-tts" and not (cfg.get("key") or _kai_cfg.AZURE_TTS_KEY):
            voice_info += "  ⚠️ 需設定 AZURE_TTS_KEY"
        self.notify(f"TTS 切換為：{engine}  voice={voice_info}", timeout=4)

    def _thinking_active(self) -> bool:
        if self.ui_state.think_override is not None:
            return self.ui_state.think_override
        return self.agent.profile.supports_thinking

    def _sync_cap_bar(self) -> None:
        profile = self.agent.profile
        thinking_on = self._thinking_active()

        think = self.query_one("#cap-think", CapabilityBadge)
        think.remove_class("cap-think-on", "cap-inactive", "cap-unsupported")
        if thinking_on:
            think.add_class("cap-think-on")
        elif not profile.supports_thinking:
            think.add_class("cap-unsupported")
        else:
            think.add_class("cap-inactive")

        vision = self.query_one("#cap-vision", CapLabel)
        vision.display = bool(profile.supports_vision)

    def action_toggle_think(self) -> None:
        current = self._thinking_active()
        self.ui_state.think_override = not current
        self._sync_cap_bar()
        label = "開啟 💡" if self.ui_state.think_override else "關閉"
        self.notify(f"Thinking：{label}", severity="information", timeout=2)

    def action_toggle_file_tree(self) -> None:
        ft = self.query_one("#file-tree", FileTree)
        ft.display = not ft.display
        self.query_one("#file-tree-handle", ResizeHandle).display = ft.display

    async def on_click(self, event: events.Click) -> None:
        try:
            widget, region = self.screen.get_widget_at(event.screen_x, event.screen_y)
        except Exception:
            return
        wid = widget.id or "" if widget else ""
        if not wid.startswith("--content-tab-") or wid == "--content-tab-chat":
            return
        tab_id = wid[len("--content-tab-"):]
        if event.screen_x >= region.x + region.width - 2:
            event.stop()
            await self._maybe_close_tab(tab_id)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """切換到 agent tab（或 chat tab）時把它設為作用中的 Agent。

        讀 tc.active 而不是 event.pane.id：晚到的過時事件也只會套用畫面上真正的 tab。
        檔案 tab 不改變作用中的 Agent。
        """
        tab_id = self.query_one("#center-tabs", TabbedContent).active
        if not tab_id:
            return
        if self.agents.activate(tab_id):
            self._sync_active_agent_ui()
        # 根據新 tab 是否正在串流，恢復或保持 prompt 鎖定狀態
        # （_send 結束時若使用者已不在原 tab，解鎖交給這裡）
        prompt = self.query_one("#prompt", HistoryInput)
        if tab_id in self.tab_state.streaming_tabs:
            prompt.disabled = True
            prompt.add_class("streaming")
        else:
            prompt.disabled = False
            prompt.remove_class("streaming")
            self._update_prompt_title()
            prompt.focus()

    def _switch_to_tab(self, tab_id: str) -> None:
        """程式切換 tab：設 tc.active，並同步設定作用中 Agent（不等非同步的 TabActivated）。"""
        self.query_one("#center-tabs", TabbedContent).active = tab_id
        if self.agents.activate(tab_id):
            self._sync_active_agent_ui()

    def _sync_active_agent_ui(self) -> None:
        """把作用中 Agent 的模型、MCP、暱稱同步到 sidebar／prompt／subtitle／cap-bar。"""
        sidebar = self.query_one("#sidebar", Sidebar)
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.placeholder = f"傳訊息給 {self.agent.display_name}..."
        sidebar.set_model(self.agent.profile.name)
        sidebar.set_mcp(self.agent.mcp_client.server_summary if self.agent.mcp_client else {})
        self._update_subtitle()
        self._sync_cap_bar()

    def on_file_tree_file_chosen(self, event: FileTree.FileChosen) -> None:
        path = event.path
        if path.suffix.lower() in _IMAGE_EXTS:
            prompt = self.query_one("#prompt", HistoryInput)
            prompt.value = f"/image {path} "
            prompt.cursor_position = len(prompt.value)
            prompt.focus()
        else:
            self._open_file_tab(path)

    @work
    async def _open_file_tab(self, path: Path) -> None:
        tc = self.query_one("#center-tabs", TabbedContent)
        for tid, p in self._open_file_tabs.items():
            if p == path:
                tc.active = tid
                return
        self.tab_state.file_tab_counter += 1
        tab_id = f"ft-{self.tab_state.file_tab_counter}"
        try:
            content = await asyncio.to_thread(
                path.read_text, encoding="utf-8", errors="replace"
            )
        except Exception as e:
            self.notify(f"無法讀取 {path.name}：{e}", severity="error")
            return
        from widgets.utils import _SUFFIX_TO_LANG
        lang = _SUFFIX_TO_LANG.get(path.suffix.lower())
        ta = FileViewer(
            content,
            language=lang,
            show_line_numbers=True,
            soft_wrap=False,
            file_path=path,
        )
        self._open_file_tabs[tab_id] = path
        await tc.add_pane(TabPane(f"{path.name} ×", ta, id=tab_id))
        tc.active = tab_id

    def _move_active_tab(self, direction: int) -> None:
        """將目前 active tab 往左（-1）或右（+1）移動一格，Chat tab 不可移動。"""
        from textual.widgets._content_switcher import ContentSwitcher
        from textual.widgets._tabbed_content import ContentTab, ContentTabs
        tc = self.query_one("#center-tabs", TabbedContent)
        active_id = tc.active
        if not active_id or active_id == "chat":
            return
        try:
            content_tabs = tc.get_child_by_type(ContentTabs)
            tabs_list = content_tabs.query_one("#tabs-list")
            tabs = list(tabs_list.query("Tab").results())
            cur = next((i for i, t in enumerate(tabs) if t.id == ContentTab.add_prefix(active_id)), None)
            if cur is None:
                return
            tgt = cur + direction
            if tgt < 0 or tgt >= len(tabs):
                return
            if ContentTab.sans_prefix(tabs[tgt].id or "") == "chat":
                return
            if direction == -1:
                tabs_list.move_child(tabs[cur], before=tabs[tgt])
            else:
                tabs_list.move_child(tabs[cur], after=tabs[tgt])
            switcher = tc.get_child_by_type(ContentSwitcher)
            panes = list(switcher.children)
            pi = next((i for i, p in enumerate(panes) if p.id == active_id), None)
            if pi is not None:
                ni = pi + direction
                if 0 <= ni < len(panes):
                    if direction == -1:
                        switcher.move_child(panes[pi], before=panes[ni])
                    else:
                        switcher.move_child(panes[pi], after=panes[ni])
        except Exception as _e:
            logger.warning(f"move_tab 失敗（tab/pane 可能不同步）：{_e}")

    def action_move_tab_left(self) -> None:
        self._move_active_tab(-1)

    def action_move_tab_right(self) -> None:
        self._move_active_tab(1)

    async def action_close_tab(self) -> None:
        tc = self.query_one("#center-tabs", TabbedContent)
        active_id = tc.active
        if not active_id or active_id == "chat":
            return
        await self._maybe_close_tab(active_id)

    async def _maybe_close_tab(self, tab_id: str) -> None:
        """關閉 tab，若有未存檔修改則先以 modal 詢問確認。"""
        try:
            pane = self.query_one(f"TabPane#{tab_id}")
            viewer = pane.query_one(FileViewer)
            if viewer._modified:
                fname = viewer._file_path.name if viewer._file_path else "檔案"

                def _on_confirm(confirmed: bool | None) -> None:
                    if confirmed:
                        self._open_file_tabs.pop(tab_id, None)
                        self.query_one("#center-tabs", TabbedContent).remove_pane(tab_id)

                self.push_screen(ConfirmCloseScreen(fname), callback=_on_confirm)
                return  # 等 callback 決定是否關閉
        except Exception:
            pass
        self._open_file_tabs.pop(tab_id, None)
        if self.agents.get(tab_id) is not None and tab_id != AgentRegistry.MAIN_ID:
            self.agents.remove(tab_id)
        await self.query_one("#center-tabs", TabbedContent).remove_pane(tab_id)

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.display = not sidebar.display
        self.query_one("#sidebar-handle", ResizeHandle).display = sidebar.display

    def _active_msgs(self) -> VerticalScroll:
        """回傳作用中 Agent 對應的訊息容器。"""
        tab_id = self.agents.active_id
        if tab_id == AgentRegistry.MAIN_ID:
            return self.query_one("#messages", VerticalScroll)
        try:
            return self.query_one(f"TabPane#{tab_id}", TabPane).query_one(VerticalScroll)
        except Exception:
            return self.query_one("#messages", VerticalScroll)

    @property
    def agent(self) -> Agent:
        """作用中的 Agent（由 self.agents 決定）。"""
        return self.agents.active

    def action_cancel_stream(self) -> None:
        from textual.screen import ModalScreen
        from widgets.input import ChatAutoComplete, HistoryInput
        if isinstance(self.screen, ModalScreen):
            self.screen.dismiss()
            return
        ac = self.query_one(ChatAutoComplete)
        if ac.display:
            ac.action_hide()
            return
        prompt = self.query_one("#prompt", HistoryInput)
        if prompt.disabled:
            # 正在串流：取消並標記回填
            self.input_state.restore_input = True
            self.agent._cancelled = True
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
