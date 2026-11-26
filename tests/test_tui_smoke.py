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
    """在暫存目錄建立 ChatApp；停用 MCP。prompt history 寫在暫存目錄的 .kai/ 下。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kai").mkdir()
    import app as app_module

    monkeypatch.setattr(app_module, "load_mcp_servers", lambda: {})

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
        before = app.debug_tools
        await _submit(app, pilot, "/debug")
        assert app.debug_tools is not before
        await _submit(app, pilot, "/debug")
        assert app.debug_tools is before

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
        before = app.debug_tools
        result = debug_hit.command()
        if asyncio.iscoroutine(result):
            await result
        await pilot.pause()
        assert app.debug_tools is not before

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

        app.debug_tools = False
        await pilot.pause()
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
        assert "❯" in str(frozen)  # stop 後恢復待機標題
        await pilot.pause(0.2)
        assert prompt.border_title == frozen

        hidden = PromptSpinner(prompt, "背景", visible=lambda: False).start()
        await pilot.pause(0.2)
        assert "背景" not in str(prompt.border_title)
        hidden.stop()

    _run(app_factory, body)


def test_debug_mark_bound_to_prompt_title(app_factory) -> None:
    """App.debug_tools 透過 data_bind 驅動 prompt 標題；spinner 執行中也帶 debug 標記。"""

    async def body(app, pilot: Pilot) -> None:
        from widgets.input import HistoryInput, PromptSpinner

        prompt = app.query_one("#prompt", HistoryInput)
        app.debug_tools = False
        await pilot.pause()
        assert "debug" not in str(prompt.border_title)
        app.debug_tools = True
        await pilot.pause()
        assert "debug" in str(prompt.border_title)
        spinner = PromptSpinner(prompt, "生成中").start()
        app.debug_tools = False
        await pilot.pause(0.2)
        # spinner 執行中，debug_mark 改變不會蓋掉 spinner 標題
        assert "生成中" in str(prompt.border_title)
        assert "debug" not in str(prompt.border_title)
        spinner.stop()

    _run(app_factory, body)


def test_agent_view_drives_cap_bar_sidebar_and_subtitle(app_factory) -> None:
    async def body(app, pilot: Pilot) -> None:
        from textual.widgets import Static

        from core.app_state import AgentView

        view = app.agent_view
        assert view.model == app.agent.profile.name
        assert view.mcp == ()  # MCP 停用 → 初始化完成且沒有 server
        assert app.sub_title.startswith(view.display_name)
        sidebar_model = app.query_one("#sb-model", Static)
        assert str(sidebar_model.render()) == view.model
        assert "未啟用" in str(app.query_one("#sb-mcp", Static).render())

        think = app.query_one("#cap-think", Static)
        vision = app.query_one("#cap-vision", Static)
        assert vision.display is view.supports_vision

        app.agent_view = AgentView(
            display_name="測試",
            model="x-model",
            supports_thinking=True,
            supports_vision=False,
            thinking_on=True,
            mcp=(("db", 3),),
            session_name="s1",
        )
        await pilot.pause()
        assert app.sub_title == "測試 · s1"
        assert str(sidebar_model.render()) == "x-model"
        assert "db" in str(app.query_one("#sb-mcp", Static).render())
        assert think.has_class("cap-think-on")
        assert not think.has_class("cap-inactive")
        assert vision.display is False
        assert app.query_one("#prompt").placeholder == "傳訊息給 測試..."

    _run(app_factory, body)


def test_think_toggle_via_cap_bar_click(app_factory) -> None:
    async def body(app, pilot: Pilot) -> None:
        before = app._thinking_active()
        await pilot.click("#cap-think")
        await pilot.pause()
        assert app._thinking_active() is not before
        assert app.agent_view.thinking_on is not before
        think = app.query_one("#cap-think")
        if app.agent.profile.supports_thinking:
            assert think.has_class("cap-think-on") is (not before)

    _run(app_factory, body)


def test_prompt_lock_follows_busy_tab(app_factory, tmp_path: Path) -> None:
    """busy_tabs + active_tab 決定 prompt 鎖定；切 tab 自動鎖／解鎖，重疊工作要全部結束才解鎖。"""

    async def body(app, pilot: Pilot) -> None:
        from textual.widgets import TabbedContent

        from widgets.input import HistoryInput

        prompt = app.query_one("#prompt", HistoryInput)
        tabs = app.query_one("#center-tabs", TabbedContent)

        app.mark_busy("chat")
        await pilot.pause()
        assert app.prompt_locked and prompt.disabled and prompt.has_class("streaming")

        # 切到檔案 tab：該 tab 沒在忙 → 解鎖
        f = tmp_path / "a.txt"
        f.write_text("hi", encoding="utf-8")
        await app._open_file_tab(f).wait()  # @work：回傳 Worker
        await pilot.pause()
        assert app.active_tab != "chat"
        assert not app.prompt_locked and not prompt.disabled

        # 切回 chat → 再次鎖住
        tabs.active = "chat"
        await pilot.pause()
        assert app.active_tab == "chat" and prompt.disabled

        # 同一 tab 兩件工作：結束一件仍鎖，全部結束才解鎖並 focus
        app.mark_busy("chat")
        app.unmark_busy("chat")
        await pilot.pause()
        assert prompt.disabled
        app.unmark_busy("chat")
        await pilot.pause()
        assert not prompt.disabled and not prompt.has_class("streaming")
        assert prompt.has_focus
        assert "❯" in str(prompt.border_title)

        # 多 unmark 不會出錯
        app.unmark_busy("chat")
        assert app.busy_tabs == ()

    _run(app_factory, body)


# ── PromptBar ───────────────────────────────────────────────────────────────


def test_prompt_history_navigation(app_factory, tmp_path: Path) -> None:
    """送出寫進歷史（重複的不重記、會存檔）；Up/Down 走歷史，走回底還原草稿。"""

    async def body(app, pilot: Pilot) -> None:
        from widgets.prompt_bar import PromptBar, load_prompt_history

        bar = app.query_one(PromptBar)
        prompt = bar.prompt
        await _submit(app, pilot, "/greeting off")
        await _submit(app, pilot, "/greeting on")
        await _submit(app, pilot, "/greeting on")
        assert bar.history[-2:] == ["/greeting off", "/greeting on"]
        assert prompt.value == ""
        saved = load_prompt_history(tmp_path / ".kai" / ".prompt_history")
        assert saved == ["/greeting off", "/greeting on"]

        prompt.value = "草稿"
        prompt.focus()
        await pilot.press("up")
        assert prompt.value == "/greeting on"
        await pilot.press("up")
        await pilot.press("up")  # 已到最舊，停住
        assert prompt.value == "/greeting off"
        await pilot.press("down")
        assert prompt.value == "/greeting on"
        await pilot.press("down")
        assert prompt.value == "草稿"

    _run(app_factory, body)


def test_prompt_multiline_paste_expands_on_submit(app_factory) -> None:
    """多行貼上顯示成 marker；送出時換回完整內容，前後打的字保留。"""

    async def body(app, pilot: Pilot) -> None:
        from textual import events

        from widgets.prompt_bar import PromptBar

        bar = app.query_one(PromptBar)
        prompt = bar.prompt
        submitted: list[str] = []
        # 一般文字會走 App 的分派 → _send；攔下來避免真的呼叫模型
        app._send = lambda full, original_text=None: submitted.append(original_text)

        prompt.focus()
        prompt.value = "看這段 "
        prompt.cursor_position = len(prompt.value)
        app.post_message(events.Paste("第一行\n第二行\n第三行"))
        await pilot.pause()
        assert prompt.value.startswith("看這段 [3 行] 第一行")
        prompt.value += " 怎麼樣"
        await prompt.action_submit()
        await pilot.pause()
        assert submitted == ["看這段 第一行\n第二行\n第三行 怎麼樣"]
        assert bar.history[-1] == submitted[0]
        # 歷史叫回時重新壓縮成 marker
        await pilot.press("up")
        assert prompt._is_marker(prompt.value)

    _run(app_factory, body)


def test_prompt_filters_mouse_sequences(app_factory) -> None:
    async def body(app, pilot: Pilot) -> None:
        from textual import events

        from widgets.prompt_bar import PromptBar

        prompt = app.query_one(PromptBar).prompt
        prompt.focus()
        app.post_message(events.Paste("<35;80;25M"))
        await pilot.pause()
        assert prompt.value == ""
        prompt.value = "80;25M"
        await pilot.pause()
        assert prompt.value == ""

    _run(app_factory, body)


def test_prompt_attach_inserts_path(app_factory, tmp_path: Path) -> None:
    async def body(app, pilot: Pilot) -> None:
        from widgets.prompt_bar import PromptBar

        bar = app.query_one(PromptBar)
        prompt = bar.prompt
        prompt.value = "說明"
        bar.attach(tmp_path / "a.py")
        assert prompt.value == f"說明 @{tmp_path / 'a.py'} "
        bar.attach(tmp_path / "b.png")
        assert prompt.value == f"/image {tmp_path / 'b.png'} "
        assert prompt.cursor_position == len(prompt.value)

    _run(app_factory, body)


def test_prompt_up_down_leave_open_dropdown_alone(app_factory) -> None:
    """自動完成下拉開著時 Up 不切歷史；Esc 先關下拉。"""

    async def body(app, pilot: Pilot) -> None:
        from widgets.prompt_bar import PromptBar

        bar = app.query_one(PromptBar)
        await _submit(app, pilot, "/greeting on")
        bar.prompt.focus()
        await pilot.press("/", "m", "c")
        await pilot.pause(0.3)
        assert bar.autocomplete.display
        assert (
            bar.autocomplete.region.height > 3
        )  # 掛在 screen 上，不被 3 行高的輸入列裁掉
        await pilot.press("up")
        assert bar.prompt.value == "/mc"
        await pilot.press("escape")
        await pilot.pause()
        assert not bar.autocomplete.display
        assert bar.prompt.value == "/mc"

    _run(app_factory, body)
