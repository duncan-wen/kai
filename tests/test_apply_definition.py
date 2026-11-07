"""Agent definition 解析，以及把 definition 套用到 Agent 上（開啟、reload、換模型）。"""
import pytest

from core.agent_factory import AgentDefinition


def defn(**overrides) -> AgentDefinition:
    data = {"name": "writer", "model": "qwen3.5:4b", "system_prompt": "你是寫手。"}
    data.update(overrides)
    return AgentDefinition.model_validate(data)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (True, {"voice": "en-US-AriaNeural"}),
        ({"engine": "kokoro", "voice": "af_sky"}, {"engine": "kokoro", "voice": "af_sky"}),
        (False, None),
        ({}, None),
        (None, None),
    ],
)
def test_definition_normalizes_tts(raw, expected):
    assert defn(tts=raw).tts == expected


@pytest.fixture
def make_agent(tmp_path):
    from core.agent import Agent

    def _make(model: str = "qwen3.5:4b") -> Agent:
        return Agent(model=model, history_file=tmp_path / "history.json", agent_dir=tmp_path)

    return _make


def system_text(agent) -> str:
    assert agent.history[0]["role"] == "system"
    return agent.history[0]["content"]


def test_apply_definition_sets_every_per_agent_field(make_agent):
    agent = make_agent()

    agent.apply_definition(
        defn(
            nickname="小寫",
            history_keep=3,
            datasets=["kb-1"],
            tts=True,
            system_prompt="你是專職寫手，目錄在 {AGENT_DIR}。",
        )
    )

    assert agent.display_name == "小寫"
    assert agent.history_keep == 3
    assert agent.datasets == ["kb-1"]
    assert agent.tts_config == {"voice": "en-US-AriaNeural"}
    assert "你是專職寫手" in system_text(agent)
    assert agent.mcp_enabled is True


def test_reapplying_definition_resets_omitted_fields_to_defaults(make_agent):
    from core.config import HISTORY_KEEP

    agent = make_agent()
    agent.apply_definition(defn(nickname="小寫", history_keep=3, datasets=["kb-1"], tts=True))
    agent.mcp_client = object()  # type: ignore[assignment]

    agent.apply_definition(defn(mcp_servers={}))

    assert agent.display_name == "writer"
    assert agent.history_keep == HISTORY_KEEP
    assert agent.datasets == []
    assert agent.tts_config is None
    assert agent.mcp_enabled is False
    assert agent.mcp_client is None


def test_switch_model_keeps_everything_but_the_model(make_agent):
    agent = make_agent("qwen3.5:4b")
    agent.apply_definition(defn(nickname="小寫", history_keep=3, datasets=["kb-1"], tts=True))
    agent.history.append({"role": "user", "content": "寫一首詩"})
    agent.extra_tools = [{"type": "function", "function": {"name": "send_to_agent"}}]
    agent.tools_map["open_agent"] = lambda q: q

    agent.switch_model("gemma4:e4b")

    assert agent.profile.name == "gemma4:e4b"
    assert agent.display_name == "小寫"
    assert agent.history_keep == 3
    assert agent.datasets == ["kb-1"]
    assert agent.tts_config == {"voice": "en-US-AriaNeural"}
    assert agent.history[-1] == {"role": "user", "content": "寫一首詩"}
    assert "你是寫手" in system_text(agent)
    assert agent.extra_tools[0]["function"]["name"] == "send_to_agent"
    assert "open_agent" in agent.tools_map


def test_switch_model_rejects_unknown_model(make_agent):
    agent = make_agent("qwen3.5:4b")

    with pytest.raises(ValueError):
        agent.switch_model("no-such-model")

    assert agent.profile.name == "qwen3.5:4b"


def test_apply_definition_switches_to_the_definition_model(make_agent):
    agent = make_agent("qwen3.5:4b")

    changes = agent.apply_definition(defn(model="gemma4:e4b"))

    assert agent.profile.name == "gemma4:e4b"
    assert "model → gemma4:e4b" in changes


def test_apply_definition_with_unknown_model_keeps_current_model_and_applies_the_rest(make_agent):
    agent = make_agent("qwen3.5:4b")

    changes = agent.apply_definition(defn(model="no-such-model", nickname="小寫"))

    assert agent.profile.name == "qwen3.5:4b"
    assert agent.display_name == "小寫"
    assert any("no-such-model" in c for c in changes)


def test_reset_history_follows_disk_when_history_file_was_deleted(make_agent, tmp_path):
    agent = make_agent()
    agent.apply_definition(defn())
    agent.history.append({"role": "user", "content": "寫一首詩"})
    agent.save_history()
    (tmp_path / "history.json").unlink()

    agent.reset_history()

    assert [m["role"] for m in agent.history] == ["system"]
    assert "你是寫手" in system_text(agent)


def test_switch_model_during_a_turn_takes_effect_after_the_turn(make_agent, monkeypatch):
    """AI 在自己的 turn 中途呼叫 switch_model：這個 turn 要用原模型跑完，下一個 turn 才換。"""
    import asyncio
    from types import SimpleNamespace

    import core.agent as agent_module

    agent = make_agent("qwen3.5:4b")
    agent.apply_definition(defn())
    models_streamed: list[str] = []
    switch_results: list[bool] = []

    async def switch_tool(model: str) -> str:
        switch_results.append(agent.switch_model(model))
        return "ok"

    agent.tools_map["switch_model"] = switch_tool

    async def fake_stream_ollama(ag, messages, tools, reply_parts, tool_calls, *rest):
        # 假的模型串流（網路邊界）：第一輪要求換模型，第二輪回覆文字
        models_streamed.append(ag.profile.name)
        if len(models_streamed) == 1:
            tool_calls.append(SimpleNamespace(
                function=SimpleNamespace(name="switch_model", arguments={"model": "gemma4:e4b"})
            ))
        else:
            reply_parts.append("換好了")

    monkeypatch.setattr(agent_module, "stream_ollama", fake_stream_ollama)

    asyncio.run(agent.chat({"role": "user", "content": "換成 gemma"}))

    assert models_streamed == ["qwen3.5:4b", "qwen3.5:4b"]
    assert switch_results == [False]  # 延後生效
    assert agent.profile.name == "gemma4:e4b"
    assert agent.history[-1] == {"role": "assistant", "content": "換好了"}
