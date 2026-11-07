"""AgentRegistry：main agent 與 agent tab 的唯一 owner（tab id、作用中 Agent、查找、MCP 分發）。"""
import pytest

from core.agent import Agent
from core.agent_factory import AgentDefinition
from core.agent_registry import AgentRegistry


@pytest.fixture
def make_agent(tmp_path):
    def _make(name: str | None = None, nickname: str | None = None, mcp_servers: dict | None = None) -> Agent:
        adir = tmp_path / (name or "main")
        adir.mkdir(exist_ok=True)
        agent = Agent(model="qwen3.5:4b", history_file=adir / "history.json", agent_dir=adir if name else None)
        if name:
            agent.apply_definition(AgentDefinition(
                name=name, nickname=nickname, model="qwen3.5:4b", system_prompt="你是助手。", mcp_servers=mcp_servers,
            ))
        return agent

    return _make


@pytest.fixture
def registry(make_agent):
    return AgentRegistry(make_agent())


def test_tab_ids_are_never_reused(registry, make_agent):
    first = registry.add(make_agent("writer"))
    second = registry.add(make_agent("coder"))
    registry.remove(first)

    third = registry.add(make_agent("reviewer"))

    assert len({first, second, third}) == 3
    assert [tid for tid, _ in registry.tabs()] == [second, third]


def test_main_agent_cannot_be_removed(registry):
    with pytest.raises(ValueError):
        registry.remove(AgentRegistry.MAIN_ID)


def test_removing_the_active_tab_falls_back_to_main(registry, make_agent):
    tid = registry.add(make_agent("writer"))
    registry.activate(tid)

    registry.remove(tid)

    assert registry.active is registry.main
    assert registry.active_id == AgentRegistry.MAIN_ID


def test_activating_a_non_agent_tab_keeps_the_active_agent(registry, make_agent):
    tid = registry.add(make_agent("writer"))
    registry.activate(tid)

    assert registry.activate("file-3") is False

    assert registry.active_id == tid


def test_agent_tab_can_be_found_by_object_and_by_agent_dir(registry, make_agent):
    writer = make_agent("writer")
    tid = registry.add(writer)

    assert registry.tab_id_of(writer) == tid
    assert registry.tab_id_of(registry.main) == AgentRegistry.MAIN_ID
    assert registry.find_by_dir(writer._agent_dir) == tid
    assert registry.find_by_dir(writer._agent_dir.parent / "nobody") is None


@pytest.fixture
def team(registry, make_agent):
    ids = {
        "writer": registry.add(make_agent("writer", nickname="小寫")),
        "writer-zh": registry.add(make_agent("writer-zh")),
        "reviewer": registry.add(make_agent("reviewer", nickname="Rita")),
    }
    return registry, ids


@pytest.mark.parametrize("query", ["main", "chat", "MAIN"])
def test_find_main_by_alias(team, query):
    registry, _ = team
    assert registry.find(query) == AgentRegistry.MAIN_ID


def test_find_main_by_display_name(team):
    registry, _ = team
    assert registry.find(registry.main.display_name.upper()) == AgentRegistry.MAIN_ID


@pytest.mark.parametrize(
    "query, expected",
    [
        ("writer", "writer"),       # 完全相同優先，即使 writer-zh 也以它開頭
        ("小寫", "writer"),          # nickname
        ("RITA", "reviewer"),       # 不分大小寫
        ("rev", "reviewer"),        # 唯一前綴
        ("writer-", "writer-zh"),
    ],
)
def test_find_agent_tab(team, query, expected):
    registry, ids = team
    assert registry.find(query) == ids[expected]


def test_find_ambiguous_prefix_lists_candidates(team):
    registry, _ = team
    with pytest.raises(LookupError) as exc:
        registry.find("wri")
    assert "writer" in str(exc.value) and "writer-zh" in str(exc.value)


def test_find_unknown_name_fails(team):
    registry, _ = team
    with pytest.raises(LookupError):
        registry.find("designer")


def fake_mcp():
    from types import SimpleNamespace
    return SimpleNamespace(tool_names={"read_file"}, server_summary={"fs": 1}, tools=[])


def test_mcp_goes_to_every_agent_except_those_that_opt_out(registry, make_agent):
    writer = make_agent("writer")
    loner = make_agent("loner", mcp_servers={})
    registry.add(writer)
    registry.add(loner)
    mcp = fake_mcp()

    registry.set_mcp(mcp, "可存取 /tmp")

    assert registry.main.mcp_client is mcp
    assert writer.mcp_client is mcp and writer.mcp_context == "可存取 /tmp"
    assert loner.mcp_client is None


def test_agent_opened_after_mcp_started_gets_it(registry, make_agent):
    mcp = fake_mcp()
    registry.set_mcp(mcp, "ctx")

    writer = make_agent("writer")
    registry.add(writer)

    assert writer.mcp_client is mcp


def test_stopping_mcp_clears_every_agent(registry, make_agent):
    writer = make_agent("writer")
    registry.add(writer)
    registry.set_mcp(fake_mcp(), "ctx")

    registry.set_mcp(None, "")

    assert registry.main.mcp_client is None and writer.mcp_client is None


def test_reloaded_agent_that_opts_back_in_gets_mcp(registry, make_agent):
    loner = make_agent("loner", mcp_servers={})
    registry.add(loner)
    mcp = fake_mcp()
    registry.set_mcp(mcp, "ctx")

    loner.apply_definition(AgentDefinition(name="loner", model="qwen3.5:4b", system_prompt="你是助手。"))
    registry.attach_mcp(loner)

    assert loner.mcp_client is mcp
