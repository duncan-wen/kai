"""AgentsMixin — /create-agent, /load-agent, /list-agents 指令邏輯。"""

from __future__ import annotations

from textual import work
from textual.containers import VerticalScroll

from core.agent import Agent
from core.agent_factory import read_definition
from core.model_stream import CancelToken
from core.turn import (
    Done,
    StepReset,
    Token,
    ToolEvent,
    TurnEvent,
    TurnRequest,
    run_turn,
)
from mixins.streaming import error_text
from widgets.center_tabs import CenterTabs
from widgets.input import HistoryInput, PromptSpinner
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app import AgentMessage, ChatApp as _MixinBase
    from core.agent_factory import AgentDefinition
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
        my_tab = self.active_tab
        design_model = _cfg.CREATE_AGENT_MODEL or _cfg.MODEL
        self.mark_busy(my_tab)
        spinner = PromptSpinner(
            prompt,
            f"設計 agent 中（{design_model}）…",
            visible=lambda: self.active_tab == my_tab,
        ).start()
        await msgs.mount(
            Static(f"🏭 設計中：{description}", markup=False, classes="bash-output")
        )
        msgs.scroll_end(animate=False)
        try:
            data = await call_full_design(description)
            missing = validate_agent(data)
            if missing:
                self.notify(f"LLM 回覆缺少欄位：{missing}", severity="error")
                return
            adir = save_agent(data, global_scope=global_scope)
            scope_label = (
                f"~/.kai/agents/{adir.name}" if global_scope else f"agents/{adir.name}"
            )
            await msgs.mount(
                Static(
                    f"✅ Agent 已建立：{data['name']}（{scope_label}）\n"
                    f"   model: {data['model']}  nickname: {data.get('nickname', '—')}\n"
                    f"   輸入 /load-agent {adir.name} 載入",
                    markup=False,
                    classes="bash-output",
                )
            )
            msgs.scroll_end(animate=False)
            self.query_one("#file-tree", FileTree).refresh_tree()
        except Exception as e:
            self.notify(f"建立失敗：{e}", severity="error")
        finally:
            spinner.stop()
            self.unmark_busy(my_tab)

    def _apply_definition(self, agent: "Agent", defn: "AgentDefinition") -> list[str]:
        """套用 definition 並顯示警告（例如未知模型）。回傳變更清單。"""
        changes = agent.apply_definition(defn)
        for c in changes:
            if c.startswith("⚠️"):
                self.notify(c, severity="warning")
        return changes

    async def _do_load_agent(self, name: str) -> None:
        from core.agent_factory import find_agent, load_agent

        # scope 前綴（來自 AgentsScreen）直接傳入；否則做模糊比對
        if name.startswith(("local:", "global:")):
            resolved = name
        else:
            resolved = find_agent(name) or name
        try:
            defn, adir = load_agent(resolved)
        except (FileNotFoundError, ValueError) as e:
            self.notify(f"載入 agent 失敗：{e}", severity="error")
            return
        # 已開啟同一個 agent dir → 切換過去，不重複建 tab
        existing_id = self.agents.find_by_dir(adir)
        if existing_id:
            self._switch_to_tab(existing_id)
            self.notify(f"{defn.display_name} 已開啟，切換過去", timeout=2)
            return
        agent = Agent(
            model=self.agent.profile.name,
            history_file=adir / "history.json",
            agent_dir=adir,
        )
        self._apply_definition(agent, defn)
        tab_id = self.agents.add(agent)  # 配 tab id，並接上目前的 MCP
        scroll = await self.query_one(CenterTabs).add_agent_tab(
            tab_id, defn.display_name
        )
        self._switch_to_tab(tab_id)
        await self._render_history_into(scroll, agent)
        self.notify(f"✅ 已開啟 agent：{defn.name}", timeout=3)
        self._inject_interagent_tools(agent)
        # 若 config 有 greeting 且是全新對話，觸發 AI 主動開場
        greeting = defn.greeting.strip()
        fresh = not any(m.get("role") != "system" for m in agent.history)
        if greeting and fresh and self.ui_state.greeting_enabled:
            self._trigger_greeting(agent, scroll, greeting)

    def on_agent_message(self, event: "AgentMessage") -> None:
        """Textual event：將 AgentMessage 路由到目標 agent。"""
        self._dispatch_to_agent(event.sender, event.target, event.content)

    @work
    async def _dispatch_to_agent(self, sender: str, target: str, content: str) -> None:
        """找到目標 agent，顯示 inbox bubble，並跑一個 turn。"""
        import json as _json
        from widgets.chat import AIMessage
        from widgets.input import HistoryInput
        from textual.widgets import Static as _Static, Collapsible
        from loguru import logger

        # target 通常是 send_to_agent 給的 tab id；其他來源則依名稱查找
        from core.agent_registry import AgentRegistry

        target_tab_id = target if self.agents.get(target) is not None else ""
        if not target_tab_id:
            try:
                target_tab_id = self.agents.find(target)
            except LookupError as e:
                logger.warning(f"[inbox] dispatch: {e}")
                return
        agent = self.agents.get(target_tab_id)
        if agent is None:
            logger.warning(f"[inbox] dispatch: 目標 tab 已關閉：{target_tab_id}")
            return
        is_main_target = target_tab_id == AgentRegistry.MAIN_ID
        scroll_id = "#messages" if is_main_target else f"#msgs-{target_tab_id}"

        try:
            scroll = self.query_one(scroll_id, VerticalScroll)
        except Exception:
            logger.warning(f"[inbox] dispatch: scroll '{scroll_id}' 不存在")
            return

        # 目標 tab 標成忙碌：使用者在（或切到）那個 tab 時 prompt 會鎖住
        cancel = CancelToken()
        self.mark_busy(target_tab_id, cancel)
        prompt = self.query_one("#prompt", HistoryInput)

        # spinner：只在目標 agent 的 tab 是目前 tab 時才畫
        spinner = PromptSpinner(
            prompt,
            "等待回覆",
            visible=lambda: self.active_tab == target_tab_id,
        ).start()

        preview = content[:300] + ("…" if len(content) > 300 else "")
        await scroll.mount(
            Collapsible(
                _Static(preview, markup=False),
                title=f"📨 來自 {sender}",
                collapsed=True,
                classes="inbox-bubble",
            )
        )
        stream_msg: "AIMessage | None" = None
        cur_text = ""

        async def finalize(text: str) -> None:
            nonlocal stream_msg, cur_text
            if stream_msg is not None:
                await stream_msg.update_content(text, final=True)
            stream_msg = None
            cur_text = ""

        async def on_token(ev: Token) -> None:
            nonlocal stream_msg, cur_text
            spinner.label = "生成中"
            cur_text = ev.text
            if stream_msg is None:
                await scroll.mount(
                    _Static(
                        f"🤖 {agent.display_name}", classes="ai-header", markup=False
                    )
                )
                stream_msg = AIMessage()
                await scroll.mount(stream_msg)
            await stream_msg.update_content(cur_text)
            scroll.scroll_end(animate=False)

        async def on_tool(ev: ToolEvent) -> None:
            spinner.label = "工具回應中"
            await finalize(cur_text)
            name, args, tr = ev.name, ev.args, ev.result
            # 與 streaming 路徑一致：吃結構化旗標（含 exit code 警告級），不掃字串
            result = tr.output
            is_err = tr.is_error
            icon = "❌" if is_err else ("⚠️" if tr.is_warn else "🔧")
            card_cls = "tool-debug tool-debug-error" if is_err else "tool-debug"
            if not self.debug_tools:
                if is_err:
                    await scroll.mount(
                        Collapsible(
                            _Static(result[:600], markup=False),
                            title=f"{icon} {name}  [dim]工具錯誤[/dim]",
                            collapsed=True,
                            classes=card_cls,
                        )
                    )
                return
            args_str = _json.dumps(args, ensure_ascii=False, indent=2)
            body = f"ARGS:\n{args_str}\n\nRESULT:\n{result[:2000]}"
            await scroll.mount(
                Collapsible(
                    _Static(body, markup=False),
                    title=f"{icon} {name}",
                    collapsed=True,
                    classes=card_cls,
                )
            )
            scroll.scroll_end(animate=False)

        async def on_done(ev: Done) -> None:
            await finalize(ev.reply if ev.reason == "complete" else cur_text)
            if ev.reason == "tool_limit":
                await scroll.mount(_Static(ev.reply, classes="error-msg", markup=False))
            elif ev.reason == "error":
                logger.error(f"[inbox] {agent.display_name} 處理失敗：{ev.error}")
                await scroll.mount(
                    _Static(error_text(ev), classes="error-msg", markup=False)
                )
                self.notify(
                    f"{agent.display_name} 處理 {sender} 的訊息失敗：{ev.error.message}",
                    severity="error",
                )
            scroll.scroll_end(animate=False)

        async def sink(ev: TurnEvent) -> None:
            if isinstance(ev, Token):
                await on_token(ev)
            elif isinstance(ev, StepReset) and stream_msg is not None:
                await stream_msg.update_content("")
            elif isinstance(ev, ToolEvent):
                await on_tool(ev)
            elif isinstance(ev, Done):
                await on_done(ev)

        if is_main_target:
            full_content = content
        else:
            reply_to = "main" if sender == self.agents.main.display_name else sender
            full_content = (
                content
                + f'\n\n[系統]完成後必須呼叫 send_to_agent("{reply_to}", 結果摘要) 回報。'
            )

        logger.info(f"[inbox] dispatch: {sender} → {agent.display_name}")
        # 其他 agent 送進來的訊息也算新對話：目標 session 不再是 resumed session
        self._session_store(agent).mark_active()
        try:
            await run_turn(
                agent,
                TurnRequest(
                    {"role": "user", "content": full_content, "_inbox_sender": sender}
                ),
                sink,
                cancel,
            )
        except Exception as e:
            # @work 預設 exit_on_error：未接住的例外會讓整個 app 關閉
            logger.exception(f"[inbox] {agent.display_name} 處理失敗")
            await scroll.mount(
                _Static(f"❌ 錯誤：{e}", classes="error-msg", markup=False)
            )
            scroll.scroll_end(animate=False)
        finally:
            spinner.stop()
            self.unmark_busy(target_tab_id, cancel)

    @work
    async def _trigger_greeting(
        self, agent: "Agent", scroll: "VerticalScroll", greeting_prompt: str
    ) -> None:
        """讓 agent 主動說第一句話。trigger 不存入 history，只保留 assistant 回覆。"""
        from textual.widgets import Static as _Static
        from widgets.chat import AIMessage

        prompt = self.query_one("#prompt", HistoryInput)
        my_tab = self.agents.tab_id_of(agent) or self.active_tab
        cancel = CancelToken()
        self.mark_busy(my_tab, cancel)
        spinner = PromptSpinner(
            prompt,
            f"{agent.display_name} 開場中…",
            visible=lambda: self.active_tab == my_tab,
        ).start()
        stream_msg: AIMessage | None = None

        async def sink(ev: TurnEvent) -> None:
            nonlocal stream_msg
            if isinstance(ev, Token):
                if stream_msg is None:
                    await scroll.mount(
                        _Static(
                            f"🤖 {agent.display_name}",
                            classes="ai-header",
                            markup=False,
                        )
                    )
                    stream_msg = AIMessage()
                    await scroll.mount(stream_msg)
                await stream_msg.update_content(ev.text)
                scroll.scroll_end(animate=False)
            elif isinstance(ev, Done):
                if stream_msg is not None:
                    await stream_msg.update_content(
                        ev.reply if ev.reason == "complete" else "", final=True
                    )
                if ev.reason == "error":
                    self.notify(
                        f"greeting 失敗：{ev.error.message}", severity="warning"
                    )

        try:
            # trigger 訊息不存入 history，只保留 assistant 開場白
            await run_turn(
                agent,
                TurnRequest(
                    {"role": "user", "content": greeting_prompt}, persist="reply_only"
                ),
                sink,
                cancel,
            )
        except Exception as e:
            self.notify(f"greeting 失敗：{e}", severity="warning")
        finally:
            spinner.stop()
            self.unmark_busy(my_tab, cancel)
            if self.agent is agent:
                prompt.reset_prediction(f"傳訊息給 {agent.display_name}...")

    async def _do_reload_agent(self) -> None:
        """重新從 config.json 熱更新執行中的 agent：system_prompt、model、nickname 全部套用。
        若 history.json 已被刪除，同步清除 in-memory history（否則 save_history 會把舊記憶寫回）。
        """
        adir = self.agent._agent_dir
        if not adir:
            self.notify(
                "目前 agent 沒有 config.json（主 chat 不支援此指令）",
                severity="warning",
            )
            return
        config_path = adir / "config.json"
        if not config_path.exists():
            self.notify(f"找不到 {config_path}", severity="error")
            return
        try:
            defn = read_definition(config_path)
        except ValueError as e:
            self.notify(f"讀取 config.json 失敗：{e}", severity="error")
            return

        changes = self._apply_definition(self.agent, defn)
        self.agents.attach_mcp(self.agent)  # mcp_servers 可能改了
        # 以磁碟為準重載 history（不呼叫 save_history，避免覆寫使用者手動編輯）
        self.agent.reset_history()
        name = defn.display_name

        self.refresh_agent_view()

        # 重新渲染聊天畫面（history 可能已有增減）
        msgs = self._active_msgs()
        await msgs.remove_children()
        await self._render_history_into(msgs, self.agent)

        self._inject_interagent_tools(self.agent)
        self.agent._rebuild_system_prompt()  # extra_tools_note 更新後重建 system message
        change_str = "、".join(changes) if changes else "（無變更）"
        self.notify(f"✅ {name} 已重載：{change_str}", timeout=4)

    async def _do_delete_agent(self, name: str) -> None:
        from core.agent_factory import delete_agent, find_agent_dir, list_agents
        from widgets.agents_screen import ConfirmDeleteAgentScreen, DeleteAgentsScreen
        from widgets.tree import FileTree

        def _confirm_and_delete_many(scoped_names: list[str]) -> None:
            agents_meta: list[tuple[str, str, str]] = []
            for scoped_name in scoped_names:
                try:
                    adir = find_agent_dir(scoped_name)
                except FileNotFoundError as e:
                    self.notify(str(e), severity="error")
                    return
                agent_name = adir.name
                scope_path = (
                    "~/.kai/agents" if scoped_name.startswith("global:") else "agents"
                )
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
            self.notify(
                "agents/ 和 ~/.kai/agents/ 都是空的，用 /create-agent 建立第一個 agent",
                severity="warning",
            )
            return

        async def _on_result(dir_name: str | None) -> None:
            if dir_name:
                await self._do_load_agent(dir_name)

        self.push_screen(AgentsScreen(agents), _on_result)
