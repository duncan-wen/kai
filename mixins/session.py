"""SessionMixin — /nickname, /cd, /compact, /cls, /resume, /model 指令邏輯。"""
from __future__ import annotations

import os
from pathlib import Path


from textual.containers import VerticalScroll
from textual.widgets import Static, TabbedContent

from core.agent import Agent
from core.session_store import SessionStore
from core.config import _normalize, apply_kai_config, current_kai_config, ensure_kai_dir, get_nickname, load_kai_config, reload_env
from core.models import REGISTRY
from widgets.input import HistoryInput
from widgets.sidebar import Sidebar
from widgets.tree import FileTree

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app import ChatApp as _MixinBase
else:
    _MixinBase = object  # type: ignore[assignment,misc]


class SessionMixin(_MixinBase):
    def _do_nickname(self, arg: str) -> None:
        model = self.agent.profile.name
        key = f"NICKNAME_{_normalize(model)}"
        if not arg:
            current = get_nickname(model)
            shown = current if current != model else "（未設定）"
            self.notify(f"{model}\n.env key：{key}\n目前暱稱：{shown}", timeout=6)
            return
        env_path = Path(".env")
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        replaced = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
                lines[i] = f"{key}={arg}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}={arg}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.environ[key] = arg
        self.agent._rebuild_system_prompt()
        self.agent.save_history()
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.placeholder = f"傳訊息給 {arg}..."
        self.sub_title = arg
        self.notify(f"✅ {model} 暱稱設為「{arg}」", timeout=3)

    def _apply_project_config(self) -> None:
        """重新讀取 .env + .kai/config.json 並套用所有設定到 agent 和 UI。"""
        reload_env()
        cfg = load_kai_config()
        apply_kai_config(cfg)
        if "history_keep" in cfg:
            self.agent.history_keep = int(cfg["history_keep"])
        if "num_ctx" in cfg:
            self.agent.profile.num_ctx = int(cfg["num_ctx"])
        if "debug_tools" in cfg:
            v = cfg["debug_tools"]
            self.ui_state.debug_tools = bool(v) if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")
        if "model" in cfg and cfg["model"] != self.agent.profile.name:
            self.call_later(self.action_switch_model, cfg["model"])

    async def action_init_config(self, arg: str) -> None:
        """開啟互動式 config 面板（選取 key 後按 L/G 匯出）。"""
        from widgets.config_screen import ConfigScreen
        from core.config import config_sources
        cfg = current_kai_config()
        await self.push_screen(ConfigScreen(cfg, config_sources()))

    async def action_reload_config(self) -> None:
        """重新載入 .kai/config.json 並套用所有設定。"""
        self._apply_project_config()
        self.notify("✅ .kai/config.json 已重新載入", severity="information", timeout=3)

    async def _do_cd(self, arg: str) -> None:
        target = Path(self.session.prev_cwd if arg == "-" else arg).expanduser().resolve()
        if not target.is_dir():
            self.notify(f"目錄不存在：{target}", severity="error")
            return
        self.agent.save_history()
        self.session.prev_cwd = os.getcwd()
        os.chdir(target)
        ensure_kai_dir()
        self._apply_project_config()
        self.query_one("#sb-cwd", Static).update(str(target))
        self.query_one("#file-tree", FileTree).refresh_tree()
        self.agent.reset_history()
        await self.query_one("#messages", VerticalScroll).remove_children()
        self._render_history()
        self._update_subtitle()
        self.notify(f"已切換至 {target}", severity="information")

    async def action_compact(self) -> None:
        if not self.agent._gemini and not self.agent._deepseek:
            self.notify("需要 GEMINI_API_KEY 或 DEEPSEEK_API_KEY 才能使用 compact", severity="warning")
            return
        msgs = self._active_msgs()
        await msgs.mount(Static("⚡ 正在用 Gemini 壓縮對話歷史…", classes="error-msg", markup=False))
        msgs.scroll_end(animate=False)
        try:
            await self.agent.compact_history()
            await msgs.remove_children()
            await self._render_history_into(msgs, self.agent)
            self.notify("✅ 對話已壓縮", severity="information")
        except Exception as e:
            self.notify(f"compact 失敗：{e}", severity="error")

    async def action_clear_screen(self) -> None:
        await self._active_msgs().remove_children()

    # ── session 命名 ──────────────────────────────────────────────────────────

    def _session_store(self, agent: Agent | None = None) -> SessionStore:
        """目前（或指定）agent 的 session archive。

        每次依 history 檔路徑重建：狀態都在磁碟上，/cd 後自然對到新目錄。
        """
        return SessionStore((agent or self.agent)._history_file)

    def _update_subtitle(self) -> None:
        display = self.agent.display_name
        name = self._session_store().name
        self.sub_title = f"{display} · {name}" if name else display

    def _do_rename(self, arg: str) -> None:
        store = self._session_store()
        if not arg:
            shown = store.name or "（未命名）"
            self.notify(f"目前 session 名稱：{shown}\n用法：/rename <名稱>", timeout=5)
            return
        to_archive = store.resumed_from is not None
        try:
            store.rename(arg)
        except OSError as e:
            self.notify(f"命名失敗：{e}", severity="error")
            return
        self._update_subtitle()
        hint = "已同步更新來源 archive" if to_archive else "封存時自動儲存"
        self.notify(f"Session 命名為「{arg}」，{hint}", timeout=3)

    # ── /cls ─────────────────────────────────────────────────────────────────

    async def action_clear(self) -> None:
        # 快照：await 期間 self.agent 可能因 tab 事件被替換，固定操作當下的 agent/msgs
        agent = self.agent
        msgs = self._active_msgs()
        real_msgs = [m for m in agent.history if m.get("role") != "system"]
        if not real_msgs:
            self.notify("沒有對話記錄，無需清除", severity="information", timeout=2)
            return
        agent.save_history()  # 確保磁碟與記憶體一致後再封存
        try:
            result = self._session_store(agent).close()
        except OSError as e:
            self.notify(f"封存失敗：{e}", severity="error")
            return
        await msgs.remove_children()
        if result.kind == "archived" and result.archive is not None:
            self.notify(f"已封存至 .history/{result.archive.name}", timeout=3)
        elif result.kind == "discarded":
            self.notify("已清除（resume 後未新增對話，不重複封存）", timeout=3)
        agent.history = []
        agent._rebuild_system_prompt()
        agent.save_history()
        # await 期間 TabActivated 可能把 self.agent 換成 main agent，顯式還原
        self.agent = agent
        self._update_subtitle()
        self.query_one("#file-tree", FileTree).refresh_tree()

    # ── /resume ──────────────────────────────────────────────────────────────

    async def _handle_resume(self, arg: str) -> None:
        from widgets.resume import ResumeScreen
        store = self._session_store()
        entries = store.list_archives()
        if not entries:
            self.notify("沒有任何 archive", severity="warning")
            return
        if arg:
            try:
                idx = int(arg) - 1
                if idx < 0:
                    raise IndexError
                archive = entries[idx].path
            except (ValueError, IndexError):
                self.notify(f"編號無效，共 {len(entries)} 個 archive", severity="error")
                return
            await self._resume_load(archive)
            return

        async def _on_result(archive: Path | None) -> None:
            if archive is not None:
                await self._resume_load(archive)

        self.push_screen(ResumeScreen(store), _on_result)

    async def _resume_load(self, archive: Path) -> None:
        agent = self.agent
        store = self._session_store(agent)
        agent.save_history()  # 確保磁碟與記憶體一致後再切換
        try:
            result = store.resume(archive)
        except OSError as e:
            self.notify(f"載入失敗：{e}", severity="error")
            return
        if result.kind == "archived" and result.archive is not None:
            self.notify(f"已自動封存：{result.archive.name}", timeout=3)
        msgs = self._active_msgs()
        await msgs.remove_children()
        agent.reset_history()
        await self._render_history_into(msgs, agent)
        self.agent = agent
        self._update_subtitle()
        name_display = store.name or archive.name
        self.notify(f"已載入：{name_display}（有新對話後才會重新封存）", timeout=4)

    async def action_switch_model(self, model_name: str) -> None:
        if model_name not in REGISTRY:
            self.notify(f"未知模型：{model_name}", severity="error")
            return
        if model_name == self.agent.profile.name:
            self.notify("已是目前模型", severity="information")
            return
        old_history = self.agent.history[:]
        old_mcp = self.agent.mcp_client
        old_ctx = getattr(self.agent, "mcp_context", "")
        self.agent = Agent(model=model_name)
        self.agent.history = old_history
        self.agent.mcp_client = old_mcp
        self.agent.mcp_context = old_ctx
        self.agent._rebuild_system_prompt()
        self.ui_state.think_override = None
        tc = self.query_one("#center-tabs", TabbedContent)
        if tc.active == "chat":
            self.tab_state.main_agent = self.agent
        self._sync_cap_bar()
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.placeholder = f"傳訊息給 {get_nickname(model_name)}..."
        self.sub_title = get_nickname(model_name)
        self.query_one("#sidebar", Sidebar).set_model(model_name)
        self.notify(f"已切換至 {model_name}", severity="information")
