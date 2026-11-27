"""ChatLog / ReplySink：用 fake turn 事件驅動，檢查泡泡、卡片、通知、stats。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace

from textual.app import App, ComposeResult
from textual.widgets import Collapsible, Static

from core.model_stream import Usage
from core.tools.result import ToolResult
from core.turn import (
    Compacted,
    Compacting,
    Done,
    StepReset,
    ThinkingDone,
    ThinkingStarted,
    Token,
    ToolEvent,
    TurnError,
    TurnStats,
)
from widgets.chat import AIMessage
from widgets.chat_log import ChatLog, format_stats

AGENT = SimpleNamespace(display_name="小幫手", history=[])


class _LogApp(App):
    def compose(self) -> ComposeResult:
        yield ChatLog()


def _run(body: Callable[[ChatLog], Awaitable[None]]) -> None:
    async def _main() -> None:
        app = _LogApp()
        async with app.run_test(size=(100, 40)) as pilot:
            log = app.query_one(ChatLog)
            await body(log)
            await pilot.pause()

    asyncio.run(_main())


def _texts(log: ChatLog, selector: str) -> list[str]:
    return [str(w.render()) for w in log.query(selector)]


def _titles(log: ChatLog) -> list[str]:
    return [c.title for c in log.query(Collapsible)]


async def _play(log: ChatLog, events: list, *, debug: bool = False) -> list[str]:
    phases: list[str] = []
    sink = log.start_reply(AGENT, debug=debug, on_phase=phases.append)
    for ev in events:
        await sink(ev)
    return phases


def test_reply_renders_header_bubble_and_stats() -> None:
    async def body(log: ChatLog) -> None:
        stats = TurnStats(elapsed=1.2, usage=Usage(gen_tokens=5, prompt_tokens=50))
        phases = await _play(
            log,
            [
                ThinkingStarted(),
                ThinkingDone("想一下", 0.5),
                Token("哈", "哈"),
                Token("囉", "哈囉"),
                Done("complete", reply="哈囉", stats=stats),
            ],
        )
        assert phases == ["思考中", "生成中", "生成中"]  # ThinkingDone 不改階段
        assert _texts(log, ".ai-header") == ["🤖 小幫手"]
        assert [m._raw for m in log.query(AIMessage)] == ["哈囉"]
        assert _titles(log) == ["💡 思考過程"]
        assert _texts(log, ".stats") == [format_stats(stats)]

    _run(body)


def test_tool_round_splits_bubbles_and_reset_clears_text() -> None:
    async def body(log: ChatLog) -> None:
        ok = ToolResult("web_search", "結果")
        await _play(
            log,
            [
                Token("先查", "先查"),
                ToolEvent("web_search", {"q": "x"}, ok),
                Token("亂", "亂"),
                StepReset(),
                Token("答案", "答案"),
                Done("complete", reply="答案"),
            ],
            debug=True,
        )
        assert [m._raw for m in log.query(AIMessage)] == ["先查", "答案"]
        assert _titles(log) == ["🔧 web_search"]

    _run(body)


def test_tool_error_card_shown_without_debug() -> None:
    async def body(log: ChatLog) -> None:
        await _play(
            log,
            [
                ToolEvent("web_search", {"q": "x"}, ToolResult("web_search", "ok")),
                ToolEvent(
                    "bash_exec", {"cmd": "x"}, ToolResult("bash_exec", "壞", True)
                ),
                Done("empty"),
            ],
        )
        assert len(_titles(log)) == 1 and "bash_exec" in _titles(log)[0]
        assert any("無文字回覆" in t for t in _texts(log, ".ai-header"))
        assert len(_texts(log, ".stats")) == 1

    _run(body)


def test_error_and_cancel_keep_partial_text_without_stats() -> None:
    async def body(log: ChatLog) -> None:
        await _play(
            log,
            [
                Token("一半", "一半"),
                Done("error", error=TurnError("timeout", "卡住")),
            ],
        )
        assert [m._raw for m in log.query(AIMessage)] == ["一半"]
        assert _texts(log, ".error-msg") == ["⏱ 模型無回應（逾時）：卡住"]
        assert _texts(log, ".stats") == []

    _run(body)


def test_step_reset_text_not_restored_on_error() -> None:
    async def body(log: ChatLog) -> None:
        await _play(
            log,
            [
                Token("作廢", "作廢"),
                StepReset(),
                Done("error", error=TurnError("other", "壞了")),
            ],
        )
        assert [m._raw for m in log.query(AIMessage)] == [""]

    _run(body)


def test_compact_and_tool_limit_show_notices() -> None:
    async def body(log: ChatLog) -> None:
        await _play(
            log, [Compacting(), Compacted(), Done("tool_limit", reply="⚠️ 工具上限")]
        )
        notices = _texts(log, ".notice")
        assert notices[0].startswith("⚡") and notices[1].startswith("✅")
        assert notices[2] == "⚠️ 工具上限"

    _run(body)


def test_user_text_is_not_parsed_as_markup() -> None:
    async def body(log: ChatLog) -> None:
        await log.add_user("看 [bold]這[/bold] 跟 [link]")
        (text,) = _texts(log, ".user-bubble")
        assert text.endswith("看 [bold]這[/bold] 跟 [link]")

    _run(body)


def test_render_history_replaces_content() -> None:
    async def body(log: ChatLog) -> None:
        await log.add_error("舊的")
        agent = SimpleNamespace(
            display_name="小幫手",
            history=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "問"},
                {"role": "user", "content": "轉來的", "_inbox_sender": "main"},
                {"role": "assistant", "content": "答"},
            ],
        )
        await log.render_history(agent)
        assert _texts(log, ".error-msg") == []
        assert len(_texts(log, ".user-bubble")) == 1
        assert _titles(log) == ["📨 來自 main"]
        assert [m._raw for m in log.query(AIMessage)] == ["答"]

    _run(body)


def test_add_output_returns_updatable_widget() -> None:
    async def body(log: ChatLog) -> None:
        out = await log.add_output("執行中")
        out.update("完成")
        assert isinstance(out, Static) and _texts(log, ".bash-output") == ["完成"]

    _run(body)
