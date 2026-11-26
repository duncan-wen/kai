"""Input widgets: HistoryInput and ChatAutoComplete."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from textual import events
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import Input
from textual_autocomplete import AutoComplete, DropdownItem, TargetState

from core.config import USERNAME

from .slash import completion_items
from .utils import _IMAGE_EXTS


SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class PromptSpinner:
    """在 prompt 的 border_title 播放 spinner 動畫；`label` 可隨時改（例如「生成中」）。

    `visible` 回傳 False 時這格不畫，讓背景 tab 的工作不會蓋掉目前 tab 的標題。
    debug 標記由 prompt 自己的 `debug_mark` 決定。用法：`start()` 後在 finally 裡 `stop()`；
    stop 時若沒有其他 spinner 在跑，prompt 會恢復待機標題。
    """

    def __init__(
        self,
        prompt: HistoryInput,
        label: str,
        *,
        visible: Callable[[], bool] = lambda: True,
    ) -> None:
        self.prompt = prompt
        self.label = label
        self._visible = visible
        self._idx = 0
        self._timer = None

    def start(self) -> PromptSpinner:
        self.prompt._spinners.add(self)
        self._render()
        self._timer = self.prompt.set_interval(0.08, self._tick)
        return self

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.prompt._spinners.discard(self)
        if self._is_visible() and not self.prompt._spinner_showing():
            self.prompt.show_idle_title()

    def _is_visible(self) -> bool:
        try:
            return self._visible()
        except Exception:
            return False

    def _tick(self) -> None:
        self._idx = (self._idx + 1) % len(SPINNER)
        self._render()

    def _render(self) -> None:
        if self._is_visible():
            self.prompt.border_title = (
                f"{SPINNER[self._idx]} {self.label}{self.prompt.debug_suffix}"
            )


class HistoryInput(Input):
    """Input with Up/Down history navigation and Ctrl+Z undo.

    Uses _on_key (runs before the binding system) to intercept up/down before
    ScrollView's scroll_up/scroll_down bindings consume them.
    """

    BINDINGS = [
        Binding("tab", "apply_prediction", "Apply prediction", show=False),
    ]

    def action_apply_prediction(self) -> None:
        if self._real_placeholder and not self.value:
            prediction = self.placeholder
            self.placeholder = self._real_placeholder
            self._real_placeholder = ""
            self._suggestion = ""
            self._pending_prediction = ""
            self.value = prediction
            self.cursor_position = len(self.value)
        else:
            self.screen.focus_next()

    # 超過此字元數自動壓縮成 marker，避免 Input 出現 scroll-x
    _COMPRESS_THRESHOLD: int = 200

    debug_mark: reactive[bool] = reactive(False, init=False)
    """工具除錯模式；ChatApp 用 data_bind 綁到 ChatApp.debug_tools。"""
    locked: reactive[bool] = reactive(False, init=False)
    """目前 tab 有工作在跑（串流、bash…）；ChatApp 綁到 ChatApp.prompt_locked。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._spinners: set[PromptSpinner] = set()
        self._undo_stack: list[tuple[str, int]] = []
        self._multiline_paste: str | None = None
        self._multiline_marker: str | None = None
        self._real_placeholder: str = ""  # saved when ghost text swaps placeholder
        self._pending_prediction: str = ""  # last prediction, for re-show on empty

    # ── 標題 / placeholder ─────────────────────────────────────────────────────

    @property
    def debug_suffix(self) -> str:
        return "  [dim]🔧 debug[/dim]" if self.debug_mark else ""

    def show_idle_title(self) -> None:
        """待機標題：使用者名稱 + debug 標記。"""
        self.border_title = f"[bold]{USERNAME}[/bold] [dim]❯[/dim]{self.debug_suffix}"

    def on_mount(self) -> None:
        self.show_idle_title()

    def _spinner_showing(self) -> bool:
        return any(s._is_visible() for s in self._spinners)

    def watch_debug_mark(self) -> None:
        if not self._spinner_showing():
            self.show_idle_title()

    def watch_locked(self, locked: bool) -> None:
        self.disabled = locked
        self.set_class(locked, "streaming")
        if not locked:
            if not self._spinner_showing():
                self.show_idle_title()
            self.focus()

    def set_base_placeholder(self, text: str) -> None:
        """設定一般 placeholder；正在顯示 ghost text 預測時只更新被暫存的那份。"""
        if self._real_placeholder:
            self._real_placeholder = text
        else:
            self.placeholder = text

    # ── 長文字壓縮 ─────────────────────────────────────────────────────────────

    def _compress(self, text: str) -> None:
        """把長文字壓縮成 marker 格式，避免 Input 橫向 scroll。
        若 text 中含舊 marker，先展開成真實內容再重壓縮，確保 submit 時內容正確。"""
        if (
            self._multiline_marker
            and self._multiline_paste
            and self._multiline_marker in text
        ):
            text = text.replace(self._multiline_marker, self._multiline_paste, 1)
        self._multiline_paste = text
        # preview 取第一行避免 \n 混入 marker，否則 self.value 會是多行字串
        first_line = text.split("\n", 1)[0]
        preview = first_line[:40] + (
            "…" if len(first_line) > 40 or "\n" in text else ""
        )
        marker = f"[{len(text)} 字元] {preview}"
        self._multiline_marker = marker
        self.value = marker
        self.cursor_position = len(marker)

    # ── paste ──────────────────────────────────────────────────────────────────

    def on_paste(self, event: events.Paste) -> None:
        # 連續貼上：若整個 input 就是舊 marker，清空重來（取代語意，非累加）
        if self._multiline_paste is not None and self.value == self._multiline_marker:
            self._multiline_paste = None
            self._multiline_marker = None
            self.value = ""
            self.cursor_position = 0

        text = event.text
        if "\n" not in text and "\r" not in text:
            # 單行貼上：若貼後總長超過閾值，壓縮（移除 _multiline_paste is None 限制）
            combined = (
                self.value[: self.cursor_position]
                + text
                + self.value[self.cursor_position :]
            )
            if len(combined) > self._COMPRESS_THRESHOLD:
                event.prevent_default()
                self._compress(combined)
            return
        event.prevent_default()
        raw = text.replace("\r\n", "\n").replace("\r", "\n").strip()

        last = getattr(self.app, "_last_file_copy", None)
        if last and last.get("text", "").strip() == raw:
            p = last["path"]
            fname = p.name if p else "unknown"
            s, e = last["start_line"], last["end_line"]
            loc = f"{fname}:{s}" if s == e else f"{fname}:{s}-{e}"
            raw = f"```{p.suffix.lstrip('.') if p else ''} {loc}\n{raw}\n```"

        self._multiline_paste = raw
        lines = raw.splitlines()
        preview = lines[0][:40] + ("…" if len(lines[0]) > 40 else "")
        marker = f"[{len(lines)} 行] {preview}"
        self._multiline_marker = marker
        before = self.value[: self.cursor_position]
        after = self.value[self.cursor_position :]
        self.value = before + marker + after
        self.cursor_position = len(before) + len(marker)

    def reset_prediction(self, new_placeholder: str | None = None) -> None:
        """清除 ghost text 預測狀態，避免 _real_placeholder 過期汙染。
        在切換 tab 或新回覆開始時呼叫，可同時設定新的 placeholder。
        """
        self._real_placeholder = ""
        self._pending_prediction = ""
        self._suggestion = ""
        if new_placeholder is not None:
            self.placeholder = new_placeholder

    def show_prediction(self, text: str) -> None:
        """顯示 AI 預測 ghost text。input 空時用 placeholder swap，有內容時用 _suggestion。"""
        self._pending_prediction = text
        self._suggestion = text
        if not self.value:
            if not self._real_placeholder:
                self._real_placeholder = self.placeholder
            self.placeholder = text

    # marker 格式：[<數字> 字元] 或 [<數字> 行]，開頭必須是數字以避免誤判一般文字
    _MARKER_RE = re.compile(r"^\[\d+ (?:字元|行)\]")

    def _is_marker(self, value: str) -> bool:
        return bool(self._MARKER_RE.match(value))

    def _watch_selection(self, selection: object) -> None:
        """修正 Textual bug：_watch_selection 在 scroll_to_region 前設 cursor_position，
        導致 terminal cursor（綠）與 soft cursor（灰）位移。scroll 後重設一次。"""
        super()._watch_selection(selection)  # type: ignore[arg-type]
        if self.has_focus:
            self.app.cursor_position = self.cursor_screen_offset

    def _watch_value(self, value: str) -> None:
        # 長文字先壓縮：確保 super()._watch_value 永遠只看到短值。
        # 若先呼叫 super() 再壓縮，Textual 會以 virtual_size=Size(N,1) 執行
        # scroll_to_region，可能在 terminal 留下超出 TUI canvas 的殘影。
        if (
            len(value) > self._COMPRESS_THRESHOLD or "\n" in value
        ) and not self._is_marker(value):
            self._compress(value)
            return  # _compress → self.value=marker → 重新進入此函式
        # marker 已不在 value（例如 Ctrl+U 清空後另輸文字），清除壓縮狀態避免舊內容被送出
        if self._multiline_marker and self._multiline_marker not in value:
            self._multiline_paste = None
            self._multiline_marker = None
        super()._watch_value(value)  # 父類會清掉 _suggestion
        if value and self._real_placeholder:
            self.placeholder = self._real_placeholder
            self._real_placeholder = ""
        elif not value and not self._real_placeholder and self._pending_prediction:
            self.show_prediction(self._pending_prediction)

    def _on_key(self, event: events.Key) -> None:
        if event.key == "right" and self._real_placeholder and not self.value:
            self.action_apply_prediction()
            event.prevent_default()
            event.stop()
            return
        if event.key == "up":
            event.prevent_default()
            event.stop()
            self.app.action_history_prev()  # type: ignore[attr-defined]
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            self.app.action_history_next()  # type: ignore[attr-defined]
        elif event.key == "ctrl+z":
            event.prevent_default()
            event.stop()
            if self._undo_stack:
                val, pos = self._undo_stack.pop()
                self.value = val
                self.cursor_position = min(pos, len(val))
        else:
            _SAVE_BEFORE = frozenset(
                {"backspace", "delete", "ctrl+backspace", "ctrl+delete"}
            )
            if event.key in _SAVE_BEFORE or len(event.key) == 1:
                self._undo_stack.append((self.value, self.cursor_position))
                if len(self._undo_stack) > 200:
                    del self._undo_stack[:-200]


class ChatAutoComplete(AutoComplete):
    """動態補全：斜線指令、@path 檔案、/image 路徑、! bash 路徑。"""

    def _at_prefix(self, text: str) -> str | None:
        """回傳游標前最後一個 @ 之後的路徑片段；@ 後有空白表示已完成，回傳 None。"""
        idx = text.rfind("@")
        if idx == -1:
            return None
        if idx > 0 and text[idx - 1] not in " \t":
            return None  # @ 夾在字中間（如 email）
        fragment = text[idx + 1 :]
        if " " in fragment:
            return None  # @ mention 已完成
        return fragment

    def get_search_string(self, state: TargetState) -> str:
        text = state.text
        if text.startswith("/image "):
            return text[7:]
        if text.startswith("/cd "):
            return text[4:]
        if text.startswith("/"):
            return text
        at = self._at_prefix(text)
        if at is not None:
            return at
        if text.startswith("!"):
            cmd_part = text[1:]
            tokens = cmd_part.split()
            if not tokens:
                return ""
            cmd0 = tokens[0]
            last = tokens[-1] if not text.endswith(" ") else ""
            if not last or last == cmd0:
                return ""
            if not last.startswith("-"):
                return last
        return ""

    def should_show_dropdown(self, search_string: str) -> bool:
        text = self._target_state.text
        if text.startswith(("/image ", "/cd ")):
            return self.option_list.option_count > 0
        if self._at_prefix(text) is not None:
            return self.option_list.option_count > 0
        return super().should_show_dropdown(search_string)

    def get_candidates(self, state: TargetState) -> list[DropdownItem]:
        text = state.text
        if text.startswith("/image "):
            return self._image_items(text[7:])
        if text.startswith("/cd "):
            return self._dir_items(text[4:])
        if text.startswith("/"):
            return [
                DropdownItem(main=cmd)
                for cmd, _desc in completion_items()
                if text == "/" or cmd.startswith(text) or text[1:] in cmd
            ]
        at = self._at_prefix(text)
        if at is not None:
            return self._file_items(at)
        if text.startswith("!"):
            cmd_part = text[1:]
            tokens = cmd_part.split()
            if not tokens:
                return []
            cmd0 = tokens[0]
            last = tokens[-1] if not text.endswith(" ") else ""
            if not last or last == cmd0:
                return []
            if not last.startswith("-"):
                if cmd0 == "cd":
                    return self._dir_items(last)
                return self._file_items(last)
        return []

    def _dir_items(self, prefix: str) -> list[DropdownItem]:
        try:
            base = Path(prefix) if prefix else Path(".")
            search_dir = base if base.is_dir() else base.parent
            name_filter = "" if base.is_dir() else base.name.lower()
            entries = sorted(search_dir.iterdir(), key=lambda p: p.name.lower())
            return [
                DropdownItem(main=str(p) + "/", prefix="📁 ")
                for p in entries
                if p.is_dir() and p.name.lower().startswith(name_filter)
            ][:20]
        except Exception:
            return []

    def _file_items(self, prefix: str) -> list[DropdownItem]:
        try:
            base = Path(prefix) if prefix else Path(".")
            search_dir = base if base.is_dir() else base.parent
            name_filter = "" if base.is_dir() else base.name.lower()
            entries = sorted(
                search_dir.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
            return [
                DropdownItem(
                    main=str(p) + ("/" if p.is_dir() else ""),
                    prefix="📁 " if p.is_dir() else "📄 ",
                )
                for p in entries
                if p.name.lower().startswith(name_filter)
            ][:20]
        except Exception:
            return []

    def _image_items(self, prefix: str) -> list[DropdownItem]:
        try:
            base = Path(prefix) if prefix else Path(".")
            search_dir = base if base.is_dir() else base.parent
            name_filter = "" if base.is_dir() else base.name.lower()
            entries = sorted(
                search_dir.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
            return [
                DropdownItem(
                    main=str(p) + ("/" if p.is_dir() else ""),
                    prefix="📁 " if p.is_dir() else "🖼 ",
                )
                for p in entries
                if p.name.lower().startswith(name_filter)
                and (p.is_dir() or p.suffix.lower() in _IMAGE_EXTS)
            ][:20]
        except Exception:
            return []

    def apply_completion(self, value: str, state: TargetState) -> None:
        text = state.text
        target = self.target
        if text.startswith("/image "):
            new_val = "/image " + value
            target.value = new_val
            target.cursor_position = len(new_val)
        elif text.startswith("/cd "):
            new_val = "/cd " + value
            target.value = new_val
            target.cursor_position = len(new_val)
        elif text.startswith("/"):
            target.value = value
            target.cursor_position = len(value)
        elif self._at_prefix(text) is not None:
            at_idx = text.rfind("@")
            new_val = text[: at_idx + 1] + value
            target.value = new_val
            target.cursor_position = len(new_val)
        elif text.startswith("!"):
            last_space = text.rfind(" ")
            base = text[: last_space + 1] if last_space >= 0 else "!"
            new_val = base + value + ("" if value.endswith("/") else " ")
            target.value = new_val
            target.cursor_position = len(new_val)
        else:
            super().apply_completion(value, state)
        new_state = self._get_target_state()
        self._rebuild_options(new_state, self.get_search_string(new_state))

    def _align_to_target(self) -> None:
        from textual.geometry import Offset

        x, y = self.target.cursor_screen_offset
        _w, h = self.option_list.outer_size
        if h == 0:
            h = min(max(self.option_list.option_count, 1), 12)
        self.absolute_offset = Offset(max(0, x - 1), max(0, y - h))

    def post_completion(self) -> None:
        text = self._target_state.text
        # /cd 與 !cd：選取中間目錄後直接關閉，讓使用者可按 Enter 確認，不強制選到最末層
        _is_cd = text.startswith("/cd ") or (
            text.startswith("!") and text.lstrip("!").lstrip().startswith("cd ")
        )
        if _is_cd:
            self.action_hide()
            return
        if self.target.value.endswith("/"):
            return
        self.action_hide()
