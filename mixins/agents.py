"""AgentsMixin — /create-agent, /load-agent, /list-agents 指令邏輯。"""
from __future__ import annotations

from textual import work
from textual.containers import VerticalScroll
from textual.widgets import TabbedContent, TabPane

from core.agent import Agent

from core.models import REGISTRY
from mixins.streaming import SPINNER
from widgets.input import HistoryInput
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app import AgentMessage, ChatApp as _MixinBase
else:
    _MixinBase = object  # type: ignore[assignment,misc]


class AgentsMixin(_MixinBase):
    @work(exclusive=True)
    async def _do_create_agent(self, description: str) -> None:
        import core.config as _cfg
        from core.agent_factory import call_full_design, save_agent, validate_agent
        from textual.widgets import Static
        from widgets.tree import FileTree

        global_scope = "--global" in description.split()
        if global_scope:
            description = description.replace("--global", "").strip()

        msgs = self._active_msgs()
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.disabled = True
        design_model = _cfg.CREATE_AGENT_MODEL or _cfg.MODEL
        prompt.border_title = f"{SPINNER[0]} 設計 agent 中（{design_model}）…"
        _spin_idx = 0

        def _tick() -> None:
            nonlocal _spin_idx
            _spin_idx = (_spin_idx + 1) % len(SPINNER)
            prompt.border_title = f"{SPINNER[_spin_idx]} 設計 agent 中（{design_model}）…"

        timer = self.set_interval(0.08, _tick)
        await msgs.mount(Static(f"🏭 設計中：{description}", markup=False, classes="bash-output"))
        msgs.scroll_end(animate=False)
        try:
            data = await call_full_design(description)
            missing = validate_agent(data)
            if missing:
                self.notify(f"LLM 回覆缺少欄位：{missing}", severity="error")
                return
            adir = save_agent(data, global_scope=global_scope)
            scope_label = f"~/.kai/agents/{adir.name}" if global_scope else f"agents/{adir.name}"
            await msgs.mount(Static(
                f"✅ Agent 已建立：{data['name']}（{scope_label}）\n"
                f"   model: {data['model']}  nickname: {data.get('nickname', '—')}\n"
                f"   輸入 /load-agent {adir.name} 載入",
                markup=False, classes="bash-output",
            ))
            msgs.scroll_end(animate=False)
            self.query_one("#file-tree", FileTree).refresh_tree()
        except Exception as e:
            self.notify(f"建立失敗：{e}", severity="error")
        finally:
            timer.stop()
            prompt.disabled = False
            self._update_prompt_title()
            prompt.placeholder = f"傳訊息給 {self.agent.display_name}..."
            prompt.focus()

    async def _do_load_agent(self, name: str) -> None:
        from core.agent_factory import find_agent, load_agent
        # scope 前綴（來自 AgentsScreen）直接傳入；否則做模糊比對
        if name.startswith(("local:", "global:")):
            resolved = name
        else:
            resolved = find_agent(name) or name
        try:
            data, adir = load_agent(resolved)
        except FileNotFoundError as e:
            self.notify(str(e), severity="error")
            return
        # 已開啟同一個 agent dir → 切換過去，不重複建 tab
        for tab_id, existing in list(self.tab_state.agent_tabs.items()):
            if getattr(existing, "_agent_dir", None) == adir:
                tc = self.query_one("#center-tabs", TabbedContent)
                tc.active = tab_id
                self.agent = existing
                display = data.get("nickname") or data["name"]
                self.notify(f"{display} 已開啟，切換過去", timeout=2)
                return
        model = data.get("model", self.agent.profile.name)
        if model not in REGISTRY:
            self.notify(f"未知模型：{model}，改用 {self.agent.profile.name}", severity="warning")
            model = self.agent.profile.name
        agent = Agent(
            model=model,
            history_file=adir / "history.json",
            agent_dir=adir,
            system_prompt=data["system_prompt"],
        )
        # mcp_servers 明確設為 {} 表示不要 MCP；省略或非空則共用 main agent 的 MCP client
        mcp_cfg = data.get("mcp_servers")
        if mcp_cfg != {}:
            agent.mcp_client = self.agent.mcp_client
            agent.mcp_context = getattr(self.agent, "mcp_context", "")
        nickname = data.get("nickname") or data["name"]
        # 不寫 os.environ 以免污染使用相同 model 的 main agent 暱稱
        agent._nickname = nickname
        if "history_keep" in data:
            agent.history_keep = int(data["history_keep"])
        agent.datasets = data.get("datasets", [])
        # TTS config：True → 預設設定，dict → 自訂 voice/player
        tts_raw = data.get("tts")
        if tts_raw is True:
            agent._tts_config = {"voice": "en-US-AriaNeural"}
        elif isinstance(tts_raw, dict) and tts_raw:
            agent._tts_config = tts_raw
        else:
            agent._tts_config = None
        tab_idx = len(self.tab_state.agent_tabs)
        tab_id = f"agent-{tab_idx}"
        scroll = VerticalScroll(id=f"msgs-agent-{tab_idx}")
        pane = TabPane(f"🤖 {nickname} ×", scroll, id=tab_id)
        tc = self.query_one("#center-tabs", TabbedContent)
        await tc.add_pane(pane)
        self.tab_state.agent_tabs[tab_id] = agent
        tc.active = tab_id
        self.agent = agent
        await self._render_history_into(scroll, agent)
        self.notify(f"✅ 已開啟 agent：{data['name']}", timeout=3)
        self._inject_interagent_tools(agent)
        # 若 config 有 greeting 且是全新對話，觸發 AI 主動開場
        greeting = data.get("greeting", "").strip()
        fresh = not any(m.get("role") != "system" for m in agent.history)
        if greeting and fresh and self.ui_state.greeting_enabled:
            self._trigger_greeting(agent, scroll, greeting)

    def on_agent_message(self, event: "AgentMessage") -> None:
        """Textual event：將 AgentMessage 路由到目標 agent。"""
        self._dispatch_to_agent(event.sender, event.target, event.content)

    @work
    async def _dispatch_to_agent(self, sender: str, target: str, content: str) -> None:
        """找到目標 agent，顯示 inbox bubble，並執行 agent.chat()。"""
        import json as _json
        import re as _re
        from widgets.chat import AIMessage
        from widgets.input import HistoryInput
        from textual.widgets import Static as _Static, Collapsible
        from loguru import logger
        from mixins.streaming import SPINNER

        q = target.strip().lower()
        main = self.tab_state.main_agent
        agent: "Agent | None" = None
        scroll_id: str = ""
        target_tab_id: str = ""

        if main and q in ("main", "chat", main.display_name.lower()):
            agent = main
            scroll_id = "#messages"
            target_tab_id = "chat"
        else:
            for tab_id, ag in self.tab_state.agent_tabs.items():
                nick = (ag._nickname or "").lower()
                dir_name = (ag._agent_dir.name if ag._agent_dir else "").lower()
                if q in nick or nick in q or q in dir_name or dir_name in q:
                    agent = ag
                    scroll_id = f"#msgs-{tab_id}"
                    target_tab_id = tab_id
                    break

        if agent is None:
            logger.warning(f"[inbox] dispatch: 找不到 target='{target}'，已開啟：{[a.display_name for a in self.tab_state.agent_tabs.values()]}")
            return

        try:
            scroll = self.query_one(scroll_id, VerticalScroll)
        except Exception:
            logger.warning(f"[inbox] dispatch: scroll '{scroll_id}' 不存在")
            return

        # 加入 streaming_tabs，讓 tab 切換時 prompt 保持鎖定
        self.tab_state.streaming_tabs.add(target_tab_id)
        prompt = self.query_one("#prompt", HistoryInput)
        try:
            from textual.widgets import TabbedContent as _TC
            if self.query_one("#center-tabs", _TC).active == target_tab_id:
                prompt.disabled = True
                prompt.add_class("streaming")
        except Exception:
            pass

        # spinner
        _spin_idx = 0
        _spin_label = "等待回覆"

        def _tick() -> None:
            nonlocal _spin_idx
            _spin_idx = (_spin_idx + 1) % len(SPINNER)
            try:
                from textual.widgets import TabbedContent as _TC2
                if self.query_one("#center-tabs", _TC2).active == target_tab_id:
                    dbg = "  [dim]🔧 debug[/dim]" if self.ui_state.debug_tools else ""
                    prompt.border_title = f"{SPINNER[_spin_idx]} {_spin_label}{dbg}"
            except Exception:
                pass

        timer = self.set_interval(0.08, _tick)

        preview = content[:300] + ("…" if len(content) > 300 else "")
        await scroll.mount(Collapsible(
            _Static(preview, markup=False),
            title=f"📨 來自 {sender}",
            collapsed=True,
            classes="inbox-bubble",
        ))
        stream_msg: "AIMessage | None" = None
        buf: list[str] = []

        async def on_token(chunk: str) -> None:
            nonlocal _spin_label, stream_msg
            _spin_label = "生成中"
            buf.append(chunk)
            if stream_msg is None:
                await scroll.mount(_Static(f"🤖 {agent.display_name}", classes="ai-header", markup=False))
                stream_msg = AIMessage()
                await scroll.mount(stream_msg)
            await stream_msg.update_content("".join(buf))
            scroll.scroll_end(animate=False)

        async def on_done(*_) -> None:
            if stream_msg is None:
                return
            hist = agent.history
            display = str(hist[-1]["content"]) if hist and hist[-1]["role"] == "assistant" else "".join(buf)
            await stream_msg.update_content(display, final=True)

        async def on_tool_call(name: str, args: dict, result: str) -> None:
            nonlocal _spin_label, stream_msg, buf
            _spin_label = "工具回應中"
            if stream_msg and buf:
                _pre = _re.sub(r"\s*<next>.*?</next>\s*$", "", "".join(buf), flags=_re.DOTALL).rstrip()
                await stream_msg.update_content(_pre, final=True)
                stream_msg = None
                buf.clear()
            _ERR = ("[MCP Error]", "[Error]", "[ValidationError]", "[stderr]", "Traceback")
            is_err = any(kw in result for kw in _ERR)
            icon = "❌" if is_err else "🔧"
            card_cls = "tool-debug tool-debug-error" if is_err else "tool-debug"
            if not self.ui_state.debug_tools:
                if is_err:
                    await scroll.mount(Collapsible(
                        _Static(result[:600], markup=False),
                        title=f"{icon} {name}  [dim]工具錯誤[/dim]",
                        collapsed=True, classes=card_cls,
                    ))
                return
            args_str = _json.dumps(args, ensure_ascii=False, indent=2)
            body = f"ARGS:\n{args_str}\n\nRESULT:\n{result[:2000]}"
            await scroll.mount(Collapsible(
                _Static(body, markup=False),
                title=f"{icon} {name}",
                collapsed=True, classes=card_cls,
            ))
            scroll.scroll_end(animate=False)

        is_main_target = (agent is self.tab_state.main_agent)
        if is_main_target:
            full_content = content
        else:
            main = self.tab_state.main_agent
            reply_to = "main" if (main and sender == main.display_name) else sender
            full_content = content + f"\n\n[系統]完成後必須呼叫 send_to_agent(\"{reply_to}\", 結果摘要) 回報。"

        logger.info(f"[inbox] dispatch: {sender} → {agent.display_name}")
        # 其他 agent 送進來的訊息也算新對話：目標 session 不再是 resumed session
        self._session_archive(agent).mark_active()
        try:
            async with agent._chat_lock:
                await agent.chat(
                    {"role": "user", "content": full_content, "_inbox_sender": sender},
                    on_token=on_token, on_done=on_done, on_tool_call=on_tool_call,
                )
        except Exception as e:
            # @work 預設 exit_on_error：未接住的例外（API 額度用完、網路中斷）會讓整個 app 關閉
            logger.exception(f"[inbox] {agent.display_name} 處理失敗")
            await scroll.mount(_Static(f"❌ 錯誤：{e}", classes="error-msg", markup=False))
            scroll.scroll_end(animate=False)
            self.notify(f"{agent.display_name} 處理 {sender} 的訊息失敗：{e}", severity="error")
        finally:
            timer.stop()
            self.tab_state.streaming_tabs.discard(target_tab_id)
            try:
                from textual.widgets import TabbedContent as _TC3
                if self.query_one("#center-tabs", _TC3).active == target_tab_id:
                    prompt.disabled = False
                    prompt.remove_class("streaming")
                    self._update_prompt_title()
                    prompt.focus()
            except Exception:
                pass

    @work
    async def _trigger_greeting(self, agent: "Agent", scroll: "VerticalScroll", greeting_prompt: str) -> None:
        """讓 agent 主動說第一句話。trigger 不存入 history，只保留 assistant 回覆。"""
        from textual.widgets import Static as _Static
        from widgets.chat import AIMessage
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.disabled = True
        prompt.border_title = f"{SPINNER[0]} {agent.display_name} 開場中…"
        _spin_idx = 0

        def _tick() -> None:
            nonlocal _spin_idx
            _spin_idx = (_spin_idx + 1) % len(SPINNER)
            prompt.border_title = f"{SPINNER[_spin_idx]} {agent.display_name} 開場中…"

        timer = self.set_interval(0.08, _tick)
        stream_msg: AIMessage | None = None
        buf: list[str] = []

        async def on_token(chunk: str) -> None:
            nonlocal stream_msg
            buf.append(chunk)
            if stream_msg is None:
                await scroll.mount(_Static(f"🤖 {agent.display_name}", classes="ai-header", markup=False))
                stream_msg = AIMessage()
                await scroll.mount(stream_msg)
            await stream_msg.update_content("".join(buf))
            scroll.scroll_end(animate=False)

        async def on_done(*_) -> None:
            if stream_msg and buf:
                _hist = agent.history
                if _hist and _hist[-1]["role"] == "assistant":
                    _display = str(_hist[-1]["content"])
                else:
                    _display = "".join(buf)
                await stream_msg.update_content(_display, final=True)

        try:
            async with agent._chat_lock:
                await agent.chat(
                    {"role": "user", "content": greeting_prompt},
                    on_token=on_token,
                    on_done=on_done,
                )
            # 把 trigger user message 從 history 移除，只保留 assistant 開場白
            agent.history = [
                m for m in agent.history
                if not (m.get("role") == "user" and m.get("content") == greeting_prompt)
            ]
            agent.save_history()
        except Exception as e:
            self.notify(f"greeting 失敗：{e}", severity="warning")
        finally:
            timer.stop()
            prompt.disabled = False
            if self.agent is agent:
                self._update_prompt_title()
                prompt.reset_prediction(f"傳訊息給 {agent.display_name}...")
                prompt.focus()

    async def _do_reload_agent(self) -> None:
        """重新從 config.json 熱更新執行中的 agent：system_prompt、model、nickname 全部套用。
        若 history.json 已被刪除，同步清除 in-memory history（否則 save_history 會把舊記憶寫回）。
        """
        adir = self.agent._agent_dir
        if not adir:
            self.notify("目前 agent 沒有 config.json（主 chat 不支援此指令）", severity="warning")
            return
        config_path = adir / "config.json"
        if not config_path.exists():
            self.notify(f"找不到 {config_path}", severity="error")
            return
        import json as _json
        try:
            data = _json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.notify(f"讀取 config.json 失敗：{e}", severity="error")
            return

        name = data.get("nickname") or data.get("name", adir.name)
        new_prompt = data.get("system_prompt", "")
        new_model = data.get("model", self.agent.profile.name)
        changes: list[str] = []

        if new_prompt and new_prompt != self.agent._custom_system_prompt:
            changes.append("system_prompt")
        if name != self.agent._nickname:
            changes.append("nickname")

        # TTS 重載
        tts_raw = data.get("tts")
        if tts_raw is True:
            new_tts: dict | None = {"voice": "en-US-AriaNeural"}
        elif isinstance(tts_raw, dict) and tts_raw:
            new_tts = tts_raw
        else:
            new_tts = None
        if new_tts != self.agent._tts_config:
            changes.append("tts")

        # model 變更：換 Agent 物件；Agent.__init__ 自動從磁碟載入 history
        if new_model != self.agent.profile.name:
            if new_model not in REGISTRY:
                self.notify(f"未知模型：{new_model}", severity="error")
                return
            old = self.agent
            self.agent = Agent(
                model=new_model,
                history_file=old._history_file,
                agent_dir=old._agent_dir,
                system_prompt=new_prompt or old._custom_system_prompt,
            )
            self.agent.mcp_client = old.mcp_client
            self.agent.mcp_context = getattr(old, "mcp_context", "")
            self.agent._nickname = name
            self.agent._tts_config = new_tts
            # 同步更新 agent_tabs 映射
            for tid, ag in list(self.tab_state.agent_tabs.items()):
                if ag is old:
                    self.tab_state.agent_tabs[tid] = self.agent
                    break
            changes.append(f"model → {new_model}")
        else:
            # 以磁碟為準重載 history（不呼叫 save_history，避免覆寫使用者手動編輯）
            if new_prompt:
                self.agent._custom_system_prompt = new_prompt
            self.agent._nickname = name
            self.agent._tts_config = new_tts
            self.agent.datasets = data.get("datasets", [])
            self.agent.history = []
            self.agent._rebuild_system_prompt()   # 先把新 system prompt 放進去
            self.agent._load_history()            # 再從磁碟補入 non-system 訊息

        # 更新 UI
        from widgets.sidebar import Sidebar
        from widgets.input import HistoryInput
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.set_model(self.agent.profile.name)
        mcp_sum = self.agent.mcp_client.server_summary if self.agent.mcp_client else {}
        sidebar.set_mcp(mcp_sum)
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.placeholder = f"傳訊息給 {self.agent.display_name}..."
        self._update_subtitle()
        self._sync_cap_bar()

        # 重新渲染聊天畫面（history 可能已有增減）
        msgs = self._active_msgs()
        await msgs.remove_children()
        await self._render_history_into(msgs, self.agent)

        self._inject_interagent_tools(self.agent)
        self.agent._rebuild_system_prompt()  # extra_tools_note 更新後重建 system message
        change_str = "、".join(changes) if changes else "（無變更）"
        self.notify(f"✅ {name} 已重載：{change_str}", timeout=4)

    async def _do_delete_agent(self, name: str) -> None:
        from core.agent_factory import list_agents, load_agent, delete_agent
        from widgets.agents_screen import ConfirmDeleteAgentScreen, DeleteAgentsScreen
        from widgets.tree import FileTree

        def _confirm_and_delete_many(scoped_names: list[str]) -> None:
            agents_meta: list[tuple[str, str, str]] = []
            for scoped_name in scoped_names:
                try:
                    data, adir = load_agent(scoped_name)
                except FileNotFoundError as e:
                    self.notify(str(e), severity="error")
                    return
                agent_name = data.get("name", adir.name)
                scope_path = "~/.kai/agents" if scoped_name.startswith("global:") else "agents"
                agents_meta.append((agent_name, scope_path, adir.name))

            def _on_confirmed(confirmed: bool) -> None:
                if not confirmed:
                    return
                deleted: list[str] = []
                for scoped_name, (agent_name, _, _) in zip(scoped_names, agents_meta):
                    try:
                        delete_agent(scoped_name)
                        deleted.append(agent_name)
                    except Exception as e:
                        self.notify(f"刪除失敗 {agent_name}：{e}", severity="error")
                if deleted:
                    self.notify(f"✅ 已刪除：{'、'.join(deleted)}", timeout=4)
                    try:
                        self.query_one("#file-tree", FileTree).refresh_tree()
                    except Exception:
                        pass

            self.push_screen(ConfirmDeleteAgentScreen(agents_meta), _on_confirmed)

        def _open_panel(agents_list: list[dict]) -> None:
            def _on_selected(selected: list[str]) -> None:
                if selected:
                    _confirm_and_delete_many(selected)
            self.push_screen(DeleteAgentsScreen(agents_list), _on_selected)

        if not name:
            agents = list_agents()
            if not agents:
                self.notify("沒有任何 agent 可以刪除", severity="warning")
                return
            _open_panel(agents)
        else:
            from core.agent_factory import find_all_agents
            matches = find_all_agents(name)
            if not matches:
                self.notify(f"找不到 agent：{name}", severity="error")
                return
            if len(matches) == 1:
                scoped = f"{matches[0]['scope']}:{matches[0]['dir']}"
                _confirm_and_delete_many([scoped])
            else:
                # 同名跨 scope，開面板讓使用者選要刪哪些
                _open_panel(matches)

    async def _do_list_agents(self) -> None:
        from core.agent_factory import list_agents
        from widgets.agents_screen import AgentsScreen
        agents = list_agents()
        if not agents:
            self.notify("agents/ 和 ~/.kai/agents/ 都是空的，用 /create-agent 建立第一個 agent", severity="warning")
            return

        async def _on_result(dir_name: str | None) -> None:
            if dir_name:
                await self._do_load_agent(dir_name)

        self.push_screen(AgentsScreen(agents), _on_result)
