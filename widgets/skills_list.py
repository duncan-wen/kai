"""SkillsListScreen — ModalScreen 列出所有可用 skill（鏡像 MCPListScreen）。"""

from __future__ import annotations

from rich.box import SIMPLE
from rich.table import Table as RichTable
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from core.skills import Skill


class SkillsListScreen(ModalScreen):
    BINDINGS = [Binding("escape,q", "dismiss", show=False)]

    DEFAULT_CSS = """
    SkillsListScreen {
        align: center middle;
    }
    #skills-dialog {
        width: 82%;
        max-height: 85%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #skills-title {
        text-style: bold;
        color: $accent;
        content-align: center middle;
        margin-bottom: 1;
    }
    #skills-scroll {
        max-height: 1fr;
    }
    #skills-close {
        margin-top: 1;
        width: 100%;
        align-horizontal: center;
    }
    """

    def __init__(self, skills: list[Skill]) -> None:
        super().__init__()
        self._skills = skills

    def compose(self) -> ComposeResult:
        with Vertical(id="skills-dialog"):
            yield Static(
                f"🧩  Skills  ·  {len(self._skills)} 個技能",
                id="skills-title",
            )
            with VerticalScroll(id="skills-scroll"):
                tbl = RichTable(
                    box=SIMPLE, show_header=True, expand=True, padding=(0, 1)
                )
                tbl.add_column("名稱", style="cyan bold", no_wrap=True)
                tbl.add_column("說明", style="dim")
                tbl.add_column("觸發時機", style="dim")
                for s in self._skills:
                    tbl.add_row(s.name, s.description or "—", s.when_to_use or "—")
                yield Static(tbl)
            yield Button("關閉  [Esc / Q]", variant="default", id="skills-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()
