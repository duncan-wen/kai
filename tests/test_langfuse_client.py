"""langfuse_client 的 no-op fallback 與 helper 單元測試。"""
from core import langfuse_client as lf
from core.tools.dispatch import _mask_args
from core.backends import _usage_details


def _reset():
    lf._checked = False
    lf._client = None


def test_no_creds_returns_none(monkeypatch):
    monkeypatch.setattr("core.config.LANGFUSE_PUBLIC_KEY", None)
    monkeypatch.setattr("core.config.LANGFUSE_SECRET_KEY", None)
    _reset()
    assert lf.get_langfuse() is None


def test_context_managers_are_noop(monkeypatch):
    monkeypatch.setattr("core.config.LANGFUSE_PUBLIC_KEY", None)
    monkeypatch.setattr("core.config.LANGFUSE_SECRET_KEY", None)
    _reset()
    with lf.trace(name="x", session_id="s") as s:
        assert s is None
        with lf.generation(name="g", model="m") as g:
            assert g is None
            with lf.tool(name="t") as t:
                assert t is None
            with lf.agent(name="agent:子代理") as a:
                assert a is None


def test_trim_messages_excludes_system_and_truncates():
    msgs = [
        {"role": "system", "content": "SECRET"},
        {"role": "user", "content": "hello " * 100},
        {"role": "assistant", "content": "hi"},
    ]
    out = lf.trim_messages(msgs, max_chars=10)
    assert out == [
        {"role": "user", "content": "hello hell"},
        {"role": "assistant", "content": "hi"},
    ]


def test_trim_messages_handles_multimodal():
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "看圖"}, {"type": "image_url"}]},
    ]
    out = lf.trim_messages(msgs)
    assert out == [{"role": "user", "content": "看圖"}]


def test_mask_args_hides_secrets():
    masked = _mask_args({"query": "foo", "api_key": "sk-123", "password": "x"})
    assert masked["api_key"] == "***"
    assert masked["password"] == "***"
    assert masked["query"] == "foo"


def test_usage_details_conversion():
    assert _usage_details((None, None)) is None
    assert _usage_details((100, 50)) == {"input": 50, "output": 100, "total": 150}


def test_flush_noop(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    _reset()
    lf.flush()  # 不應 crash


def test_ascii_session_id_keeps_ascii():
    assert lf._ascii_session_id("session-abc") == "session-abc"


def test_ascii_session_id_hashes_non_ascii():
    sid = lf._ascii_session_id("台股分析師")
    assert sid is not None and len(sid) == 16
    # 同輸入同輸出（分組語意穩定）
    assert sid == lf._ascii_session_id("台股分析師")
    assert sid != lf._ascii_session_id("美股分析師")


def test_ascii_session_id_none():
    assert lf._ascii_session_id(None) is None


def test_subagent_dispatch_tools_set():
    from core.tools.dispatch import SUBAGENT_DISPATCH_TOOLS
    assert "send_to_agent" in SUBAGENT_DISPATCH_TOOLS
    assert "bash_exec" not in SUBAGENT_DISPATCH_TOOLS


# ── system prompt 追蹤策略（第一輪完整 / 後續輪佔位符） ──────────────
def test_trim_messages_include_system_truncates():
    msgs = [
        {"role": "system", "content": "S" * 9000},
        {"role": "user", "content": "hi"},
    ]
    out = lf.trim_messages(msgs, include_system=True, system_max_chars=100)
    assert out[0]["role"] == "system"
    assert len(out[0]["content"]) == 100
    assert out[1] == {"role": "user", "content": "hi"}


def test_trim_messages_system_placeholder():
    msgs = [
        {"role": "system", "content": "SECRET"},
        {"role": "user", "content": "hi"},
    ]
    out = lf.trim_messages(msgs, system_placeholder=True)
    assert out[0] == {"role": "system", "content": lf.SYSTEM_PLACEHOLDER_TEXT}
    assert "SECRET" not in str(out)


def test_trim_messages_system_does_not_consume_limit():
    """system 置於 head，不佔用尾部 limit 名額。"""
    msgs = [{"role": "system", "content": "S"}] + [
        {"role": "user", "content": f"m{i}"} for i in range(10)
    ]
    out = lf.trim_messages(msgs, limit=2, include_system=True)
    assert out[0]["role"] == "system"
    assert [m["content"] for m in out[1:]] == ["m8", "m9"]


def test_trim_messages_include_system_wins_over_placeholder():
    msgs = [{"role": "system", "content": "FULL"}, {"role": "user", "content": "hi"}]
    out = lf.trim_messages(msgs, include_system=True, system_placeholder=True)
    assert out[0] == {"role": "system", "content": "FULL"}


def test_system_fingerprint_stable_and_short():
    assert lf.system_fingerprint("abc") == lf.system_fingerprint("abc")
    assert lf.system_fingerprint("abc") != lf.system_fingerprint("abd")
    assert len(lf.system_fingerprint("abc")) == 12


def test_generation_messages_first_turn_then_placeholder(monkeypatch):
    """同 turn 第一次記完整 system，第二次起用佔位符；計數遞增。"""
    from core import backends

    monkeypatch.setattr(backends, "get_langfuse", None, raising=False)
    monkeypatch.setattr("core.langfuse_client.get_langfuse", lambda: object())
    monkeypatch.setattr("core.config.LANGFUSE_TRACE_SYSTEM_PROMPT", True)

    class _A:
        _lf_gen_idx = 0

    a = _A()
    msgs = [{"role": "system", "content": "FULL"}, {"role": "user", "content": "hi"}]
    first = backends._generation_messages(a, msgs)
    second = backends._generation_messages(a, msgs)
    assert first[0] == {"role": "system", "content": "FULL"}
    assert second[0] == {"role": "system", "content": lf.SYSTEM_PLACEHOLDER_TEXT}
    assert a._lf_gen_idx == 2


def test_generation_messages_none_when_disabled(monkeypatch):
    from core import backends

    monkeypatch.setattr("core.langfuse_client.get_langfuse", lambda: None)

    class _A:
        _lf_gen_idx = 0

    assert backends._generation_messages(_A(), [{"role": "user", "content": "x"}]) is None


def test_generation_messages_system_off_by_config(monkeypatch):
    from core import backends

    monkeypatch.setattr("core.langfuse_client.get_langfuse", lambda: object())
    monkeypatch.setattr("core.config.LANGFUSE_TRACE_SYSTEM_PROMPT", False)

    class _A:
        _lf_gen_idx = 0

    out = backends._generation_messages(_A(), [{"role": "system", "content": "FULL"}])
    assert all(m["role"] != "system" for m in out)
