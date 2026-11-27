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
    """busy_jobs + active_tab 決定 prompt 鎖定；切 tab 自動鎖／解鎖，重疊工作要全部結束才解鎖。"""

    async def body(app, pilot: Pilot) -> None:
        from textual.widgets import TabbedContent

        from widgets.center_tabs import CenterTabs
        from widgets.input import HistoryInput

        prompt = app.query_one("#prompt", HistoryInput)
        tabs = app.query_one("#center-tabs", TabbedContent)

        app.mark_busy("chat")
        await pilot.pause()
        assert app.prompt_locked and prompt.disabled and prompt.has_class("streaming")

        # 切到檔案 tab：該 tab 沒在忙 → 解鎖
        f = tmp_path / "a.txt"
        f.write_text("hi", encoding="utf-8")
        await app.query_one(CenterTabs).open_file(f).wait()  # @work：回傳 Worker
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
        assert app.busy_jobs == ()

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


# ── CenterTabs ──────────────────────────────────────────────────────────────


async def _open(app, pilot: Pilot, path: Path) -> str:
    from widgets.center_tabs import CenterTabs

    tabs = app.query_one(CenterTabs)
    await tabs.open_file(path).wait()
    await pilot.pause()
    return tabs.active


def test_file_tabs_open_dedup_and_close(app_factory, tmp_path: Path) -> None:
    """同一檔案只開一個 tab；Ctrl+W 關目前 tab；Chat tab 關不掉。"""

    async def body(app, pilot: Pilot) -> None:
        from widgets.center_tabs import CenterTabs

        tabs = app.query_one(CenterTabs)
        a, b = tmp_path / "a.txt", tmp_path / "b.py"
        a.write_text("aa", encoding="utf-8")
        b.write_text("bb", encoding="utf-8")
        ta = await _open(app, pilot, a)
        tb = await _open(app, pilot, b)
        assert ta != tb and app.active_tab == tb
        assert await _open(app, pilot, a) == ta  # 已開啟 → 切過去
        assert tabs.tab_order() == ["chat", ta, tb]

        await pilot.press("ctrl+w")
        await pilot.pause()
        assert tabs.tab_order() == ["chat", tb]
        tabs.active = "chat"
        await pilot.pause()
        await pilot.press("ctrl+w")
        await pilot.pause()
        assert tabs.tab_order() == ["chat", tb]

    _run(app_factory, body)


def test_unsaved_file_tab_asks_before_close(app_factory, tmp_path: Path) -> None:
    async def body(app, pilot: Pilot) -> None:
        from widgets.center_tabs import CenterTabs
        from widgets.chat import ConfirmCloseScreen, FileViewer

        tabs = app.query_one(CenterTabs)
        f = tmp_path / "a.txt"
        f.write_text("aa", encoding="utf-8")
        tid = await _open(app, pilot, f)
        tabs.get_pane(tid).query_one(FileViewer)._modified = True

        await tabs.request_close(tid)
        await pilot.pause()
        assert isinstance(app.screen, ConfirmCloseScreen)
        await pilot.press("n")
        await pilot.pause()
        assert tid in tabs.tab_order()

        await tabs.request_close(tid)
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert tid not in tabs.tab_order()
        assert app.active_tab == "chat"

    _run(app_factory, body)


def test_click_x_closes_tab(app_factory, tmp_path: Path) -> None:
    """點標籤最右邊的 “×” 關閉；點標籤其他地方只切換。"""

    async def body(app, pilot: Pilot) -> None:
        from textual.widgets._tabbed_content import ContentTab

        from widgets.center_tabs import CenterTabs

        tabs = app.query_one(CenterTabs)
        f = tmp_path / "a.txt"
        f.write_text("aa", encoding="utf-8")
        tid = await _open(app, pilot, f)
        tabs.active = "chat"
        await pilot.pause()

        tab = tabs.query_one(f"#{ContentTab.add_prefix(tid)}", ContentTab)
        await pilot.click(tab, offset=(1, 0))
        await pilot.pause()
        assert tabs.active == tid and tid in tabs.tab_order()
        await pilot.click(tab, offset=(tab.region.width - 1, 0))
        await pilot.pause()
        assert tid not in tabs.tab_order()

    _run(app_factory, body)


def test_move_tab_keeps_chat_first(app_factory, tmp_path: Path) -> None:
    async def body(app, pilot: Pilot) -> None:
        from widgets.center_tabs import CenterTabs

        tabs = app.query_one(CenterTabs)
        paths = [tmp_path / f"{n}.txt" for n in "ab"]
        for p in paths:
            p.write_text(p.stem, encoding="utf-8")
        ta = await _open(app, pilot, paths[0])
        tb = await _open(app, pilot, paths[1])
        await pilot.press("ctrl+shift+left")
        await pilot.pause()
        assert tabs.tab_order() == ["chat", tb, ta]
        await pilot.press("ctrl+shift+left")  # 不能越過 Chat
        await pilot.pause()
        assert tabs.tab_order() == ["chat", tb, ta]
        await pilot.press("ctrl+shift+right")
        await pilot.pause()
        assert tabs.tab_order() == ["chat", ta, tb]

    _run(app_factory, body)


def test_agent_tab_close_clears_registry(app_factory, tmp_path: Path) -> None:
    """agent tab：開啟後成為作用中 Agent、訊息畫在自己的容器；關閉後 registry 移除、退回 main。"""

    async def body(app, pilot: Pilot) -> None:
        from core.agent import Agent
        from widgets.center_tabs import CenterTabs

        tabs = app.query_one(CenterTabs)
        agent = Agent(model=app.agent.profile.name, history_file=tmp_path / "h.json")
        tid = app.agents.add(agent)
        scroll = await tabs.add_agent_tab(tid, "測試")
        app._switch_to_tab(tid)
        await pilot.pause()
        assert app.agent is agent and app._active_msgs() is scroll

        await app._maybe_close_tab(tid)
        await pilot.pause()
        assert app.agents.get(tid) is None
        assert app.agents.active_id == "chat" and app.active_tab == "chat"
        assert app._active_msgs() is app.query_one("#messages")

    _run(app_factory, body)


# ── turn → UI（FakeModel 取代模型伺服器）──────────────────────────────────────


async def _wait_turn(app, pilot: Pilot) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


def test_send_renders_reply_stats_and_prediction(app_factory) -> None:
    from tests.fake_model import FakeModel, reply
    from widgets.chat import AIMessage
    from widgets.input import HistoryInput

    async def body(app, pilot: Pilot) -> None:
        app.agent.model_stream = FakeModel(
            [reply("哈", "囉<next>再見</next>", gen_tokens=3, prompt_tokens=100)]
        )
        await _submit(app, pilot, "你好")
        await _wait_turn(app, pilot)

        msgs = app._active_msgs()
        assert [m._raw for m in msgs.query(AIMessage)] == ["哈囉"]
        stats = [str(s.render()) for s in msgs.query(".stats")]
        assert len(stats) == 1 and "3 tokens" in stats[0]
        assert app.agent.history[-1] == {"role": "assistant", "content": "哈囉"}
        assert app.query_one("#prompt", HistoryInput)._pending_prediction == "再見"
        assert not app.prompt_locked

    _run(app_factory, body)


def test_ask_does_not_persist_until_save(app_factory) -> None:
    from tests.fake_model import FakeModel, reply

    async def body(app, pilot: Pilot) -> None:
        app.agent.model_stream = FakeModel([reply("臨時答案")])

        def chat_msgs() -> list[dict]:
            return [m for m in app.agent.history if m["role"] != "system"]

        before = chat_msgs()
        await _submit(app, pilot, "/ask 問一下")
        await _wait_turn(app, pilot)
        assert chat_msgs() == before

        await _submit(app, pilot, "/save")
        assert app.agent.history[-2:] == [
            {"role": "user", "content": "問一下"},
            {"role": "assistant", "content": "臨時答案"},
        ]

    _run(app_factory, body)


def test_turn_error_shows_message_and_unlocks(app_factory) -> None:
    from tests.fake_model import FakeModel

    async def body(app, pilot: Pilot) -> None:
        app.agent.model_stream = FakeModel([TimeoutError("卡住了")])
        await _submit(app, pilot, "hi")
        await _wait_turn(app, pilot)
        errors = [str(s.render()) for s in app._active_msgs().query(".error-msg")]
        assert any("逾時" in e and "卡住了" in e for e in errors)
        assert not app.prompt_locked

    _run(app_factory, body)


def test_dispatch_renders_inbox_reply_and_stats(app_factory) -> None:
    """其他 agent 送來的訊息：inbox 泡泡、回覆、stats 都和 _send 用同一套畫法。"""
    from textual.widgets import Collapsible

    from tests.fake_model import FakeModel, reply
    from widgets.chat import AIMessage

    async def body(app, pilot: Pilot) -> None:
        app.agent.model_stream = FakeModel([reply("收到", gen_tokens=2)])
        app._dispatch_to_agent("小助理", "chat", "幫我查一下")
        await _wait_turn(app, pilot)

        msgs = app._active_msgs()
        assert [c.title for c in msgs.query(Collapsible)] == ["📨 來自 小助理"]
        assert [m._raw for m in msgs.query(AIMessage)] == ["收到"]
        assert "2 tokens" in str(msgs.query_one(".stats").render())
        assert app.agent.history[-1] == {"role": "assistant", "content": "收到"}
        assert not app.prompt_locked

    _run(app_factory, body)


def test_dispatch_error_on_visible_tab_is_not_toasted_twice(app_factory) -> None:
    """目標 tab 就在畫面上：錯誤只畫在聊天區，不另外跳通知。"""
    from tests.fake_model import FakeModel

    async def body(app, pilot: Pilot) -> None:
        app.agent.model_stream = FakeModel([TimeoutError("卡住了")])
        app._dispatch_to_agent("小助理", "chat", "幫我查一下")
        await _wait_turn(app, pilot)

        errors = [str(s.render()) for s in app._active_msgs().query(".error-msg")]
        assert any("卡住了" in e for e in errors)
        assert not any("處理 小助理 的訊息失敗" in n for n in _notifications(app))

    _run(app_factory, body)


# ── tab 關閉／開啟的時序（fix/tab-close）──────────────────────────────────────


async def _open_agent_tab(app, pilot: Pilot, tmp_path: Path, nickname: str) -> str:
    from core.agent import Agent
    from widgets.center_tabs import CenterTabs

    agent = Agent(model=app.agent.profile.name, history_file=tmp_path / "h.json")
    agent.nickname = nickname
    tid = app.agents.add(agent)
    await app.query_one(CenterTabs).add_agent_tab(tid, nickname)
    app._switch_to_tab(tid)
    await pilot.pause()
    return tid


def test_closing_active_agent_tab_next_to_file_tab_shows_main(
    app_factory, tmp_path: Path
) -> None:
    """關掉作用中的 agent tab、畫面落在檔案 tab：側邊欄要退回 main，不停在已關掉的 agent。"""

    async def body(app, pilot: Pilot) -> None:
        f = tmp_path / "a.txt"
        f.write_text("aa", encoding="utf-8")
        fid = await _open(app, pilot, f)
        tid = await _open_agent_tab(app, pilot, tmp_path, "分身")
        assert app.agent_view.display_name == "分身"

        await app._maybe_close_tab(tid)
        await pilot.pause()
        assert app.agents.get(tid) is None
        assert app.agent is app.agents.main
        assert app.agent_view.display_name == app.agents.main.display_name
        assert fid in app.query_one("CenterTabs").tab_order()

    _run(app_factory, body)


def test_registry_is_cleared_before_pane_is_removed(
    app_factory, tmp_path: Path
) -> None:
    """pane 移除時 agent 已不在 registry：dispatch 不會把回覆畫進 main chat。"""

    async def body(app, pilot: Pilot) -> None:
        from widgets.center_tabs import CenterTabs

        tabs = app.query_one(CenterTabs)
        tid = await _open_agent_tab(app, pilot, tmp_path, "分身")
        seen: list[bool] = []
        real_remove = tabs.remove_pane

        def spy(pane_id: str):
            seen.append(app.agents.get(pane_id) is None)
            return real_remove(pane_id)

        tabs.remove_pane = spy
        await app._maybe_close_tab(tid)
        await pilot.pause()
        assert seen == [True]

    _run(app_factory, body)


def test_closing_tab_cancels_its_turns(app_factory, tmp_path: Path) -> None:
    async def body(app, pilot: Pilot) -> None:
        from core.model_stream import CancelToken

        tid = await _open_agent_tab(app, pilot, tmp_path, "分身")
        cancel = CancelToken()
        app.mark_busy(tid, cancel)
        try:
            await app._maybe_close_tab(tid)
            await pilot.pause()
            assert cancel.cancelled
        finally:
            app.unmark_busy(tid, cancel)

    _run(app_factory, body)


def test_double_close_removes_once(app_factory, tmp_path: Path) -> None:
    """連點兩下 ×：第二次在第一次還沒關完時直接略過。"""

    async def body(app, pilot: Pilot) -> None:
        from widgets.center_tabs import CenterTabs

        tabs = app.query_one(CenterTabs)
        f = tmp_path / "a.txt"
        f.write_text("aa", encoding="utf-8")
        fid = await _open(app, pilot, f)
        calls: list[str] = []
        real_remove = tabs.remove_pane

        def spy(pane_id: str):
            calls.append(pane_id)
            return real_remove(pane_id)

        tabs.remove_pane = spy
        await asyncio.gather(tabs.request_close(fid), tabs.request_close(fid))
        await pilot.pause()
        assert calls == [fid]
        assert fid not in tabs.tab_order()

    _run(app_factory, body)


def test_opening_same_file_twice_concurrently_gives_one_tab(
    app_factory, tmp_path: Path
) -> None:
    async def body(app, pilot: Pilot) -> None:
        from widgets.center_tabs import CenterTabs

        tabs = app.query_one(CenterTabs)
        f = tmp_path / "a.txt"
        f.write_text("aa", encoding="utf-8")
        w1, w2 = tabs.open_file(f), tabs.open_file(f)
        await w1.wait()
        await w2.wait()
        await pilot.pause()
        assert tabs.tab_order() == ["chat", tabs.active]

    _run(app_factory, body)
