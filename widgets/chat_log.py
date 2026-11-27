"""ChatLog：一個 tab 的訊息容器，以及把 turn 事件畫上去的 ReplySink。

所有訊息（使用者泡泡、inbox、通知、錯誤、`!bash` 輸出、AI 回覆）都經由這裡 mount，
「貼底才 auto-scroll」的規則也只寫在這裡。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from rich.text import Text
from textual.containers import Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Collapsible, Markdown, Static

import core.config as _cfg
from core.turn import (
    Compacted,
    Compacting,
    Done,
    StepReset,
    ThinkingDone,
    ThinkingStarted,
    Token,
    ToolEvent,
    TurnEvent,
    TurnStats,
    inbox_sender,
    user_text,
)

from .chat import AIMessage, ImageBubble

if TYPE_CHECKING:
    from core.agent import Agent


def format_stats(stats: TurnStats) -> str:
    """turn 結束後的統計行：思考秒數、耗時、tokens、context 使用率、cache 命中。"""
    parts: list[str] = []
    if stats.thinking_s:
        parts.append(f"思考 {stats.thinking_s:.1f}s")
    parts.append(f"共 {stats.elapsed:.1f}s")
    u = stats.usage
    if u.gen_tokens:
        parts.append(f"{u.gen_tokens} tokens")
    prompt_tok = u.prompt_tokens
    if prompt_tok:
        if u.num_ctx:
            pct = prompt_tok * 100 // u.num_ctx
            parts.append(f"ctx {pct}% ({prompt_tok}/{u.num_ctx})")
        else:
            parts.append(f"輸入 {prompt_tok} tokens")
        if u.cache_hit is not None:
            parts.append(f"cache {u.cache_hit * 100 // prompt_tok}%")
    return " · ".join(parts)


_ERROR_PREFIX = {
    "timeout": "⏱ 模型無回應（逾時）：",
    "overflow": "❌ Context 已滿：",
    "quota": "❌ ",
    "other": "❌ 錯誤：",
}


def error_text(done: Done) -> str:
    assert done.error is not None
    return _ERROR_PREFIX[done.error.kind] + done.error.message


def _preview(text: str, cap: int) -> str:
    return text[:cap] + ("…" if len(text) > cap else "")


class ChatLog(VerticalScroll):
    """一個 tab 的訊息流。"""

    # ── 捲動 ──────────────────────────────────────────────────────────────────

    def at_bottom(self) -> bool:
        return self.scroll_y >= self.max_scroll_y - 3

    async def append(self, *widgets: Widget, follow: bool | None = None) -> None:
        """mount 到最後；`follow=None` 表示貼底才捲，True 一律捲到底。"""
        stick = self.at_bottom() if follow is None else follow
        await self.mount(*widgets)
        if stick:
            self.scroll_end(animate=False)

    # ── 非 turn 的訊息 ────────────────────────────────────────────────────────

    @staticmethod
    def ai_header(name: str) -> Static:
        return Static(f"🤖 {name}", classes="ai-header", markup=False)

    def _user_bubble(self, text: str) -> Static:
        body = Text.assemble((_cfg.USERNAME, "bold"), "\n", text)
        return Static(body, markup=False, classes="user-bubble")

    def _inbox_bubble(self, sender: str, text: str) -> Collapsible:
        return Collapsible(
            Static(_preview(text, 300), markup=False),
            title=f"📨 來自 {sender}",
            collapsed=True,
            classes="inbox-bubble",
        )

    async def add_user(self, text: str) -> None:
        await self.append(self._user_bubble(text), follow=True)

    async def add_command(self, cmd: str) -> None:
        """`!bash` 指令泡泡。"""
        body = Text.assemble(("$", "bold"), " ", cmd)
        await self.append(
            Static(body, markup=False, classes="user-bubble"), follow=True
        )

    async def add_image(self, path: str, question: str) -> None:
        await self.append(ImageBubble(path, question), follow=True)

    async def add_inbox(self, sender: str, text: str) -> None:
        await self.append(self._inbox_bubble(sender, text), follow=True)

    async def add_output(self, text: str | Text = "") -> Static:
        """指令輸出區塊（`!bash`、/create-agent）；回傳 widget 供之後 `update`。"""
        out = Static(text, markup=False, classes="bash-output")
        await self.append(out, follow=True)
        return out

    async def add_notice(self, text: str) -> None:
        await self.append(Static(text, markup=False, classes="notice"), follow=True)

    async def add_error(self, text: str) -> None:
        await self.append(Static(text, markup=False, classes="error-msg"), follow=True)

    async def render_history(self, agent: "Agent", *, clear: bool = True) -> None:
        """把 agent.history 畫出來；`clear=False` 時接在既有內容後面（例如歡迎訊息）。"""
        if clear:
            await self.remove_children()
        widgets: list = []
        ai_updates: list[tuple[AIMessage, str]] = []
        for msg in agent.history:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "user":
                text = user_text(msg)
                if not text:
                    continue
                sender = inbox_sender(msg)
                widgets.append(
                    self._inbox_bubble(sender, text)
                    if sender
                    else self._user_bubble(text)
                )
            elif role == "assistant" and content:
                widgets.append(self.ai_header(agent.display_name))
                ai_msg = AIMessage()
                widgets.append(ai_msg)
                ai_updates.append((ai_msg, content))
        if widgets:
            await self.mount(*widgets)  # 一次 layout pass，而非 N 次
        for ai_msg, content in ai_updates:
            await ai_msg.update_content(content, final=True)
        self.scroll_end(animate=False)

    # ── turn ──────────────────────────────────────────────────────────────────

    def start_reply(
        self,
        agent: "Agent",
        *,
        debug: bool,
        on_phase: Callable[[str], None] | None = None,
    ) -> ReplySink:
        """回傳一個 turn 的 sink（直接傳給 run_turn）。`on_phase` 收到「生成中」等階段文字。"""
        return ReplySink(self, agent, debug=debug, on_phase=on_phase)


class ReplySink:
    """把一個 turn 的事件畫到 ChatLog：思考、串流泡泡、工具卡片、通知、stats。"""

    _RENDER_INTERVAL = 0.1  # 串流中最多每 0.1s 重畫一次

    def __init__(
        self,
        log: ChatLog,
        agent: "Agent",
        *,
        debug: bool,
        on_phase: Callable[[str], None] | None = None,
    ) -> None:
        self._log = log
        self._agent = agent
        self._debug = debug
        self._on_phase = on_phase or (lambda _label: None)
        self._bubble: AIMessage | None = None
        self._text = ""  # 本 step 目前可見的文字（已剝 <next>）
        self._last_render = 0.0

    async def __call__(self, ev: TurnEvent) -> None:
        match ev:
            case ThinkingStarted():
                self._on_phase("思考中")
            case ThinkingDone():
                await self._thinking(ev.text)
            case Token():
                self._on_phase("生成中")
                await self._token(ev.text)
            case StepReset():
                # 後端重試本 step：已顯示的文字作廢
                self._text = ""
                if self._bubble is not None:
                    await self._bubble.update_content("")
            case ToolEvent():
                self._on_phase("工具回應中")
                await self._finalize(self._text)
                await self._tool(ev)
            case Compacting():
                self._on_phase("壓縮中")
                await self._finalize(self._text)
                await self._log.add_notice("⚡ Context 已滿，正在壓縮對話歷史…")
            case Compacted():
                await self._log.add_notice("✅ 已壓縮，繼續對話")
            case Done():
                await self._done(ev)

    async def _finalize(self, content: str) -> None:
        """把目前串流中的泡泡定稿成 Markdown。"""
        if self._bubble is not None:
            await self._bubble.update_content(content, final=True)
        self._bubble = None
        self._text = ""

    async def _thinking(self, text: str) -> None:
        await self._log.append(
            Collapsible(
                Static(_preview(text, 2000), markup=False),
                title="💡 思考過程",
                collapsed=True,
                classes="thinking",
            )
        )

    async def _token(self, text: str) -> None:
        self._text = text
        if self._bubble is None:
            self._bubble = AIMessage()
            await self._log.append(
                ChatLog.ai_header(self._agent.display_name), self._bubble
            )
        now = time.monotonic()
        if now - self._last_render >= self._RENDER_INTERVAL:
            self._last_render = now
            stick = self._log.at_bottom()
            await self._bubble.update_content(text)
            if stick:
                self._log.scroll_end(animate=False)

    async def _tool(self, ev: ToolEvent) -> None:
        # 錯誤判定完全來自分發層的結構化旗標，不掃描輸出字串
        tr = ev.result
        icon = "❌" if tr.is_error else ("⚠️" if tr.is_warn else "🔧")
        classes = "tool-debug tool-debug-error" if tr.is_error else "tool-debug"
        if not self._debug:
            # 非 debug 模式只顯示錯誤卡片
            if tr.is_error:
                args = _preview(json.dumps(ev.args, ensure_ascii=False), 300)
                await self._log.append(
                    Collapsible(
                        Static(f"參數：{args}\n\n{tr.output[:600]}", markup=False),
                        title=f"{icon} {ev.name}  [dim]工具錯誤[/dim]",
                        collapsed=True,
                        classes=classes,
                    )
                )
            return
        args = json.dumps(ev.args, ensure_ascii=False, indent=2)
        cap = 1000
        result = tr.output[:cap] + (
            f"\n…（共 {len(tr.output)} 字元）" if len(tr.output) > cap else ""
        )
        await self._log.append(
            Collapsible(
                Vertical(
                    Markdown(f"**參數**\n```json\n{args}\n```"),
                    Static(result, markup=False, classes="tool-result-body"),
                ),
                title=f"{icon} {ev.name}",
                collapsed=not tr.is_error,
                classes=classes,
            )
        )

    async def _done(self, ev: Done) -> None:
        await self._finalize(ev.reply if ev.reason == "complete" else self._text)
        match ev.reason:
            case "error":
                await self._log.add_error(error_text(ev))
                return
            case "cancelled":
                return
            case "empty":
                await self._log.append(
                    Static(
                        "🤖 [dim]（已完成，無文字回覆）[/dim]",
                        markup=True,
                        classes="ai-header",
                    )
                )
            case "tool_limit":
                await self._log.add_notice(ev.reply)
        await self._log.append(
            Static(format_stats(ev.stats), classes="stats", markup=False)
        )
