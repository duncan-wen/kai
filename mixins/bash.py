"""BashMixin — ! 指令執行邏輯。"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

from rich.text import Text
from textual import work
from textual.widgets import Static

from core.config import BASH_TIMEOUT, get_nickname
from widgets.input import HistoryInput
from widgets.sidebar import Sidebar
from widgets.tree import FileTree

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app import ChatApp as _MixinBase
else:
    _MixinBase = object  # type: ignore[assignment,misc]


class BashMixin(_MixinBase):
    @work
    async def _run_bash(self, cmd: str) -> None:
        stripped = cmd.strip()
        if (
            stripped == "cd"
            or stripped.startswith("cd ")
            or stripped.startswith("cd\t")
        ):
            arg = stripped[2:].strip() or str(Path.home())
            try:
                await self._do_cd(arg)
            except Exception as e:
                self.notify(f"切換失敗：{e}", severity="error")
            return

        msgs = self._active_msgs()
        prompt = self.query_one("#prompt", HistoryInput)
        await msgs.mount(
            Static(f"[bold]$[/bold] {cmd}", markup=True, classes="user-bubble")
        )
        msgs.scroll_end(animate=False)
        prompt.disabled = True
        prompt.add_class("streaming")
        prompt.border_title = "⚙ 執行中..."
        try:
            env = {
                **os.environ,
                "CLICOLOR_FORCE": "1",
                "COLORTERM": "truecolor",
                "TERM": "xterm-256color",
                "COLUMNS": "120",
            }
            proc = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "-c",
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
            self._bash_proc = proc
            out_widget = Static(Text(""), markup=False, classes="bash-output")
            await msgs.mount(out_widget)
            lines: list[str] = []

            def _refresh() -> None:
                raw = "".join(lines) or "(no output)"
                try:
                    out_widget.update(Text.from_ansi(raw))
                except Exception:
                    out_widget.update(Text(raw))
                msgs.scroll_end(animate=False)

            async def _read(stream: asyncio.StreamReader, prefix: str = "") -> None:
                async for line in stream:
                    lines.append(prefix + line.decode(errors="replace"))
                    _refresh()

            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        _read(proc.stdout),
                        _read(proc.stderr, "[stderr] "),
                        proc.wait(),
                    ),
                    timeout=BASH_TIMEOUT,
                )
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                await proc.communicate()
                lines.append(f"\n⏱ 逾時（>{BASH_TIMEOUT}s）")
                _refresh()
        except Exception as e:
            await msgs.mount(
                Static(f"❌ {e or type(e).__name__}", markup=False, classes="error-msg")
            )
        finally:
            msgs.scroll_end(animate=False)
            prompt.disabled = False
            prompt.remove_class("streaming")
            self._update_prompt_title()
            prompt.placeholder = f"傳訊息給 {get_nickname(self.agent.profile.name)}..."
            prompt.focus()
            self._bash_proc = None
            self._bash_worker = None
            self.query_one("#sidebar", Sidebar).refresh_git()
            self.query_one("#file-tree", FileTree).refresh_tree()
