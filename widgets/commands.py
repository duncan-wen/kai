"""Command palette provider — all slash commands with descriptions."""
from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from textual.command import Hit, Hits, Provider

from core.models import REGISTRY

if TYPE_CHECKING:
    from app import ChatApp


# ── Palette helpers (sync callbacks) ──────────────────────────────────────────

def _fill_prompt(app: ChatApp, text: str) -> None:
    """填入文字並 focus prompt，用於需要補充參數的指令。"""
    from widgets.input import HistoryInput
    prompt = app.query_one("#prompt", HistoryInput)
    prompt.value = text
    prompt.cursor_position = len(text)
    prompt.focus()


def _toggle_debug(app: ChatApp) -> None:
    app._debug_tools = not app._debug_tools
    label = "🔧 開啟" if app._debug_tools else "關閉"
    app.notify(f"工具除錯模式：{label}", severity="information", timeout=3)
    app._update_prompt_title()


def _do_save(app: ChatApp) -> None:
    if not app._last_ephemeral:
        app.notify("沒有可保存的 /ask 回答", severity="warning")
    else:
        app.agent.history.extend(app._last_ephemeral)
        app.agent.save_history()
        app._last_ephemeral = None
        app.notify("已存入對話歷史", timeout=2)


# ── Provider ───────────────────────────────────────────────────────────────────

class ChatCommands(Provider):
    def _all_hits(self, app: ChatApp) -> list[Hit]:
        current = app.agent.profile.name
        think_state = "開啟" if app._thinking_active() else "關閉"
        debug_state = "開啟" if app._debug_tools else "關閉"

        hits: list[Hit] = [
            # ── 清除 / 歷史 ──────────────────────────────────────────────────
            Hit(1.0, "/clear  清除畫面",
                app.action_clear_screen,
                help="清除聊天畫面（不刪對話 history）"),
            Hit(1.0, "/cls  清除記憶並封存",
                app.action_clear,
                help="清除對話記錄並封存（可 /resume 恢復）"),
            Hit(0.9, "/resume  列出 archive",
                partial(app._handle_resume, ""),
                help="開啟 archive 選擇器，點選直接載入"),
            Hit(0.9, "/compact  壓縮對話歷史",
                app.action_compact,
                help="用 Gemini 壓縮對話 context，釋放 token 空間"),
            Hit(0.9, "/save  儲存 /ask 回答",
                partial(_do_save, app),
                help="將上一次 /ask 的回答永久存入對話歷史"),

            # ── 提問 / 記憶 ──────────────────────────────────────────────────
            Hit(0.9, "/ask  臨時提問",
                partial(_fill_prompt, app, "/ask "),
                help="臨時提問，不污染對話歷史"),
            Hit(0.9, "/remember  記住規則",
                partial(_fill_prompt, app, "/remember "),
                help="寫入 .kai/notes.md 讓 AI 永久記住"),
            Hit(0.9, "/image  傳送圖片",
                partial(_fill_prompt, app, "/image "),
                help="傳送圖片給視覺模型：/image <路徑> [問題]"),

            # ── 模式切換 ─────────────────────────────────────────────────────
            Hit(0.9, f"/think  Thinking 模式（{think_state}）",
                app.action_toggle_think,
                help="切換 extended thinking"),
            Hit(0.9, f"/debug  工具除錯模式（{debug_state}）",
                partial(_toggle_debug, app),
                help="顯示工具呼叫參數與回應 card"),

            # ── 目錄 ─────────────────────────────────────────────────────────
            Hit(0.8, "/cd  切換工作目錄",
                partial(_fill_prompt, app, "/cd "),
                help="切換工作目錄並重載歷史"),

            # ── MCP ──────────────────────────────────────────────────────────
            Hit(0.8, "/mcp-list  列出 MCP 工具",
                app._do_mcp_list,
                help="顯示所有已連線 MCP server 與工具說明"),
            Hit(0.8, "/mcp-reload  重新載入 MCP",
                app._do_mcp_reload,
                help="重新啟動所有 MCP server 連線"),
            Hit(0.8, "/mcp-add  新增 MCP server",
                partial(_fill_prompt, app, "/mcp-add "),
                help="新增：/mcp-add <name> <command> [args...]  或  --url <url>"),
            Hit(0.8, "/mcp-del  移除 MCP server",
                partial(_fill_prompt, app, "/mcp-del "),
                help="移除：/mcp-del <name>"),

            # ── Agent ─────────────────────────────────────────────────────────
            Hit(0.8, "/list-agents  列出所有 agents",
                app._do_list_agents,
                help="列出 agents/ 目錄下所有已建立的 agent"),
            Hit(0.8, "/load-agent  載入 agent",
                partial(_fill_prompt, app, "/load-agent "),
                help="在新 tab 載入 agent：/load-agent <目錄名>"),
            Hit(0.8, "/create-agent  設計新 agent",
                partial(_fill_prompt, app, "/create-agent "),
                help="用 AI 根據描述設計並建立 agent"),

            # ── TTS ──────────────────────────────────────────────────────────
            Hit(0.8, "/tts kokoro-api  切換 TTS 引擎（Docker 8885）",
                partial(_fill_prompt, app, "/tts kokoro-api"),
                help="kokoro-fastapi Docker server（localhost:8885），OpenAI 格式"),
            Hit(0.8, "/tts melotts  切換 TTS 引擎（Docker 8886）",
                partial(_fill_prompt, app, "/tts melotts"),
                help="切換語音引擎：melotts / kokoro-api / kokoro / edge-tts / off"),
            Hit(0.8, "/tts kokoro  切換 TTS 引擎（本地 ONNX）",
                partial(_fill_prompt, app, "/tts kokoro"),
                help="切換語音引擎：melotts / kokoro-api / kokoro / edge-tts / off"),
            Hit(0.8, "/tts edge-tts  切換 TTS 引擎（雲端）",
                partial(_fill_prompt, app, "/tts edge-tts"),
                help="切換語音引擎：melotts / kokoro-api / kokoro / edge-tts / off"),
            Hit(0.8, "/tts off  關閉語音",
                partial(_fill_prompt, app, "/tts off"),
                help="關閉目前 agent 的 TTS 語音"),

            # ── 暱稱 / session 命名 ──────────────────────────────────────────────
            Hit(0.8, "/rename  命名目前 session",
                partial(_fill_prompt, app, "/rename "),
                help="為目前 session 命名，封存時自動儲存"),
            Hit(0.8, "/nickname  設定模型暱稱",
                partial(_fill_prompt, app, "/nickname "),
                help="查詢或設定目前模型的顯示暱稱"),
        ]

        # ── 模型切換 ─────────────────────────────────────────────────────────
        for model_name in REGISTRY:
            is_cur = model_name == current
            hits.append(Hit(
                1.0 if is_cur else 0.8,
                f"/model {model_name}{'  ✓' if is_cur else ''}",
                partial(app.action_switch_model, model_name),
                help="目前使用中" if is_cur else f"切換到 {model_name}",
            ))

        return hits

    async def discover(self) -> Hits:
        for hit in self._all_hits(self.app):  # type: ignore[arg-type]
            yield hit

    async def search(self, query: str) -> Hits:
        app: ChatApp = self.app  # type: ignore[assignment]
        matcher = self.matcher(query)
        for hit in self._all_hits(app):
            label = hit.match_display if isinstance(hit.match_display, str) else str(hit.match_display)
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, matcher.highlight(label), hit.command, help=hit.help)
