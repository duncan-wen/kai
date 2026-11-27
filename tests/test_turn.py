"""core.turn — 用 FakeModel 驅動整個 turn，不需要模型伺服器。"""

import asyncio

import pytest

from core.turn import (
    Compacted,
    Compacting,
    Done,
    StepReset,
    ThinkingDone,
    Token,
    ToolEvent,
    TurnRequest,
    run_turn,
    split_prediction,
    visible_text,
)
from core.model_stream import CancelToken
from tests.fake_model import FakeModel, reply, tool_step


@pytest.fixture
def make_agent(tmp_path):
    from core.agent import Agent

    def _make(script, model: str = "qwen3.5:4b", **kw):
        agent = Agent(
            model=model, history_file=tmp_path / "history.json", agent_dir=tmp_path
        )
        agent.model_stream = FakeModel(script, **kw)
        return agent

    return _make


def user(text: str = "hi") -> dict:
    return {"role": "user", "content": text}


def run(agent, request, cancel=None):
    events: list = []

    async def sink(ev):
        events.append(ev)

    done = asyncio.run(run_turn(agent, request, sink, cancel))
    return done, events


def non_system(agent) -> list[dict]:
    return [m for m in agent.history if m["role"] != "system"]


# ── 純函式 ────────────────────────────────────────────────────────────────────


def test_visible_text_hides_next_and_partial_tag():
    assert visible_text("你好<next>下一句") == "你好"
    assert visible_text("你好 <nex") == "你好 "
    assert visible_text("a < b") == "a < b"


def test_split_prediction():
    assert split_prediction("答案\n<next>再來</next>") == ("答案", "再來")
    assert split_prediction("答案<next>" + "長" * 51 + "</next>") == ("答案", "")
    assert split_prediction("答案<next>沒關") == ("答案", "")
    assert split_prediction("純文字") == ("純文字", "")


# ── 一般回覆 ──────────────────────────────────────────────────────────────────


def test_plain_reply_commits_and_streams(make_agent):
    agent = make_agent([reply("你", "好<ne", "xt>猜你想問</next>", gen_tokens=7)])
    done, events = run(agent, TurnRequest(user("嗨")))

    assert done.reason == "complete"
    assert done.reply == "你好"
    assert done.prediction == "猜你想問"
    assert done.stats.usage.gen_tokens == 7
    assert events[-1] is done
    tokens = [e for e in events if isinstance(e, Token)]
    assert [t.text for t in tokens] == ["你", "你好", "你好"]
    assert non_system(agent) == [user("嗨"), {"role": "assistant", "content": "你好"}]


def test_thinking_only_is_empty_and_not_committed(make_agent):
    agent = make_agent([reply(thinking="想了很久")])
    done, events = run(agent, TurnRequest(user()))

    assert done.reason == "empty"
    assert [e.text for e in events if isinstance(e, ThinkingDone)] == ["想了很久"]
    assert non_system(agent) == []


def test_thinking_done_sent_once_before_text(make_agent):
    agent = make_agent([reply("a", "b", thinking="嗯")])
    _, events = run(agent, TurnRequest(user()))
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["ThinkingDone", "Token", "Token", "Done"]


def test_step_reset_forwarded(make_agent):
    agent = make_agent([reply("壞", "好", reset_before=1)])
    done, events = run(agent, TurnRequest(user()))
    assert any(isinstance(e, StepReset) for e in events)
    assert done.reply == "好"


def test_think_none_follows_model_default(make_agent):
    agent = make_agent([reply("x"), reply("y")])
    run(agent, TurnRequest(user()))
    run(agent, TurnRequest(user(), think=False))
    assert [c.think for c in agent.model_stream.calls] == [
        agent.profile.supports_thinking,
        False,
    ]


# ── tool loop ─────────────────────────────────────────────────────────────────


def test_tool_loop_two_steps(make_agent):
    seen: list[str] = []

    async def echo(text: str) -> str:
        seen.append(text)
        return f"echo:{text}"

    agent = make_agent([tool_step(("echo", {"text": "hi"})), reply("完成")])
    agent.tools_map["echo"] = echo
    done, events = run(agent, TurnRequest(user()))

    assert done.reason == "complete"
    assert seen == ["hi"]
    tool_events = [e for e in events if isinstance(e, ToolEvent)]
    assert [(e.name, e.args, e.result.output) for e in tool_events] == [
        ("echo", {"text": "hi"}, "echo:hi")
    ]
    second = agent.model_stream.calls[1].messages
    assert second[-1] == {
        "role": "tool",
        "tool_call_id": "c0",
        "name": "echo",
        "content": "echo:hi",
    }
    # history 只存 user + 最終回覆
    assert non_system(agent) == [user(), {"role": "assistant", "content": "完成"}]


def test_tool_error_does_not_break_turn(make_agent):
    async def boom() -> str:
        raise ValueError("壞了")

    agent = make_agent([tool_step(("boom", {})), reply("抱歉")])
    agent.tools_map["boom"] = boom
    done, events = run(agent, TurnRequest(user()))

    assert done.reason == "complete"
    (ev,) = [e for e in events if isinstance(e, ToolEvent)]
    assert ev.result.is_error


def test_bad_json_args_reported_to_model_then_fixed(make_agent):
    calls: list[str] = []

    async def echo(text: str) -> str:
        calls.append(text)
        return text

    agent = make_agent(
        [
            tool_step(("echo", "{bad json")),
            tool_step(("echo", '{"text": "ok"}')),
            reply("好了"),
        ]
    )
    agent.tools_map["echo"] = echo
    done, events = run(agent, TurnRequest(user()))

    assert done.reason == "complete"
    assert calls == ["ok"]
    first, second = [e for e in events if isinstance(e, ToolEvent)]
    assert first.result.is_error and "JSON" in first.result.output
    assert not second.result.is_error
    assert "JSON" in agent.model_stream.calls[1].messages[-1]["content"]


def test_empty_string_args_mean_no_args(make_agent):
    async def ping() -> str:
        return "pong"

    agent = make_agent([tool_step(("ping", "")), reply("ok")])
    agent.tools_map["ping"] = ping
    _, events = run(agent, TurnRequest(user()))
    (ev,) = [e for e in events if isinstance(e, ToolEvent)]
    assert ev.result.output == "pong"


def test_tool_limit_commits_progress(make_agent, monkeypatch):
    monkeypatch.setattr("core.config.MAX_TOOL_CALLS", 2)

    async def ping() -> str:
        return "pong"

    agent = make_agent([tool_step(("ping", {})), tool_step(("ping", {}))])
    agent.tools_map["ping"] = ping
    done, _ = run(agent, TurnRequest(user()))

    assert done.reason == "tool_limit"
    assert "ping、ping" in done.reply
    assert non_system(agent)[-1] == {"role": "assistant", "content": done.reply}


# ── 取消 ──────────────────────────────────────────────────────────────────────


def test_cancel_during_stream(make_agent):
    agent = make_agent([reply("a", "b", "c")])
    cancel = CancelToken()
    events: list = []

    async def sink(ev):
        events.append(ev)
        if isinstance(ev, Token):
            cancel.cancel()

    done = asyncio.run(run_turn(agent, TurnRequest(user()), sink, cancel))
    assert done.reason == "cancelled"
    assert non_system(agent) == []


def test_cancel_during_tools_waits_for_batch(make_agent):
    cancel = CancelToken()
    finished: list[str] = []

    async def slow(name: str) -> str:
        cancel.cancel()
        await asyncio.sleep(0)
        finished.append(name)
        return name

    agent = make_agent(
        [tool_step(("slow", {"name": "a"}), ("slow", {"name": "b"})), reply("x")]
    )
    agent.tools_map["slow"] = slow
    done, events = run(agent, TurnRequest(user()), cancel)

    assert done.reason == "cancelled"
    assert sorted(finished) == ["a", "b"]  # 這批跑完才停
    # 已經跑完的工具照樣回報（可能有副作用，UI 要看得到）
    assert [e.args["name"] for e in events if isinstance(e, ToolEvent)] == ["a", "b"]
    assert len(agent.model_stream.calls) == 1
    assert non_system(agent) == []


# ── 錯誤與 autocompact ────────────────────────────────────────────────────────


def _fake_compact(agent, log: list):
    async def compact() -> str:
        log.append("compact")
        system = [m for m in agent.history if m["role"] == "system"]
        agent.history = system + [{"role": "assistant", "content": "[對話摘要]"}]
        return "摘要"

    agent.compact_history = compact
    agent.compact_available = lambda: True


def test_overflow_compacts_and_retries_same_message(make_agent):
    msg = user("@file 很長的內容")
    agent = make_agent([RuntimeError("context length exceeded"), reply("好")])
    log: list = []
    _fake_compact(agent, log)
    done, events = run(agent, TurnRequest(msg))

    assert done.reason == "complete"
    assert log == ["compact"]
    assert [type(e) for e in events[:2]] == [Compacting, Compacted]
    retry = agent.model_stream.calls[1].messages
    assert retry[-1] == msg
    assert retry[-2] == {"role": "assistant", "content": "[對話摘要]"}


def test_overflow_twice_gives_error(make_agent):
    agent = make_agent(
        [RuntimeError("context window full"), RuntimeError("context window full")]
    )
    log: list = []
    _fake_compact(agent, log)
    done, _ = run(agent, TurnRequest(user()))

    assert done.reason == "error"
    assert done.error.kind == "overflow"
    assert "壓縮後仍失敗" in done.error.message
    assert log == ["compact"]


def test_overflow_after_tools_does_not_rerun_them(make_agent):
    runs: list[str] = []

    async def side_effect(x: str) -> str:
        runs.append(x)
        return "done"

    agent = make_agent(
        [
            tool_step(("side_effect", {"x": "1"})),
            RuntimeError("context length exceeded"),
            reply("好"),
        ]
    )
    agent.tools_map["side_effect"] = side_effect
    log: list = []
    _fake_compact(agent, log)
    done, events = run(agent, TurnRequest(user("做事")))

    assert done.reason == "complete"
    assert runs == ["1"]  # 重試接著原本的進度，不重跑工具
    assert len([e for e in events if isinstance(e, ToolEvent)]) == 1
    retry = agent.model_stream.calls[2].messages
    assert retry[-1]["role"] == "tool"
    assert retry[-3] == user("做事")


def test_overflow_without_compact_backend(make_agent):
    agent = make_agent([RuntimeError("prompt is too long")])
    agent.compact_available = lambda: False
    done, _ = run(agent, TurnRequest(user()))
    assert done.error.kind == "overflow"
    assert "沒有可用的壓縮後端" in done.error.message
    assert "壓縮後仍失敗" not in done.error.message


def test_saturated_context_with_empty_reply_is_overflow(make_agent):
    async def ls() -> str:
        return "很長的結果"

    agent = make_agent(
        [
            tool_step(("ls", {})),
            reply(gen_tokens=1, prompt_tokens=16383, num_ctx=16384),
        ]
    )
    agent.tools_map["ls"] = ls
    log: list = []
    _fake_compact(agent, log)
    done, _ = run(agent, TurnRequest(user()))

    assert done.reason == "error"
    assert done.error.kind == "overflow"
    assert "16383/16384" in done.error.message
    assert log == []  # 不 autocompact
    assert non_system(agent) == []


def test_empty_reply_below_context_limit_stays_empty(make_agent):
    agent = make_agent([reply(prompt_tokens=9000, num_ctx=16384)])
    done, _ = run(agent, TurnRequest(user()))
    assert done.reason == "empty"


def test_model_errors_become_done(make_agent):
    from core.model_stream import QuotaError

    agent = make_agent(
        [TimeoutError("卡住"), QuotaError("沒額度"), ConnectionError("連不上")]
    )
    kinds = [run(agent, TurnRequest(user()))[0].error.kind for _ in range(3)]
    assert kinds == ["timeout", "quota", "other"]
    assert non_system(agent) == []


def test_vision_check(make_agent):
    agent = make_agent([], model="qwen2.5-coder:3b")
    done, _ = run(agent, TurnRequest({**user(), "images": ["/tmp/x.png"]}))
    assert done.reason == "error"
    assert agent.model_stream.calls == []


# ── persist ───────────────────────────────────────────────────────────────────


def test_persist_reply_only(make_agent):
    agent = make_agent([reply("歡迎")])
    run(agent, TurnRequest(user("請打招呼"), persist="reply_only"))
    assert non_system(agent) == [{"role": "assistant", "content": "歡迎"}]


def test_persist_none_then_commit(make_agent):
    agent = make_agent([reply("答")])
    done, _ = run(agent, TurnRequest(user("問"), persist="none"))
    assert non_system(agent) == []
    asyncio.run(agent.commit(list(done.new_messages)))
    assert non_system(agent) == [user("問"), {"role": "assistant", "content": "答"}]


# ── reasoning / lock / 換模型 ─────────────────────────────────────────────────


def test_keeps_reasoning(make_agent):
    async def ping() -> str:
        return "pong"

    agent = make_agent(
        [tool_step(("ping", {}), thinking="先查"), reply("答", thinking="想")],
        keeps_reasoning=True,
    )
    agent.tools_map["ping"] = ping
    run(agent, TurnRequest(user()))

    tool_asst = agent.model_stream.calls[1].messages[-2]
    assert tool_asst["reasoning_content"] == "先查"
    assert non_system(agent)[-1] == {
        "role": "assistant",
        "content": "答",
        "reasoning_content": "想",
    }


def test_no_reasoning_when_adapter_does_not_keep_it(make_agent):
    agent = make_agent([reply("答", thinking="想")])
    run(agent, TurnRequest(user()))
    assert "reasoning_content" not in non_system(agent)[-1]


def test_lock_serializes_turns(make_agent):
    order: list[str] = []
    agent = make_agent(
        [
            reply("a1", "a2", on_start=lambda: order.append("A")),
            reply("b1", "b2", on_start=lambda: order.append("B")),
        ]
    )

    async def sink(ev):
        if isinstance(ev, Done):
            order.append("done")

    async def both():
        await asyncio.gather(
            run_turn(agent, TurnRequest(user("1")), sink),
            run_turn(agent, TurnRequest(user("2")), sink),
        )

    asyncio.run(both())
    assert order == ["A", "done", "B", "done"]


def test_switch_model_during_turn_applies_after(make_agent):
    models: list[str] = []
    agent = make_agent([])
    agent.model_stream = FakeModel(
        [
            tool_step(
                ("switch_model", {"model": "gemma4:e4b"}),
                on_start=lambda: models.append(agent.profile.name),
            ),
            reply("換好了", on_start=lambda: models.append(agent.profile.name)),
        ]
    )
    results: list[bool] = []

    async def switch_model(model: str) -> str:
        results.append(agent.switch_model(model))
        return "ok"

    agent.tools_map["switch_model"] = switch_model
    run(agent, TurnRequest(user()))

    assert models == ["qwen3.5:4b", "qwen3.5:4b"]
    assert results == [False]
    assert agent.profile.name == "gemma4:e4b"
