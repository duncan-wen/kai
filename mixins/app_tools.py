"""AppToolsMixin — 將 app-level 指令暴露為 AI tools，注入 main agent。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent import Agent
    from app import ChatApp as _MixinBase
else:
    _MixinBase = object  # type: ignore[assignment,misc]

from .app_tool_schemas import (
    ADD_MCP_TOOL as _ADD_MCP_TOOL,
    CHANGE_DIR_TOOL as _CHANGE_DIR_TOOL,
    CLOSE_AGENT_TOOL as _CLOSE_AGENT_TOOL,
    COMPACT_HISTORY_TOOL as _COMPACT_HISTORY_TOOL,
    DELETE_AGENT_TOOL as _DELETE_AGENT_TOOL,
    DESIGN_SYSTEM_PROMPT_TOOL as _DESIGN_SYSTEM_PROMPT_TOOL,
    LIST_AGENTS_TOOL as _LIST_AGENTS_TOOL,
    LIST_MCP_TOOL as _LIST_MCP_TOOL,
    LIST_OPEN_AGENTS_TOOL as _LIST_OPEN_AGENTS_TOOL,
    LOAD_AGENT_TOOL as _LOAD_AGENT_TOOL,
    RELOAD_AGENT_TOOL as _RELOAD_AGENT_TOOL,
    RELOAD_CONFIG_TOOL as _RELOAD_CONFIG_TOOL,
    RELOAD_MCP_TOOL as _RELOAD_MCP_TOOL,
    REMEMBER_TOOL as _REMEMBER_TOOL,
    REMOVE_MCP_TOOL as _REMOVE_MCP_TOOL,
    RENAME_SESSION_TOOL as _RENAME_SESSION_TOOL,
    SET_TTS_TOOL as _SET_TTS_TOOL,
    SEND_TO_AGENT_TOOL as _SEND_TO_AGENT_TOOL,
    TOGGLE_GREETING_TOOL as _TOGGLE_GREETING_TOOL,
)

class AppToolsMixin(_MixinBase):

    def _inject_app_tools(self, agent: "Agent") -> None:
        """將 app-level tools 注入 agent。只對 main agent 呼叫一次（或切換模型後重注入）。"""
        from core.models import REGISTRY
        models_str = "、".join(REGISTRY.keys())

        create_agent_tool = {
            "type": "function",
            "function": {
                "name": "create_agent",
                "description": (
                    "建立一個新的 AI agent。當用戶說「幫我建一個 agent」、「我需要專門做 X 的 AI」時使用。"
                    "請自行設計完整的 system_prompt，不需要另外詢問。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name":          {"type": "string", "description": "agent 顯示名稱（中文可以）"},
                        "nickname":      {"type": "string", "description": "簡短暱稱 2-4 字，省略則同 name"},
                        "model":         {"type": "string", "description": f"模型名稱，可用：{models_str}"},
                        "system_prompt": {"type": "string", "description": "完整 system prompt；請先呼叫 design_system_prompt 取得，**必須原封不動傳入，包含 {DIFY_DATASETS} 等佔位符，不得刪減任何段落**"},
                        "description":   {"type": "string", "description": "一行說明這個 agent 的用途"},
                        "greeting":      {"type": "string", "description": "首次對話時 AI 主動開場的 prompt，可省略"},
                        "global_scope":  {"type": "boolean", "description": "**預設必須用 false**，只有用戶明確說「跨專案」或「全域」時才用 true。true = 存到 ~/.kai/agents/（跨專案）；false = 存到 cwd/agents/"},
                        "datasets": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Dify 知識庫 UUID 列表。design_system_prompt 回傳有 datasets 時填入，否則省略。",
                        },
                        "tts": {
                            "type": "object",
                            "description": (
                                "語音設定。用戶提到「需要語音」「語音朗讀」「TTS」「說話」時填入，否則省略。"
                                "格式：{\"engine\": \"edge-tts\", \"voice\": \"zh-TW-HsiaoChenNeural\", \"player\": \"powershell\", \"speed\": 1.0}。"
                                "中文 voice：zh-TW-HsiaoChenNeural（女）/ zh-TW-YunJheNeural（男）；英文：en-US-AriaNeural。"
                            ),
                        },
                    },
                    "required": ["name", "model", "system_prompt"],
                },
            },
        }

        switch_model_tool = {
            "type": "function",
            "function": {
                "name": "switch_model",
                "description": f"切換 AI 模型。當用戶說「換成 XXX 模型」、「用 gemini 回答」時使用。可用：{models_str}。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string", "description": f"目標模型名稱，可用：{models_str}"},
                    },
                    "required": ["model"],
                },
            },
        }

        agent.extra_tools_note = (
            "\n\n## 🖥️ App 操作工具（extra tools）\n"
            "以下工具直接操作應用程式介面（開啟/關閉 tab、切換目錄、寫入檔案等）。\n"
            "**嚴禁用文字假裝已執行——必須呼叫對應工具，操作才會真正發生。**\n"
            "收到「開啟/載入 agent」請求時，必須先呼叫 list_open_agents 確認目前狀態，再決定是否呼叫 load_agent。\n"
            "收到「叫/通知/委派/傳任務給某 agent」的請求時，先呼叫 load_agent（已開啟時僅切換 tab，不重複建），再呼叫 send_to_agent。\n"
            "可用工具：list_open_agents、list_agents、design_system_prompt、create_agent、load_agent、"
            "close_agent、delete_agent、reload_agent、change_dir、switch_model、remember、rename_session、"
            "reload_config、compact_history、list_mcp、add_mcp、remove_mcp、reload_mcp、set_tts、send_to_agent。"
        )

        agent.extra_tools = [
            _LIST_OPEN_AGENTS_TOOL,
            _LIST_AGENTS_TOOL,
            _DESIGN_SYSTEM_PROMPT_TOOL,
            create_agent_tool,
            _LOAD_AGENT_TOOL,
            _CLOSE_AGENT_TOOL,
            _DELETE_AGENT_TOOL,
            _RELOAD_AGENT_TOOL,
            _CHANGE_DIR_TOOL,
            switch_model_tool,
            _REMEMBER_TOOL,
            _RENAME_SESSION_TOOL,
            _RELOAD_CONFIG_TOOL,
            _COMPACT_HISTORY_TOOL,
            _LIST_MCP_TOOL,
            _ADD_MCP_TOOL,
            _REMOVE_MCP_TOOL,
            _RELOAD_MCP_TOOL,
            _SET_TTS_TOOL,
            _TOGGLE_GREETING_TOOL,
            _SEND_TO_AGENT_TOOL,
        ]
        agent.extra_tools_map = {
            "list_open_agents":      self._app_list_open_agents,
            "list_agents":           self._app_list_agents,
            "design_system_prompt":  self._app_design_system_prompt,
            "create_agent":          self._app_create_agent,
            "load_agent":       self._app_load_agent,
            "close_agent":      self._app_close_agent,
            "delete_agent":     self._app_delete_agent,
            "reload_agent":     self._app_reload_agent,
            "change_dir":       self._app_change_dir,
            "switch_model":     self._app_switch_model,
            "remember":         self._app_remember,
            "rename_session":   self._app_rename_session,
            "reload_config":    self._app_reload_config,
            "compact_history":  self._app_compact_history,
            "list_mcp":         self._app_list_mcp,
            "add_mcp":          self._app_add_mcp,
            "remove_mcp":       self._app_remove_mcp,
            "reload_mcp":       self._app_reload_mcp,
            "set_tts":          self._app_set_tts,
            "toggle_greeting":  self._app_toggle_greeting,
            "send_to_agent":    self._app_send_to_agent,
        }

    # ── callbacks ─────────────────────────────────────────────────────────────

    async def _app_list_open_agents(self) -> str:
        from textual.widgets import TabbedContent
        tabs = self.tab_state.agent_tabs
        if not tabs:
            return "目前沒有任何已開啟的 agent tab。"
        try:
            tc = self.query_one("#center-tabs", TabbedContent)
            active_tab = tc.active
        except Exception:
            active_tab = None
        lines = ["目前已開啟的 agent tab："]
        for tab_id, agent in tabs.items():
            name = agent.display_name
            active_mark = "（目前聚焦）" if tab_id == active_tab else ""
            lines.append(f"- {name}  [tab_id: {tab_id}]{active_mark}")
        return "\n".join(lines)

    async def _app_design_system_prompt(self, name: str, description: str) -> str:
        import json
        from core.agent_factory import call_design_model
        import core.config as _cfg
        design_model = _cfg.CREATE_AGENT_MODEL or _cfg.MODEL
        self.notify(f"設計 system prompt 中…（{design_model}）", timeout=30)
        system_prompt, greeting, extras = await call_design_model(name, description)
        result = {"system_prompt": system_prompt, "greeting": greeting, **extras}
        return json.dumps(result, ensure_ascii=False)

    async def _app_list_agents(self) -> str:
        from core.agent_factory import list_agents
        agents = list_agents()
        if not agents:
            return "目前沒有任何 agent，可以用 create_agent 建立。"
        lines = ["可用的 agent："]
        for a in agents:
            scope = "本地" if a["scope"] == "local" else "全域"
            nick = f"暱稱：{a['nickname']}，" if a.get("nickname") else ""
            desc = f" — {a['description']}" if a.get("description") else ""
            lines.append(f"- {a['name']}（{nick}模型：{a['model']}，{scope}）{desc}")
        return "\n".join(lines)

    async def _app_create_agent(
        self,
        name: str,
        model: str,
        system_prompt: str,
        nickname: str = "",
        description: str = "",
        greeting: str = "",
        global_scope: bool = False,
        tts: "dict | None" = None,
        datasets: "list | None" = None,
    ) -> str:
        from core.agent_factory import save_agent, validate_agent
        from widgets.tree import FileTree
        data: dict = {
            "name": name,
            "nickname": nickname or name,
            "model": model,
            "system_prompt": system_prompt,
            "description": description,
            "greeting": greeting,
            "mcp_servers": {},
            "history_keep": 15,
        }
        if tts:
            data["tts"] = tts
        if datasets:
            data["datasets"] = datasets
        missing = validate_agent(data)
        if missing:
            return f"缺少必要欄位：{missing}"
        adir = save_agent(data, global_scope=global_scope)
        try:
            self.query_one("#file-tree", FileTree).refresh_tree()
        except Exception:
            pass
        scope_label = f"~/.kai/agents/{adir.name}" if global_scope else f"agents/{adir.name}"
        return f"✅ 已建立 agent：{name}（{scope_label}）。可告訴我「載入 {name}」來開啟。"

    async def _app_load_agent(self, name: str) -> str:
        from core.agent_factory import find_agent
        from textual.widgets import TabbedContent
        # 若已開啟，直接切換 tab
        q = name.lower()
        for tab_id, agent in self.tab_state.agent_tabs.items():
            dn = agent.display_name.lower()
            nick = (agent.nickname or "").lower()
            if q == dn or q == nick or q in dn or (nick and q in nick):
                try:
                    tc = self.query_one("#center-tabs", TabbedContent)
                    tc.active = tab_id
                except Exception:
                    pass
                return f"✅ {agent.display_name} 已開啟中，已切換過去。"
        resolved = find_agent(name)
        if not resolved:
            return f"找不到 agent：{name}，可用 list_agents 查詢所有可用 agent。"
        try:
            await self._do_load_agent(resolved)
            return f"✅ 已開啟 agent：{name}"
        except Exception as e:
            return f"開啟失敗：{e}"

    async def _app_close_agent(self, name: str = "") -> str:
        if not name:
            if not self.tab_state.agent_tabs:
                return "目前沒有開啟中的 agent tab"
            if len(self.tab_state.agent_tabs) == 1:
                tab_id, ag = next(iter(self.tab_state.agent_tabs.items()))
                agent_name = ag.display_name
                await self._maybe_close_tab(tab_id)
                return f"✅ 已關閉 agent：{agent_name}"
            names = "、".join(ag.display_name for ag in self.tab_state.agent_tabs.values())
            return f"目前有多個開啟中的 agent：{names}。請指定要關閉哪一個。"
        q = name.lower()
        for tab_id, ag in list(self.tab_state.agent_tabs.items()):
            if q in ag.display_name.lower() or (ag.nickname and q in ag.nickname.lower()):
                agent_name = ag.display_name
                await self._maybe_close_tab(tab_id)
                return f"✅ 已關閉 agent：{agent_name}"
        return f"找不到開啟中的 agent：{name}"

    async def _app_delete_agent(self, name: str) -> str:
        from core.agent_factory import find_all_agents, delete_agent
        from widgets.tree import FileTree
        matches = find_all_agents(name)
        if not matches:
            return f"找不到 agent：{name}"
        if len(matches) > 1:
            names_str = "、".join(f"{a['name']}（{a['scope']}）" for a in matches)
            return f"找到多個符合的 agent：{names_str}。請提供更精確的名稱再重試。"
        scoped = f"{matches[0]['scope']}:{matches[0]['dir']}"
        try:
            delete_agent(scoped)
            try:
                self.query_one("#file-tree", FileTree).refresh_tree()
            except Exception:
                pass
            return f"✅ 已刪除 agent：{matches[0]['name']}"
        except Exception as e:
            return f"刪除失敗：{e}"

    async def _app_change_dir(self, path: str) -> str:
        try:
            await self._do_cd(path)
            return f"✅ 已切換至 {os.getcwd()}"
        except Exception as e:
            return f"切換失敗：{e}"

    async def _app_switch_model(self, model: str) -> str:
        try:
            return await self.action_switch_model(model)
        except Exception as e:
            return f"切換失敗：{e}"

    async def _app_remember(self, note: str) -> str:
        notes_path = Path(".kai/notes.md")
        notes_path.parent.mkdir(exist_ok=True)
        with notes_path.open("a", encoding="utf-8") as f:
            f.write(f"\n- {note}")
        self.agent._rebuild_system_prompt()
        return f"✅ 已記住：{note}"

    async def _app_rename_session(self, name: str) -> str:
        self._do_rename(name)
        return f"✅ Session 命名為「{name}」"

    async def _app_reload_agent(self) -> str:
        try:
            await self._do_reload_agent()
            return "✅ agent 已重新載入"
        except Exception as e:
            return f"重新載入失敗：{e}"

    async def _app_reload_config(self) -> str:
        try:
            await self.action_reload_config()
            return "✅ .kai/config.json 已重新載入"
        except Exception as e:
            return f"重新載入失敗：{e}"

    async def _app_compact_history(self) -> str:
        if not self.agent._gemini and not self.agent._deepseek:
            return "需要 GEMINI_API_KEY 或 DEEPSEEK_API_KEY 才能壓縮"
        try:
            summary = await self.agent.compact_history()
            msgs = self._active_msgs()
            await msgs.remove_children()
            await self._render_history_into(msgs, self.agent)
            preview = summary[:80] + "…" if len(summary) > 80 else summary
            return f"✅ 對話已壓縮。摘要：{preview}"
        except Exception as e:
            return f"壓縮失敗：{e}"

    async def _app_list_mcp(self) -> str:
        mcp = self.agent.mcp_client
        if not mcp or not mcp.tool_names:
            return "目前沒有連線中的 MCP server"
        lines = ["已連線的 MCP server："]
        for server, tools in mcp.tools_by_server.items():
            names = "、".join(t["name"] for t in tools) if tools else "（無工具）"
            lines.append(f"- {server}（{len(tools)} 個工具）：{names}")
        return "\n".join(lines)

    async def _app_add_mcp(self, name: str, command: str = "", args: list | None = None, url: str = "") -> str:
        import json
        from core.mcp_client import GLOBAL_MCP_FILE
        if not command and not url:
            return "需要提供 command 或 url"
        data: dict = {}
        if GLOBAL_MCP_FILE.exists():
            try:
                data = json.loads(GLOBAL_MCP_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        entry: dict = {"type": "http", "url": url} if url else {"command": command, "args": args or []}
        data.setdefault("mcpServers", {})[name] = entry
        GLOBAL_MCP_FILE.parent.mkdir(parents=True, exist_ok=True)
        GLOBAL_MCP_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"✅ 已新增 MCP server：{name}。呼叫 reload_mcp 生效。"

    async def _app_remove_mcp(self, name: str) -> str:
        import json
        from core.mcp_client import GLOBAL_MCP_FILE
        if not GLOBAL_MCP_FILE.exists():
            return "~/.kai/mcp_servers.json 不存在"
        try:
            data = json.loads(GLOBAL_MCP_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            return f"讀取失敗：{e}"
        servers = data.get("mcpServers", {})
        if name not in servers:
            available = "、".join(servers.keys()) or "（空）"
            return f"找不到 server：{name}。目前有：{available}"
        del servers[name]
        GLOBAL_MCP_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"✅ 已移除 MCP server：{name}。呼叫 reload_mcp 生效。"

    async def _app_reload_mcp(self) -> str:
        try:
            await self._do_mcp_reload()
            n = len(self.agent.mcp_client.tool_names) if self.agent.mcp_client else 0
            return f"✅ MCP 重新載入完成，共 {n} 個工具"
        except Exception as e:
            return f"重新載入失敗：{e}"

    def _inject_interagent_tools(self, agent: "Agent") -> None:
        """為 subagent 注入通信工具：send_to_agent、load_agent、list_open_agents。"""
        _self = self
        _caller = agent

        async def _send(name: str, message: str) -> str:
            return await _self._app_send_to_agent(name, message, caller=_caller)

        async def _load(name: str) -> str:
            return await _self._app_load_agent(name)

        agent.extra_tools = [_SEND_TO_AGENT_TOOL, _LOAD_AGENT_TOOL, _LIST_OPEN_AGENTS_TOOL]
        agent.extra_tools_map = {
            "send_to_agent": _send,
            "load_agent": _load,
            "list_open_agents": _self._app_list_open_agents,
        }
        agent.extra_tools_note = (
            "\n\n## \U0001f91d Agent 通信工具\n"
            "要傳指令給其他 agent：先呼叫 load_agent（已開啟時僅切換 tab，不重複建），再呼叫 send_to_agent。\n"
            "若不確定 agent 名稱，先用 list_open_agents 查詢。"
        )

    async def _app_send_to_agent(self, name: str, message: str, caller: "Agent | None" = None) -> str:
        from loguru import logger
        from app import AgentMessage
        sender_name = caller.display_name if caller else (self.tab_state.main_agent or self.agent).display_name
        q = name.strip().lower()
        main = self.tab_state.main_agent
        if main and q in ("main", "chat", main.display_name.lower()):
            logger.info(f"[inbox] send: {sender_name} → main")
            self.post_message(AgentMessage(sender=sender_name, target="main", content=message))
            return "✅ 訊息已傳送給 main agent"
        for ag in self.tab_state.agent_tabs.values():
            nick = (ag.nickname or "").lower()
            dir_name = (ag._agent_dir.name if ag._agent_dir else "").lower()
            if q in nick or nick in q or q in dir_name or dir_name in q:
                logger.info(f"[inbox] send: {sender_name} → {ag.display_name}")
                self.post_message(AgentMessage(sender=sender_name, target=ag.display_name, content=message))
                return f"✅ 訊息已傳送給 {ag.display_name}"
        logger.warning(f"[inbox] send_to_agent 找不到 '{name}'，已開啟：{[a.display_name for a in self.tab_state.agent_tabs.values()]}")
        return f"❌ 找不到已開啟的 agent：{name}。請先用 load_agent 載入。"

    async def _app_set_tts(self, engine: str) -> str:
        self._do_tts_switch(engine)
        if engine == "off":
            return "✅ TTS 已關閉"
        return f"✅ TTS 已切換為：{engine}"

    async def _app_toggle_greeting(self, enabled: bool) -> str:
        self.ui_state.greeting_enabled = enabled
        label = "開啟" if enabled else "關閉"
        self.notify(f"Greeting：{label}", severity="information", timeout=3)
        return f"✅ Greeting 已{label}（下次載入 agent 時生效）"
