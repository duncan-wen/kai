"""ResumeScreen — 歸檔 session 選擇器 ModalScreen。"""
from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

from core.session_archive import SessionArchive


class ResumeScreen(ModalScreen[Path | None]):
    """顯示 archive 清單；選取後 dismiss(archive 路徑)，Escape dismiss(None)。

    讀取、改名、刪除都交給 SessionArchive；這裡只負責顯示。
    """

    BINDINGS = [
        Binding("escape", "dismiss_or_cancel", show=False),
        Binding("q", "dismiss_or_cancel", show=False),
        Binding("r", "start_rename", "重命名"),
        Binding("d", "delete", "刪除"),
    ]

    DEFAULT_CSS = """
    ResumeScreen {
        align: center middle;
    }
    #resume-dialog {
        width: 82%;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #resume-title {
        text-style: bold;
        color: $accent;
        content-align: center middle;
        height: 1;
        margin-bottom: 1;
    }
    #resume-list {
        height: 1fr;
    }
    #rename-input {
        display: none;
        margin-top: 1;
    }
    #resume-hint {
        color: $text-muted;
        content-align: center middle;
        height: 1;
        margin-top: 1;
    }
    """

    def __init__(self, archive: SessionArchive) -> None:
        super().__init__()
        self._sa = archive
        self._entries = archive.list_archives()
        self._rename_idx: int | None = None

    # ── build ─────────────────────────────────────────────────────────────────

    def _make_option(self, i: int) -> Option:
        e = self._entries[i]
        name = e.name
        is_current = e.path == self._sa.resumed_from
        return Option(
            Text.assemble(
                ("▶ " if is_current else "  ", "bold green"),
                (f"[{i + 1:2d}]  ", "dim"),
                (name + "  ", "bold yellow") if name else ("", ""),
                (e.timestamp, "bold cyan"),
                ("  ", ""),
                (f"{e.message_count:3d} 則  ", "green"),
                (e.preview + "…", ""),
            ),
            id=str(i),
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-dialog"):
            yield Label("📂 歸檔 Session", id="resume-title")
            yield OptionList(*[self._make_option(i) for i in range(len(self._entries))], id="resume-list")
            yield Input(placeholder="輸入名稱… Enter 確認  Esc 取消", id="rename-input")
            yield Label("[dim]↑↓ 選擇  Enter / 點擊 載入  r 重命名  d 刪除  Esc 關閉[/dim]", id="resume-hint")

    # ── rename flow ───────────────────────────────────────────────────────────

    def action_start_rename(self) -> None:
        ol = self.query_one("#resume-list", OptionList)
        idx = ol.highlighted
        if idx is None:
            return
        self._rename_idx = idx
        inp = self.query_one("#rename-input", Input)
        inp.value = self._entries[idx].name
        inp.display = True
        inp.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if self._rename_idx is None:
            return
        idx = self._rename_idx
        try:
            self._sa.rename_archive(self._entries[idx].path, event.value.strip())
        except OSError as e:
            self.notify(f"命名失敗：{e}", severity="error")
        self._entries = self._sa.list_archives()
        self._finish_rename(idx)

    def action_dismiss_or_cancel(self) -> None:
        inp = self.query_one("#rename-input", Input)
        if inp.display:
            self._finish_rename(self._rename_idx)
        else:
            self.dismiss(None)

    def _finish_rename(self, restore_idx: int | None) -> None:
        inp = self.query_one("#rename-input", Input)
        inp.display = False
        self._rename_idx = None
        # 更新該列的顯示
        if restore_idx is not None:
            ol = self.query_one("#resume-list", OptionList)
            ol.replace_option_prompt(str(restore_idx), self._make_option(restore_idx).prompt)
        self.query_one("#resume-list", OptionList).focus()

    # ── delete ───────────────────────────────────────────────────────────────

    def action_delete(self) -> None:
        if self.query_one("#rename-input", Input).display:
            return  # 正在改名，忽略
        ol = self.query_one("#resume-list", OptionList)
        idx = ol.highlighted
        if idx is None:
            return
        try:
            self._sa.delete_archive(self._entries[idx].path)
        except OSError as e:
            self.notify(f"刪除失敗：{e}", severity="error")
            return
        # 重新讀取並重建（序號需要重排）
        self._entries = self._sa.list_archives()
        if not self._entries:
            self.dismiss(None)
            return
        self._rebuild_list(min(idx, len(self._entries) - 1))

    def _rebuild_list(self, highlight: int) -> None:
        """清空並重建 OptionList，保持序號連續。"""
        ol = self.query_one("#resume-list", OptionList)
        ol.clear_options()
        for i in range(len(self._entries)):
            ol.add_option(self._make_option(i))
        ol.highlighted = highlight

    # ── select ────────────────────────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self.query_one("#rename-input", Input).display:
            return  # 正在改名，忽略點擊
        self.dismiss(self._entries[int(event.option_id)].path)
