"""Command palette provider：從 slash 指令表（widgets/slash.py）產生所有命令。

面板與 prompt 送出走同一個 handler，不再各自維護一份邏輯。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING

from textual.command import Hit, Hits, Provider

from .slash import SlashCommand, all_commands, split_command

if TYPE_CHECKING:
    from app import ChatApp


def _fill_prompt(app: ChatApp, text: str) -> None:
    """填入文字並 focus prompt，用於需要補充參數的指令。"""
    from widgets.input import HistoryInput

    prompt = app.query_one("#prompt", HistoryInput)
    prompt.value = text
    prompt.cursor_position = len(text)
    prompt.focus()


def _runner(app: ChatApp, cmd: SlashCommand, arg: str) -> Callable[[], Awaitable[None]]:
    return partial(getattr(app, cmd.method), arg)


def _state_suffix(app: ChatApp, name: str) -> str:
    """部分開關型指令在面板上顯示目前狀態。"""
    if name == "/think":
        return f"（{'開啟' if app._thinking_active() else '關閉'}）"
    if name == "/debug":
        return f"（{'開啟' if app.ui_state.debug_tools else '關閉'}）"
    return ""


class ChatCommands(Provider):
    def _all_hits(self, app: ChatApp) -> list[Hit]:
        current_model = app.agent.profile.name
        hits: list[Hit] = []
        for cmd in all_commands():
            action = (
                partial(_fill_prompt, app, f"{cmd.name} ")
                if cmd.needs_arg
                else _runner(app, cmd, "")
            )
            label = f"{cmd.name}  {cmd.help}{_state_suffix(app, cmd.name)}"
            hits.append(Hit(0.9, label, action, help=cmd.help))
            for text, desc in cmd.presets:
                _, arg = split_command(text)
                is_current = cmd.name == "/model" and arg == current_model
                hits.append(
                    Hit(
                        1.0 if is_current else 0.8,
                        f"{text}{'  ✓' if is_current else ''}",
                        _runner(app, cmd, arg),
                        help="目前使用中" if is_current else desc,
                    )
                )
        return hits

    async def discover(self) -> Hits:
        for hit in self._all_hits(self.app):  # type: ignore[arg-type]
            yield hit

    async def search(self, query: str) -> Hits:
        app: ChatApp = self.app  # type: ignore[assignment]
        matcher = self.matcher(query)
        for hit in self._all_hits(app):
            label = (
                hit.match_display
                if isinstance(hit.match_display, str)
                else str(hit.match_display)
            )
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, matcher.highlight(label), hit.command, help=hit.help)
