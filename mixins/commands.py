"""CommandsMixin — 所有 slash 指令的 handler 與分派。

每個 handler 用 `@slash(...)` 註冊進 `widgets/slash.py` 的指令表，簽名一律為
`async def _cmd_xxx(self, arg: str) -> None`。實際邏輯多半在各功能 mixin，
這裡只負責參數檢查與轉接；送出輸入與命令面板走同一個 handler。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from textual_fspicker import FileOpen, Filters, SelectDirectory

from core.models import REGISTRY
from widgets.chat import ImageBubble
from widgets.input import HistoryInput
from widgets.slash import get_command, slash, split_command
from widgets.tree import FileTree

if TYPE_CHECKING:
    from app import ChatApp as _MixinBase
else:
    _MixinBase = object  # type: ignore[assignment,misc]


# 圖片選擇器只列視覺模型能讀的格式
_PICKER_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})

_TTS_ENGINES = (
    ("edge-tts", "雲端"),
    ("azure-tts", "Azure Cognitive Services TTS"),
    ("melotts", "Docker 8886"),
    ("kokoro-api", "Docker 8885"),
    ("kokoro", "本地 ONNX"),
)


class CommandsMixin(_MixinBase):
    async def run_slash(self, text: str) -> None:
        """分派一行 `/xxx ...` 輸入到對應 handler。"""
        name, arg = split_command(text)
        cmd = get_command(name)
        if cmd is None:
            self.notify(f"未知指令：{name}", severity="warning")
            return
        await getattr(self, cmd.method)(arg)

    def _usage(self, text: str) -> None:
        self.notify(f"用法：{text}", severity="warning")

    # ── 對話 ─────────────────────────────────────────────────────────────────

    @slash("/clear", "清除畫面")
    async def _cmd_clear(self, arg: str) -> None:
        await self.action_clear_screen()

    @slash("/ask", "臨時提問，不污染對話歷史  /ask <問題>", needs_arg=True)
    async def _cmd_ask(self, arg: str) -> None:
        if not arg:
            self._usage("/ask <問題>")
            return
        self._send(arg, ephemeral=True)

    @slash("/save", "將上一次 /ask 的回答存入對話歷史")
    async def _cmd_save(self, arg: str) -> None:
        if not self.input_state.last_ephemeral:
            self.notify("沒有可保存的 /ask 回答", severity="warning")
            return
        # 存入的 /ask 回答算新對話：session 不再是 resumed session
        self._session_store().mark_active()
        await self.agent.commit(self.input_state.last_ephemeral)
        self.input_state.last_ephemeral = None
        self.notify("已存入對話歷史", timeout=2)

    @slash("/cls", "清除記憶並封存（可 /resume 恢復）")
    async def _cmd_cls(self, arg: str) -> None:
        await self.action_clear()

    @slash("/resume", "列出 archive  /resume [編號]")
    async def _cmd_resume(self, arg: str) -> None:
        await self._handle_resume(arg)

    @slash("/remember", "記住規則  /remember <內容>", needs_arg=True)
    async def _cmd_remember(self, arg: str) -> None:
        if not arg:
            self._usage("/remember <要記住的內容>")
            return
        notes_root = self.agent.agent_dir or Path(os.getcwd()) / ".kai"
        notes_root.mkdir(parents=True, exist_ok=True)
        notes_path = notes_root / "notes.md"
        existing = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""
        if f"- {arg}" in existing:
            self.notify("已存在相同記錄，略過", severity="warning", timeout=3)
            return
        with open(notes_path, "a", encoding="utf-8") as f:
            f.write(f"\n- {arg}")
        self.agent._rebuild_system_prompt()
        self.notify("✅ 已記錄到 .kai/notes.md", severity="information")
        self.query_one("#file-tree", FileTree).refresh_tree()

    @slash("/compact", "用 Gemini 壓縮對話歷史（釋放 context）")
    async def _cmd_compact(self, arg: str) -> None:
        await self.action_compact()

    @slash("/rename", "命名目前 session  /rename <名稱>", needs_arg=True)
    async def _cmd_rename(self, arg: str) -> None:
        self._do_rename(arg)

    @slash("/image", "傳送圖片給模型  /image <路徑> [問題]", needs_arg=True)
    async def _cmd_image(self, arg: str) -> None:
        if not self.agent.profile.supports_vision:
            self.notify(f"{self.agent.profile.name} 不支援圖片輸入", severity="error")
            return
        if not arg:
            self._pick_image()
            return
        img_path, _, question = arg.partition(" ")
        question = question.strip() or "請分析這張圖片"
        p = Path(img_path.strip()).expanduser().resolve()
        if not p.exists():
            self.notify(f"找不到圖片：{img_path}", severity="error")
            return
        msgs = self._active_msgs()
        await msgs.mount(ImageBubble(str(p), question))
        msgs.scroll_end(animate=False)
        self._send(question, images=[str(p)])

    def _pick_image(self) -> None:
        """開檔案選擇器，選完把 `/image <路徑> ` 填回 prompt。"""
        filters = Filters(("圖片", lambda p: p.suffix.lower() in _PICKER_IMAGE_EXTS))

        async def _on_selected(path: Path | None) -> None:
            if path:
                prompt = self.query_one("#prompt", HistoryInput)
                prompt.value = f"/image {path} "
                prompt.cursor_position = len(prompt.value)
                prompt.focus()

        self.push_screen(
            FileOpen(os.getcwd(), title="選擇圖片", filters=filters),
            callback=_on_selected,
        )

    # ── 模式 / 模型 ──────────────────────────────────────────────────────────

    @slash("/debug", "切換工具除錯模式（顯示工具參數與回應）")
    async def _cmd_debug(self, arg: str) -> None:
        self.debug_tools = not self.debug_tools
        label = "🔧 開啟" if self.debug_tools else "關閉"
        self.notify(f"工具除錯模式：{label}", severity="information", timeout=3)

    @slash("/think", "切換 Thinking 模式開／關")
    async def _cmd_think(self, arg: str) -> None:
        self.action_toggle_think()

    @slash(
        "/model",
        "切換模型  /model <名稱>",
        needs_arg=True,
        presets=tuple((f"/model {m}", f"切換到 {m}") for m in REGISTRY),
    )
    async def _cmd_model(self, arg: str) -> None:
        if not arg:
            self._usage("/model <模型名稱>")
            return
        await self.action_switch_model(arg)

    @slash("/nickname", "查詢或設定目前模型的暱稱  /nickname [名字]", needs_arg=True)
    async def _cmd_nickname(self, arg: str) -> None:
        self._do_nickname(arg)

    @slash(
        "/tts",
        "切換 TTS 引擎  /tts <edge-tts|azure-tts|melotts|kokoro-api|kokoro|off>",
        needs_arg=True,
        presets=tuple((f"/tts {e}", f"切換 TTS 為 {e}（{d}）") for e, d in _TTS_ENGINES)
        + (("/tts off", "關閉 TTS 語音"),),
    )
    async def _cmd_tts(self, arg: str) -> None:
        self._do_tts_switch(arg)

    @slash(
        "/greeting",
        "agent 開場白開關  /greeting on|off",
        needs_arg=True,
        presets=(
            ("/greeting on", "開啟 agent 開場白"),
            ("/greeting off", "關閉 agent 開場白"),
        ),
    )
    async def _cmd_greeting(self, arg: str) -> None:
        val = arg.lower()
        if val not in ("on", "off"):
            state = "開啟" if self.ui_state.greeting_enabled else "關閉"
            self._usage(f"/greeting on|off（目前：{state}）")
            return
        self.ui_state.greeting_enabled = val == "on"
        label = "開啟" if self.ui_state.greeting_enabled else "關閉"
        self.notify(f"Greeting：{label}", severity="information", timeout=3)

    # ── 目錄 / 設定 ──────────────────────────────────────────────────────────

    @slash("/cd", "切換工作目錄  /cd <路徑>", needs_arg=True)
    async def _cmd_cd(self, arg: str) -> None:
        if arg:
            await self._cd_or_notify(arg)
            return

        async def _on_selected(path: Path | None) -> None:
            if path:
                await self._cd_or_notify(str(path))

        self.push_screen(
            SelectDirectory(os.getcwd(), title="切換目錄"), callback=_on_selected
        )

    async def _cd_or_notify(self, target: str) -> None:
        try:
            await self._do_cd(target)
        except Exception as e:
            self.notify(f"切換失敗：{e}", severity="error")

    @slash("/reload-config", "重新載入 .kai/config.json 並套用所有設定")
    async def _cmd_reload_config(self, arg: str) -> None:
        await self.action_reload_config()

    @slash("/init-config", "互動式 config 面板：選取 key，L→local G→global 匯出")
    async def _cmd_init_config(self, arg: str) -> None:
        await self.action_init_config(arg)

    # ── MCP / Skill ──────────────────────────────────────────────────────────

    @slash("/mcp-list", "列出所有 MCP server 與工具說明")
    async def _cmd_mcp_list(self, arg: str) -> None:
        await self._do_mcp_list()

    @slash("/mcp-reload", "重新載入 MCP servers（不需重啟）")
    async def _cmd_mcp_reload(self, arg: str) -> None:
        await self._do_mcp_reload()

    @slash(
        "/mcp-add",
        "新增 MCP server  /mcp-add <name> <cmd> [args...]  或  /mcp-add <name> --url <url>",
        needs_arg=True,
    )
    async def _cmd_mcp_add(self, arg: str) -> None:
        await self._do_mcp_add(arg)

    @slash("/mcp-del", "移除 MCP server  /mcp-del <name>", needs_arg=True)
    async def _cmd_mcp_del(self, arg: str) -> None:
        await self._do_mcp_del(arg)

    @slash("/skills", "列出所有可用 skill 與觸發時機")
    async def _cmd_skills(self, arg: str) -> None:
        await self._do_skills_list()

    # ── Agent ────────────────────────────────────────────────────────────────

    @slash(
        "/create-agent",
        "用 AI 設計新 agent  /create-agent [--global] <描述>",
        needs_arg=True,
    )
    async def _cmd_create_agent(self, arg: str) -> None:
        if not arg:
            self._usage("/create-agent <描述>")
            return
        self._do_create_agent(arg)

    @slash(
        "/load-agent", "載入 agent 設定  /load-agent <name> [name2 ...]", needs_arg=True
    )
    async def _cmd_load_agent(self, arg: str) -> None:
        if not arg:
            self._usage("/load-agent <name> [name2 ...]")
            return
        for name in arg.split():
            await self._do_load_agent(name)

    @slash("/list-agents", "列出所有已建立的 agents")
    async def _cmd_list_agents(self, arg: str) -> None:
        await self._do_list_agents()

    @slash("/reload-agent", "重新載入目前 agent 的 config.json（system prompt 熱更新）")
    async def _cmd_reload_agent(self, arg: str) -> None:
        await self._do_reload_agent()

    @slash("/delete-agent", "刪除 agent  /delete-agent [name]（省略 name 開面板選）")
    async def _cmd_delete_agent(self, arg: str) -> None:
        await self._do_delete_agent(arg)

    @slash(
        "/close-agent", "關閉 agent tab  /close-agent [name]（省略 name 關目前 tab）"
    )
    async def _cmd_close_agent(self, arg: str) -> None:
        await self._app_close_agent(arg)
