"""Chat display widgets: ImageBubble, FileViewer, CopyButton, CapabilityBadge, CapLabel, AIMessage, ConfirmCloseScreen."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from core.config import USERNAME

from rich.text import Text as RichText
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual import events
from textual.events import Click
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Markdown, Static, Tab, TextArea

from textual_fspicker import FileSave
from .utils import _copy_to_clipboard, _latex_to_unicode, _line_height, _open_image


class ConfirmCloseScreen(ModalScreen[bool]):
    """未存檔關閉確認對話框。"""

    BINDINGS = [
        Binding("y", "confirm", "", show=False),
        Binding("n", "cancel", "", show=False),
        Binding("escape", "cancel", "", show=False),
    ]

    def __init__(self, filename: str) -> None:
        super().__init__()
        self._filename = filename

    def compose(self) -> ComposeResult:
        with Vertical(classes="confirm-dialog"):
            yield Static(
                f"[bold]{self._filename}[/bold] 有未存檔的修改",
                markup=True,
                classes="confirm-title",
            )
            yield Static(
                "關閉後修改將遺失，確定要關閉嗎？", markup=False, classes="confirm-body"
            )
            with Horizontal(classes="confirm-buttons"):
                yield Button("取消", variant="default", id="btn-cancel")
                yield Button("關閉（不存檔）", variant="error", id="btn-close")

    def on_mount(self) -> None:
        self.query_one("#btn-cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-close")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ImageBubble(Widget):
    """使用者傳送的圖片訊息：metadata card + 開啟按鈕。"""

    def __init__(self, path: str, question: str) -> None:
        super().__init__(classes="image-bubble")
        self._path = path
        self._question = question

    def compose(self) -> ComposeResult:
        from PIL import Image

        name = Path(self._path).name
        try:
            img = Image.open(self._path)
            w, h = img.size
            size_kb = Path(self._path).stat().st_size // 1024
            meta = f"{w}×{h}  {size_kb} KB"
        except Exception:
            meta = "（無法讀取）"
        yield Static(
            f"[bold]{USERNAME}[/bold]  🖼 {name}  [dim]{meta}[/dim]",
            markup=True,
            classes="image-bubble-header",
        )
        yield Static(self._question, markup=False, classes="image-question")
        yield Static("🔍 開啟圖片", classes="img-open-btn")

    async def on_click(self, event: events.Click) -> None:
        event.stop()
        await asyncio.to_thread(_open_image, self._path)


class FileViewer(TextArea):
    """檔案檢視與編輯器：Ctrl+S 存檔、Ctrl+C 多行複製（WSL）、修改時 tab 標題加 ●。"""

    BINDINGS = [Binding("ctrl+s", "save", "儲存"), Binding("f2", "save_as", "另存新檔")]

    def __init__(self, *args, file_path: Path | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._file_path = file_path
        self._modified = False
        self._mtime: float = 0.0

    def on_mount(self) -> None:
        self._modified = False
        if self._file_path:
            try:
                self._mtime = self._file_path.stat().st_mtime
            except OSError:
                pass
            self.set_interval(1.0, self._check_external_change)

    def _check_external_change(self) -> None:
        if self._modified or not self._file_path:
            return
        try:
            mtime = self._file_path.stat().st_mtime
        except OSError:
            return
        if mtime <= self._mtime:
            return
        self._mtime = mtime
        try:
            content = self._file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        self.load_text(content)
        self.app.notify(f"↺ {self._file_path.name} 已更新", timeout=2)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if not self._modified and self._file_path:
            self._modified = True
            self._set_tab_title(modified=True)

    def _set_tab_title(self, modified: bool) -> None:
        if not self._file_path or not self.parent:
            return
        tab_id = self.parent.id
        try:
            tab = self.app.query_one(f"Tab#--content-tab-{tab_id}", Tab)
            prefix = "● " if modified else ""
            tab.label = f"{prefix}{self._file_path.name} ×"
        except Exception:
            pass

    def action_save(self) -> None:
        if not self._file_path:
            self.app.notify("無法儲存：無檔案路徑", severity="warning")
            return
        try:
            self._file_path.write_text(self.text, encoding="utf-8")
            self._mtime = self._file_path.stat().st_mtime
            self._modified = False
            self._set_tab_title(modified=False)
            self.app.notify(f"已儲存 {self._file_path.name}", timeout=1.5)
        except Exception as e:
            self.app.notify(f"儲存失敗：{e}", severity="error")

    async def action_save_as(self) -> None:
        start_dir = str(self._file_path.parent) if self._file_path else os.getcwd()

        async def _on_path(path) -> None:
            if not path:
                return
            dest = Path(path)
            try:
                dest.write_text(self.text, encoding="utf-8")
                self._file_path = dest
                self._mtime = dest.stat().st_mtime
                self._modified = False
                self._set_tab_title(modified=False)
                self.app.notify(f"已另存為 {dest.name}", timeout=1.5)
            except Exception as e:
                self.app.notify(f"另存失敗：{e}", severity="error")

        self.app.push_screen(
            FileSave(start_dir, title="另存新檔"),
            callback=_on_path,
        )

    def action_copy(self) -> None:
        text = self.selected_text
        if not text:
            return
        sel = self.selection
        start_line = sel.start[0] + 1
        end_line = sel.end[0] + 1
        self.app._last_file_copy = {  # type: ignore[attr-defined]
            "text": text,
            "path": self._file_path,
            "start_line": start_line,
            "end_line": end_line,
        }
        # 主執行緒直接試 OSC52；失敗才交給 thread 用 subprocess
        if _copy_to_clipboard(text, app=self.app):
            self.app.notify("已複製", timeout=1.5)
        else:
            self._copy_worker(text)

    @work(thread=True)
    def _copy_worker(self, text: str) -> None:
        ok = _copy_to_clipboard(text)  # thread 不傳 app，走 subprocess 路徑
        msg = "已複製" if ok else "複製失敗（需要 xclip 或 clip.exe）"
        sev = "information" if ok else "warning"
        self.app.call_from_thread(self.app.notify, msg, severity=sev, timeout=1.5)


class MermaidButton(Static):
    """Mermaid 圖表開啟按鈕。"""

    def __init__(self, code: str) -> None:
        super().__init__("\U0001f5fa 開啟圖表", classes="mermaid-btn")
        self._code = code

    async def on_click(self, event: events.Click) -> None:
        event.stop()
        from widgets.utils import _mermaid_url, _open_url

        url = _mermaid_url(self._code)
        await asyncio.to_thread(_open_url, url)
        self.app.notify("已開啟 mermaid.live", timeout=2)


class CopyButton(Static):
    """程式碼區塊右上角的複製按鈕。"""

    def __init__(self, code: str) -> None:
        super().__init__("⎘", classes="copy-btn")
        self._code = code

    async def on_click(self, event: events.Click) -> None:
        event.stop()
        ok = _copy_to_clipboard(self._code, app=self.app)
        if ok:
            self.app.notify("已複製", timeout=1.5)
        else:
            self.app.notify("複製失敗（需要 xclip 或 clip.exe）", severity="warning")


class CapabilityBadge(Static):
    """可點擊功能標籤（目前只有 think）。"""

    def __init__(self, key: str, label: str) -> None:
        super().__init__(label, id=f"cap-{key}", classes="cap-badge")
        self._key = key

    def on_click(self, event: events.Click) -> None:
        event.stop()
        if self._key == "think":
            self.app.action_toggle_think()  # type: ignore[attr-defined]


class CapLabel(Static):
    """純顯示功能標籤（vision 等）。"""

    def __init__(self, key: str, label: str) -> None:
        super().__init__(label, id=f"cap-{key}", classes="cap-badge cap-label")


class AIMessage(Widget):
    """AI 回應：streaming 用 Static 純文字，完成後切換為 Markdown；右鍵切換 TextArea 選取模式。"""

    can_focus = False
    _raw: str = ""

    def compose(self) -> ComposeResult:
        yield Static("", classes="stream-view", markup=False)  # 串流期間顯示
        yield Markdown("", classes="md-view")  # 完成後顯示
        yield TextArea(
            "",
            read_only=True,
            soft_wrap=True,
            show_line_numbers=False,
            tab_behavior="focus",
            classes="ta-view",
        )

    async def update_content(self, text: str, final: bool = False) -> None:
        self._raw = text
        if not final:
            # 串流中：用 RichText 顯示，overflow='fold' 強制換行不撐爆 layout
            self.query_one(".stream-view", Static).update(
                RichText(text, overflow="fold", no_wrap=False)
            )
            return
        # 完成：做一次 Markdown render，隱藏 stream-view、顯示 md-view
        self.query_one(".stream-view", Static).display = False
        md = self.query_one(".md-view", Markdown)
        md.display = True
        await md.update(_latex_to_unicode(text))
        ta = self.query_one(".ta-view", TextArea)
        ta.text = text
        ta.styles.height = max(4, min(_line_height(text) + 1, 50))
        await self._add_copy_buttons()

    async def _add_copy_buttons(self) -> None:
        from textual.widgets._markdown import MarkdownFence
        from textual.containers import Horizontal

        _MERMAID_RE = re.compile(
            r"^(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram"
            r"|erDiagram|journey|gantt|pie|gitGraph|mindmap|timeline)\b",
            re.IGNORECASE,
        )
        md = self.query_one(".md-view", Markdown)
        for fence in md.query(MarkdownFence):
            if fence.query(CopyButton):
                continue
            if not fence.code:
                continue
            lexer = (
                getattr(fence, "_lexer", "") or getattr(fence, "language", "")
            ).lower()
            is_mermaid = lexer == "mermaid" or bool(
                _MERMAID_RE.match(fence.code.strip())
            )
            if is_mermaid:
                await fence.mount(
                    Horizontal(
                        MermaidButton(fence.code),
                        CopyButton(fence.code),
                        classes="fence-btns",
                    )
                )
            else:
                await fence.mount(CopyButton(fence.code))

    def on_click(self, event: Click) -> None:
        if event.button != 3:
            return
        if "select-mode" in self.classes:
            self.remove_class("select-mode")
            self.app.query_one("#prompt").focus()
        else:
            ta = self.query_one(".ta-view", TextArea)
            ta.language = "markdown"
            ta.text = self._raw
            ta.styles.height = max(4, min(_line_height(self._raw) + 1, 50))
            self.add_class("select-mode")
            ta.focus()
            self.app.notify(
                "選取模式：拖曳選取 → Ctrl+C 複製  ·  右鍵返回",
                severity="information",
                timeout=3,
            )
