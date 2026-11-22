"""Sidebar and ResizeHandle widgets."""
from __future__ import annotations

import os
import subprocess

from textual import events, on, work
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static
from textual_fspicker import SelectDirectory

from core.config import MODEL


class ResizeHandle(Widget):
    """拖拉分隔條，調整相鄰 widget 的寬度。

    right_anchor=True 時目標在右側（sidebar），往左拖加寬。
    """

    def __init__(
        self, target_id: str, min_w: int = 10, max_w: int = 80,
        right_anchor: bool = False, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self._target_id = target_id
        self._min_w = min_w
        self._max_w = max_w
        self._right_anchor = right_anchor
        self._dragging = False

    def render(self) -> str:
        return ""

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._dragging = True
        self.capture_mouse()
        self.add_class("dragging")
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.release_mouse()
            self.remove_class("dragging")

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        target = self.app.query_one(f"#{self._target_id}")
        if self._right_anchor:
            new_w = max(self._min_w, min(self._max_w, target.region.right - event.screen_x))
        else:
            new_w = max(self._min_w, min(self._max_w, event.screen_x - target.region.x))
        target.styles.width = new_w


class Sidebar(Widget):
    """工作目錄、模型、Git 狀態側邊欄。"""

    def compose(self) -> ComposeResult:
        yield Static("📂 工作目錄", id="sb-cwd-btn", classes="sidebar-title-btn", markup=False)
        yield Static(os.getcwd(), classes="sidebar-content", id="sb-cwd", markup=False)
        yield Static("🤖 模型", classes="sidebar-title", markup=True)
        yield Static(MODEL, classes="sidebar-content", id="sb-model", markup=False)
        yield Static("📋 Git 變更", classes="sidebar-title", markup=True)
        yield Static("", classes="sidebar-content", id="sb-git", markup=True)
        yield Static("🔧 MCP 工具", classes="sidebar-title", markup=True)
        yield Static(
            "[dim]初始化中...[/dim]",
            classes="sidebar-content",
            id="sb-mcp",
            markup=True,
        )
        yield Static("🧩 Skills", classes="sidebar-title", markup=True)
        yield Static(
            "[dim]掃描中...[/dim]",
            classes="sidebar-content",
            id="sb-skills",
            markup=True,
        )

    def on_mount(self) -> None:
        self._refresh_git_worker()

    @on(events.Click, "#sb-cwd-btn")
    async def _on_cwd_clicked(self, event: events.Click) -> None:
        event.stop()
        async def _on_dir_selected(path) -> None:
            if path:
                try:
                    await self.app._do_cd(str(path))
                except Exception as e:
                    self.app.notify(f"切換失敗：{e}", severity="error")
        self.app.push_screen(
            SelectDirectory(os.getcwd(), title="切換工作目錄"),
            callback=_on_dir_selected,
        )

    @work(thread=True)
    def _refresh_git_worker(self) -> None:
        from rich.text import Text as RichText

        try:
            out = subprocess.run(
                ["git", "-c", "color.status=always", "status", "--short"],
                capture_output=True,
                text=True,
                timeout=2,
                cwd=os.getcwd(),
            ).stdout.strip()
            result = RichText.from_ansi(out) if out else RichText("無變更", style="dim")
        except Exception:
            result = RichText("非 git 目錄", style="dim")
        self.app.call_from_thread(self.query_one("#sb-git", Static).update, result)

    def refresh_git(self) -> None:
        self._refresh_git_worker()

    def set_model(self, name: str) -> None:
        self.query_one("#sb-model", Static).update(name)

    def set_mcp(self, summary: dict[str, int]) -> None:
        if summary:
            lines = [
                f"[green]{name}[/green] [dim]{n}t[/dim]" for name, n in summary.items()
            ]
            text = "\n".join(lines)
        else:
            text = "[dim]未啟用[/dim]"
        try:
            self.query_one("#sb-mcp", Static).update(text)
        except Exception:
            pass

    def set_skills(self, names: list[str]) -> None:
        if names:
            text = f"[dim]{len(names)} 個[/dim]  " + "、".join(names)
        else:
            text = "[dim]未啟用[/dim]"
        try:
            self.query_one("#sb-skills", Static).update(text)
        except Exception:
            pass
