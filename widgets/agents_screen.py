"""AgentsScreen — agent 清單選擇器 ModalScreen（DataTable）。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

_CHECKED = "[green]✓[/green]"
_UNCHECKED = "[dim]○[/dim]"


class ConfirmDeleteAgentScreen(ModalScreen[bool]):
    """刪除 agent 確認對話框（支援單個或多個）。"""

    BINDINGS = [
        Binding("y", "confirm", show=False),
        Binding("n,escape", "cancel", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmDeleteAgentScreen {
        align: center middle;
    }
    ConfirmDeleteAgentScreen Vertical {
        width: 60;
        height: auto;
        padding: 1 2;
        border: round $error;
        background: $surface;
    }
    ConfirmDeleteAgentScreen Label {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }
    ConfirmDeleteAgentScreen Horizontal {
        width: 100%;
        height: auto;
        align: center middle;
    }
    ConfirmDeleteAgentScreen Button {
        margin: 0 1;
    }
    """

    def __init__(self, agents: list[tuple[str, str, str]]) -> None:
        """agents: list of (agent_name, scope_path, dir_name)"""
        super().__init__()
        self._agents = agents

    def compose(self) -> ComposeResult:
        n = len(self._agents)
        title = f"確認刪除 {n} 個 agent？" if n > 1 else "確認刪除 agent？"
        with Vertical():
            yield Label(title)
            for agent_name, scope_path, dir_name in self._agents:
                yield Label(
                    f"[bold]{agent_name}[/bold]  [dim]({scope_path}/{dir_name})[/dim]",
                    markup=True,
                )
            with Horizontal():
                yield Button("刪除", id="confirm", variant="error")
                yield Button("取消", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class DeleteAgentsScreen(ModalScreen[list[str]]):
    """多選刪除 agent 的 DataTable checkbox 介面。dismiss 回傳選取的 scope:dir 清單。"""

    BINDINGS = [
        Binding("space", "toggle_row", "選取/取消", show=True),
        Binding("a", "toggle_all", "全選/取消", show=True),
        Binding("d,enter", "confirm_delete", "確認刪除", show=True),
        Binding("escape,q", "cancel", show=False),
    ]

    DEFAULT_CSS = """
    DeleteAgentsScreen {
        align: center middle;
    }
    #delete-agents-dialog {
        width: 90%;
        max-height: 80%;
        background: $surface;
        border: thick $error;
        padding: 1 2;
    }
    #delete-agents-title {
        text-style: bold;
        color: $error;
        content-align: center middle;
        height: 1;
        margin-bottom: 1;
    }
    #delete-agents-table {
        height: 1fr;
    }
    #delete-agents-hint {
        color: $text-muted;
        content-align: center middle;
        height: 1;
        margin-top: 1;
    }
    """

    def __init__(self, agents: list[dict]) -> None:
        super().__init__()
        self._agents = agents
        self._selected: set[str] = set()
        self._keys: list[str] = []
        self._col_check: object | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="delete-agents-dialog"):
            yield Label("🗑 刪除 Agent（多選）", id="delete-agents-title")
            yield DataTable(cursor_type="row", id="delete-agents-table")
            yield Label(
                "[dim]Space 選取  A 全選  D/Enter 確認刪除  Esc 取消[/dim]",
                id="delete-agents-hint",
            )

    def on_mount(self) -> None:
        dt = self.query_one("#delete-agents-table", DataTable)
        (self._col_check, *_) = dt.add_columns(
            "", "來源", "名稱", "暱稱", "模型", "說明"
        )
        for a in self._agents:
            key = f"{a.get('scope', 'local')}:{a['dir']}"
            self._keys.append(key)
            scope_label = "🏠 本地" if a.get("scope") == "local" else "🌐 全域"
            dt.add_row(
                _UNCHECKED,
                scope_label,
                a["name"],
                a.get("nickname") or "—",
                a["model"],
                a.get("description") or "—",
                key=key,
            )
        dt.focus()

    def _toggle_key(self, key: str) -> None:
        dt = self.query_one("#delete-agents-table", DataTable)
        if key in self._selected:
            self._selected.discard(key)
        else:
            self._selected.add(key)
        dt.update_cell(
            key,
            self._col_check,
            _CHECKED if key in self._selected else _UNCHECKED,
            update_width=False,
        )
        self._update_title()

    def _update_title(self) -> None:
        n = len(self._selected)
        title = f"🗑 刪除 Agent（已選 {n} 個）" if n else "🗑 刪除 Agent（多選）"
        self.query_one("#delete-agents-title", Label).update(title)

    def action_toggle_row(self) -> None:
        dt = self.query_one("#delete-agents-table", DataTable)
        idx = dt.cursor_row
        if 0 <= idx < len(self._keys):
            self._toggle_key(self._keys[idx])

    def action_toggle_all(self) -> None:
        if len(self._selected) == len(self._keys):
            for key in list(self._selected):
                self._toggle_key(key)
        else:
            for key in self._keys:
                if key not in self._selected:
                    self._toggle_key(key)

    def action_confirm_delete(self) -> None:
        self.dismiss(list(self._selected))

    def action_cancel(self) -> None:
        self.dismiss([])

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._toggle_key(str(event.row_key.value))


class AgentsScreen(ModalScreen[str | None]):
    """顯示 agents 清單；選取後 dismiss("scope:dir_name")，Escape dismiss(None)。"""

    BINDINGS = [Binding("escape,q", "dismiss", show=False)]

    DEFAULT_CSS = """
    AgentsScreen {
        align: center middle;
    }
    #agents-dialog {
        width: 90%;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #agents-title {
        text-style: bold;
        color: $accent;
        content-align: center middle;
        height: 1;
        margin-bottom: 1;
    }
    #agents-table {
        height: 1fr;
    }
    #agents-hint {
        color: $text-muted;
        content-align: center middle;
        height: 1;
        margin-top: 1;
    }
    """

    def __init__(self, agents: list[dict]) -> None:
        super().__init__()
        self._agents = agents

    def compose(self) -> ComposeResult:
        with Vertical(id="agents-dialog"):
            yield Label("🤖 Agent 清單", id="agents-title")
            yield DataTable(cursor_type="row", id="agents-table")
            yield Label(
                "[dim]↑↓ 選擇  Enter / 點擊 載入  Esc 關閉[/dim]", id="agents-hint"
            )

    def on_mount(self) -> None:
        dt = self.query_one("#agents-table", DataTable)
        dt.add_columns("來源", "名稱", "暱稱", "模型", "說明")
        for a in self._agents:
            scope_label = "🏠 本地" if a.get("scope") == "local" else "🌐 全域"
            dt.add_row(
                scope_label,
                a["name"],
                a.get("nickname") or "—",
                a["model"],
                a.get("description") or "—",
                key=f"{a.get('scope', 'local')}:{a['dir']}",
            )
        dt.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(str(event.row_key.value))
