"""langfuse_client 的 no-op fallback 與 helper 單元測試。"""
from core import langfuse_client as lf
from core.tools.dispatch import _mask_args
from core.backends import _usage_details


def _reset():
    lf._checked = False
    lf._client = None


def test_no_creds_returns_none(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    _reset()
    assert lf.get_langfuse() is None


def test_context_managers_are_noop(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    _reset()
    with lf.trace(name="x", session_id="s") as s:
        assert s is None
        with lf.generation(name="g", model="m") as g:
            assert g is None
            with lf.tool(name="t") as t:
                assert t is None


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
