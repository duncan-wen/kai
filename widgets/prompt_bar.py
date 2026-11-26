"""PromptBar：輸入列（📎 附件按鈕 + HistoryInput + 自動完成）。

輸入歷史、多行貼上 marker 展開、附件插入、滑鼠序列殘渣過濾都在這裡處理；
對外只送 `PromptBar.Submitted(text)`，由 App 決定要分派到 bash / slash / 對話。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from loguru import logger
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input, Static

from .input import MOUSE_SEQ, ChatAutoComplete, HistoryInput
from .utils import _IMAGE_EXTS

PROMPT_HISTORY_FILE = Path(".kai/.prompt_history")

_TEXT_EXTS = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ts",
    ".js",
    ".html",
    ".css",
}


def load_prompt_history(path: Path) -> list[str]:
    """讀取 prompt_toolkit FileHistory 格式，回傳最舊→最新排列的清單。"""
    if not path.exists():
        return []
    entries: list[str] = []
    current: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("+"):
            current.append(line[1:])
        elif not line.strip() and current:
            entries.append("\n".join(current))
            current = []
    if current:
        entries.append("\n".join(current))
    return entries


def append_prompt_history(path: Path, text: str) -> None:
    """將一筆輸入追加到 prompt_toolkit FileHistory 格式的檔案。"""
    lines = "\n".join(f"+{ln}" for ln in text.splitlines())
    entry = f"# {datetime.now()}\n{lines}\n\n"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        logger.warning(f"寫入 prompt history 失敗：{e}")


class PromptBar(Horizontal):
    """輸入列容器；`debug_mark` / `locked` 由 App 綁定後再轉綁給 HistoryInput。"""

    class Submitted(Message):
        """使用者送出一筆非空輸入（多行貼上 marker 已展開、已去頭尾空白）。"""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    debug_mark: reactive[bool] = reactive(False, init=False)
    locked: reactive[bool] = reactive(False, init=False)

    def __init__(
        self,
        *,
        history_file: Path = PROMPT_HISTORY_FILE,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._history_file = history_file
        self.history: list[str] = load_prompt_history(history_file)
        self._history_idx = -1
        self._history_draft = ""

    def compose(self) -> ComposeResult:
        yield Static("📎", id="attach-btn", classes="attach-btn")
        self._prompt = HistoryInput(id="prompt").data_bind(
            debug_mark=PromptBar.debug_mark, locked=PromptBar.locked
        )
        yield self._prompt

    def on_mount(self) -> None:
        # 下拉選單掛在 screen 上：放在這個 3 行高的 Horizontal 裡會被裁成 3 行、
        # 也浮不到 prompt 上方。仍由 PromptBar 建立並持有。
        self._autocomplete = ChatAutoComplete(target=self._prompt, candidates=None)
        self.screen.mount(self._autocomplete)

    @property
    def prompt(self) -> HistoryInput:
        return self._prompt

    @property
    def autocomplete(self) -> ChatAutoComplete:
        return self._autocomplete

    # ── 送出 ──────────────────────────────────────────────────────────────────

    @on(Input.Submitted, "#prompt")
    def _on_prompt_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        prompt = self.prompt
        text = prompt.take_submission(event.value)
        if not text:
            return
        prompt.clear()
        if not self.history or self.history[-1] != text:
            self.history.append(text)
            append_prompt_history(self._history_file, text)
        self._history_idx = -1
        self._history_draft = ""
        self.post_message(self.Submitted(text))

    @on(Input.Changed, "#prompt")
    def _on_prompt_changed(self, event: Input.Changed) -> None:
        if MOUSE_SEQ.search(event.value):
            event.input.value = ""
            event.input.cursor_position = 0

    # ── 輸入歷史 ──────────────────────────────────────────────────────────────

    @on(HistoryInput.Navigate)
    def _on_navigate(self, event: HistoryInput.Navigate) -> None:
        event.stop()
        if event.step < 0:
            self.history_prev()
        else:
            self.history_next()

    def history_prev(self) -> None:
        """往較舊的輸入走一筆；第一次離開草稿時先暫存目前內容。"""
        prompt = self.prompt
        if prompt.disabled or self.autocomplete.display or not self.history:
            return
        if self._history_idx == -1:
            self._history_draft = prompt.value
        self._history_idx = min(self._history_idx + 1, len(self.history) - 1)
        prompt.recall(self.history[-(self._history_idx + 1)])

    def history_next(self) -> None:
        """往較新的輸入走一筆；走回最底時還原草稿。"""
        prompt = self.prompt
        if prompt.disabled or self.autocomplete.display or self._history_idx == -1:
            return
        self._history_idx -= 1
        if self._history_idx == -1:
            prompt.value = self._history_draft
            prompt.cursor_position = len(prompt.value)
        else:
            prompt.recall(self.history[-(self._history_idx + 1)])

    # ── 附件 ──────────────────────────────────────────────────────────────────

    def attach(self, path: Path) -> None:
        """圖片 → 改成 `/image <path> `；其他檔案 → 在輸入尾端加 `@<path> `。"""
        prompt = self.prompt
        if path.suffix.lower() in _IMAGE_EXTS:
            prompt.value = f"/image {path} "
        else:
            cur = prompt.value
            insert = f"@{path} "
            if cur and not cur.endswith(" "):
                insert = " " + insert
            prompt.value = cur + insert
        prompt.cursor_position = len(prompt.value)
        prompt.focus()

    @on(events.Click, "#attach-btn")
    def _on_attach_clicked(self, event: events.Click) -> None:
        event.stop()
        from textual_fspicker import FileOpen, Filters

        filters = Filters(
            ("圖片", lambda p: p.suffix.lower() in _IMAGE_EXTS),
            ("文字", lambda p: p.suffix.lower() in _TEXT_EXTS),
            ("所有檔案", lambda p: True),
        )

        def _on_chosen(path: Path | None) -> None:
            if path:
                self.attach(Path(path))

        self.app.push_screen(
            FileOpen(os.getcwd(), title="選擇附件", filters=filters),
            callback=_on_chosen,
        )
