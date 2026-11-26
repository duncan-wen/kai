"""TUI smoke test：用 Textual 的 run_test() + Pilot 以 headless 模式驅動 ChatApp。

只驗證不需要模型 / 網路的 UI 行為（slash 指令、命令面板、自動完成），
作為重構 UI 層的安全網。每個測試都在獨立的暫存目錄啟動，不碰使用者的 .kai/。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from textual.pilot import Pilot


@pytest.fixture
def app_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """在暫存目錄建立 ChatApp；停用 MCP 與 prompt history 檔案。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kai").mkdir()
    import app as app_module

    monkeypatch.setattr(app_module, "load_mcp_servers", lambda: {})
    monkeypatch.setattr(app_module, "_append_prompt_history", lambda text: None)

    def _make():
        return app_module.ChatApp()

    return _make


def _run(app_factory, body: Callable[[object, Pilot], Awaitable[None]]) -> None:
    async def _main() -> None:
        app = app_factory()
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            await body(app, pilot)

    asyncio.run(_main())


async def _submit(app, pilot: Pilot, text: str) -> None:
    from widgets.input import HistoryInput

    prompt = app.query_one("#prompt", HistoryInput)
    prompt.value = text
    prompt.focus()
    # 直接送出：按 Enter 會被自動完成下拉選單攔截（例如 /model 有候選項時）
    await prompt.action_submit()
    await pilot.pause()


def _notifications(app) -> list[str]:
    return [n.message for n in app._notifications]


def test_app_starts(app_factory) -> None:
    async def body(app, pilot: Pilot) -> None:
        assert app.query_one("#prompt").has_focus
        assert app.query_one("#center-tabs").active == "chat"

    _run(app_factory, body)


def test_unknown_command_warns(app_factory) -> None:
    async def body(app, pilot: Pilot) -> None:
        await _submit(app, pilot, "/no-such-cmd")
        assert any("未知指令" in m for m in _notifications(app))

    _run(app_factory, body)


def test_debug_toggles(app_factory) -> None:
    async def body(app, pilot: Pilot) -> None:
        before = app.ui_state.debug_tools
        await _submit(app, pilot, "/debug")
        assert app.ui_state.debug_tools is not before
        await _submit(app, pilot, "/debug")
        assert app.ui_state.debug_tools is before

    _run(app_factory, body)


def test_usage_warnings_for_missing_args(app_factory) -> None:
    async def body(app, pilot: Pilot) -> None:
        for cmd in ("/ask", "/remember", "/model", "/create-agent", "/load-agent"):
            await _submit(app, pilot, cmd)
        usages = [m for m in _notifications(app) if m.startswith("用法")]
        assert len(usages) == 5

    _run(app_factory, body)


def test_remember_writes_notes(app_factory, tmp_path: Path) -> None:
    async def body(app, pilot: Pilot) -> None:
        await _submit(app, pilot, "/remember 測試規則")
        notes = (tmp_path / ".kai" / "notes.md").read_text(encoding="utf-8")
        assert "- 測試規則" in notes
        await _submit(app, pilot, "/remember 測試規則")
        assert any("已存在相同記錄" in m for m in _notifications(app))

    _run(app_factory, body)


def test_greeting_toggle(app_factory) -> None:
    async def body(app, pilot: Pilot) -> None:
        await _submit(app, pilot, "/greeting off")
        assert app.ui_state.greeting_enabled is False
        await _submit(app, pilot, "/greeting on")
        assert app.ui_state.greeting_enabled is True

    _run(app_factory, body)


def test_save_without_ask_warns(app_factory) -> None:
    async def body(app, pilot: Pilot) -> None:
        await _submit(app, pilot, "/save")
        assert any("沒有可保存" in m for m in _notifications(app))

    _run(app_factory, body)


def test_autocomplete_lists_slash_commands(app_factory) -> None:
    async def body(app, pilot: Pilot) -> None:
        from textual_autocomplete import TargetState

        from widgets.input import ChatAutoComplete

        ac = app.query_one(ChatAutoComplete)
        items = ac.get_candidates(TargetState(text="/mcp", cursor_position=4))
        names = {str(i.main) for i in items}
        assert {"/mcp-list", "/mcp-reload", "/mcp-add", "/mcp-del"} <= names

    _run(app_factory, body)


def test_command_palette_opens(app_factory) -> None:
    async def body(app, pilot: Pilot) -> None:
        from textual.command import CommandPalette

        await pilot.press("ctrl+p")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
        await pilot.press("d", "e", "b", "u", "g")
        await pilot.pause(0.3)
        await pilot.press("escape")
        await pilot.pause()

    _run(app_factory, body)


def test_command_palette_hits_run(app_factory) -> None:
    """命令面板的 provider 例外會被 Textual 吞掉，所以直接驗證 hit 清單與執行。"""

    async def body(app, pilot: Pilot) -> None:
        from widgets.commands import ChatCommands

        provider = ChatCommands(app.screen)
        hits = [h async for h in provider.discover()]
        labels = [str(h.match_display) for h in hits]
        assert any(label.startswith("/debug") for label in labels)
        debug_hit = next(h for h in hits if str(h.match_display).startswith("/debug"))
        before = app.ui_state.debug_tools
        result = debug_hit.command()
        if asyncio.iscoroutine(result):
            await result
        await pilot.pause()
        assert app.ui_state.debug_tools is not before

    _run(app_factory, body)


def test_every_slash_command_has_handler(app_factory) -> None:
    """指令表裡每個指令都要對應到 ChatApp 上存在的 async handler。"""
    import inspect

    import app as app_module
    from widgets.slash import all_commands, completion_items

    names = [c.name for c in all_commands()]
    assert len(names) == len(set(names))
    for cmd in all_commands():
        handler = getattr(app_module.ChatApp, cmd.method, None)
        assert handler is not None, cmd.name
        assert inspect.iscoroutinefunction(handler), cmd.name
    completions = {text for text, _ in completion_items()}
    assert {"/tts off", "/greeting on", "/close-agent"} <= completions


def test_prompt_spinner_animates_while_disabled(app_factory) -> None:
    async def body(app, pilot: Pilot) -> None:
        from widgets.input import SPINNER, HistoryInput, PromptSpinner

        prompt = app.query_one("#prompt", HistoryInput)
        prompt.disabled = True
        spinner = PromptSpinner(prompt, "等待回覆").start()
        assert prompt.border_title == f"{SPINNER[0]} 等待回覆"
        await pilot.pause(0.3)
        spinner.label = "生成中"
        await pilot.pause(0.2)
        assert str(prompt.border_title).endswith(" 生成中")
        assert str(prompt.border_title)[0] in SPINNER
        spinner.stop()
        frozen = prompt.border_title
        await pilot.pause(0.2)
        assert prompt.border_title == frozen

        hidden = PromptSpinner(prompt, "背景", visible=lambda: False).start()
        await pilot.pause(0.2)
        assert "背景" not in str(prompt.border_title)
        hidden.stop()

    _run(app_factory, body)
