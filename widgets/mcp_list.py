"""MCPListScreen — ModalScreen 顯示已連線 MCP servers 與工具清單。"""

from __future__ import annotations

from rich.box import SIMPLE
from rich.table import Table as RichTable
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Static


class MCPListScreen(ModalScreen):
    BINDINGS = [Binding("escape,q", "dismiss", show=False)]

    DEFAULT_CSS = """
    MCPListScreen {
        align: center middle;
    }
    #mcp-dialog {
        width: 82%;
        max-height: 85%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #mcp-title {
        text-style: bold;
        color: $accent;
        content-align: center middle;
        margin-bottom: 1;
    }
    #mcp-scroll {
        max-height: 1fr;
    }
    MCPListScreen Collapsible {
        border: none;
        padding: 0;
        margin-bottom: 1;
    }
    MCPListScreen CollapsibleTitle {
        color: $text;
        text-style: bold;
    }
    #mcp-close {
        margin-top: 1;
        width: 100%;
        align-horizontal: center;
    }
    """

    def __init__(self, tools_by_server: dict[str, list[dict]]) -> None:
        super().__init__()
        self._tools_by_server = tools_by_server

    def compose(self) -> ComposeResult:
        total = sum(len(t) for t in self._tools_by_server.values())
        with Vertical(id="mcp-dialog"):
            yield Static(
                f"🔧  MCP Servers  ·  {len(self._tools_by_server)} servers  ·  {total} 工具",
                id="mcp-title",
            )
            with VerticalScroll(id="mcp-scroll"):
                for server, tools in self._tools_by_server.items():
                    tbl = RichTable(
                        box=SIMPLE, show_header=True, expand=True, padding=(0, 1)
                    )
                    tbl.add_column("工具", style="cyan bold", no_wrap=True)
                    tbl.add_column("說明", style="dim")
                    for t in tools:
                        tbl.add_row(t["name"], t.get("description") or "—")
                    with Collapsible(
                        title=f"{server}  ({len(tools)} 工具)", collapsed=False
                    ):
                        yield Static(tbl)
            yield Button("關閉  [Esc / Q]", variant="default", id="mcp-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()
