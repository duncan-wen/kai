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
        {
            "role": "user",
            "content": [{"type": "text", "text": "看圖"}, {"type": "image_url"}],
        },
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


def test_generation_messages_first_turn_then_no_system(monkeypatch):
    """同 turn 第一次記完整 system，第二次起不放 system（佔位符會在 Session 視圖中途冒出來）；計數遞增。"""
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
    assert second == [{"role": "user", "content": "hi"}]
    assert a._lf_gen_idx == 2


def test_generation_messages_none_when_disabled(monkeypatch):
    from core import backends

    monkeypatch.setattr("core.langfuse_client.get_langfuse", lambda: None)

    class _A:
        _lf_gen_idx = 0

    assert (
        backends._generation_messages(_A(), [{"role": "user", "content": "x"}]) is None
    )


def test_generation_messages_system_off_by_config(monkeypatch):
    from core import backends

    monkeypatch.setattr("core.langfuse_client.get_langfuse", lambda: object())
    monkeypatch.setattr("core.config.LANGFUSE_TRACE_SYSTEM_PROMPT", False)

    class _A:
        _lf_gen_idx = 0

    out = backends._generation_messages(_A(), [{"role": "system", "content": "FULL"}])
    assert all(m["role"] != "system" for m in out)


def test_message_helpers_use_role_content_shape():
    """Sessions（Messages 視圖）只認 {"role","content"}；用 {"user"}/{"reply"} 會渲染不出來。"""
    assert lf.user_message("hi") == [{"role": "user", "content": "hi"}]
    assert lf.assistant_message("yo") == [{"role": "assistant", "content": "yo"}]


def test_message_helpers_return_list_not_dict():
    """ChatML 解析器吃「訊息陣列」；單一 dict 會被當一般 JSON 而渲染不出對話。"""
    for msg in (lf.user_message("hi"), lf.assistant_message("yo")):
        assert isinstance(msg, list), f"應為 list，實際 {type(msg).__name__}"
        assert len(msg) == 1
        assert set(msg[0]) == {"role", "content"}


def test_trace_passes_message_input_to_sdk(monkeypatch):
    """trace() 需把 input 原樣交給 start_as_current_observation（掛在 trace 根節點）。"""
    import contextlib
    import langfuse

    captured = {}

    class _Obs:
        def __enter__(self):
            return object()

        def __exit__(self, *a):
            return False

    class _Fake:
        def start_as_current_observation(self, **kw):
            captured.update(kw)
            return _Obs()

    monkeypatch.setattr(lf, "get_langfuse", lambda: _Fake())
    monkeypatch.setattr(
        langfuse, "propagate_attributes", lambda **kw: contextlib.nullcontext()
    )

    with lf.trace(
        name="chat-turn", session_id="history-1", input=lf.user_message("hi")
    ) as span:
        assert span is not None
    assert captured["name"] == "chat-turn"
    assert captured["input"] == [{"role": "user", "content": "hi"}]
    assert captured["as_type"] == "span"


def test_turn_reply_text_ignores_previous_turns():
    """只取本輪 user 訊息之後的 assistant 訊息，避免舊回覆被當成本輪輸出。"""
    from core.agent import Agent

    user = {"role": "user", "content": "新問題"}

    class _Stub:
        history = [
            {"role": "user", "content": "舊問題"},
            {"role": "assistant", "content": "舊回覆"},
            user,
            {"role": "tool", "content": "工具結果"},
            {"role": "assistant", "content": "新回覆"},
        ]

    assert Agent._turn_reply_text(_Stub(), user) == "新回覆"

    class _Interrupted:
        history = [
            {"role": "assistant", "content": "舊回覆"},
            user,
            {"role": "tool", "content": "工具結果"},
        ]

    # 本輪沒有 assistant（工具迴圈被中斷）→ 空字串，而不是回傳上一輪舊回覆
    assert Agent._turn_reply_text(_Interrupted(), user) == ""


def test_turn_reply_text_survives_history_rotation():
    """history 滿了被 _rotate_history 截掉開頭（長度不變）時，仍取得本輪回覆。"""
    from core.agent import Agent

    class _A:
        history_keep = 2
        history = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]

    a = _A()
    before = len(a.history)
    user = {"role": "user", "content": "q3"}
    a.history.append(user)
    a.history.append({"role": "assistant", "content": "a3"})
    Agent._rotate_history(a)
    assert len(a.history) == before  # 舊做法以長度當索引會落空
    assert Agent._turn_reply_text(a, user) == "a3"


def test_turn_reply_text_empty_when_user_msg_removed():
    """錯誤 / 取消時 user 訊息被 pop → 空字串，不誤抓舊回覆。"""
    from core.agent import Agent

    class _Stub:
        history = [{"role": "assistant", "content": "舊回覆"}]

    assert Agent._turn_reply_text(_Stub(), {"role": "user", "content": "x"}) == ""


def test_trim_messages_keeps_tool_call_structure_openai():
    """OpenAI 形狀的 tool calling 訊息保留 tool_calls 與 tool_call_id（Session 視圖配對用）。"""
    msgs = [
        {"role": "user", "content": "查天氣"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "w", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "晴"},
    ]
    out = lf.trim_messages(msgs)
    assert out[1] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "w", "arguments": "{}"},
            }
        ],
    }
    assert out[2] == {"role": "tool", "content": "晴", "tool_call_id": "c1"}


def test_trim_messages_ollama_tool_calls_get_matching_ids():
    """Ollama tool call 無 id：合成的 id 要與 generation_output 一致，tool 結果依序配對。"""
    from types import SimpleNamespace

    calls = [
        SimpleNamespace(function=SimpleNamespace(name="a", arguments={"x": 1})),
        SimpleNamespace(function=SimpleNamespace(name="b", arguments={})),
    ]
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": calls},
        {"role": "tool", "content": "ra", "name": "a"},
        {"role": "tool", "content": "rb", "name": "b"},
    ]
    out = lf.trim_messages(msgs)
    produced = lf.generation_output("", calls)[0]["tool_calls"]
    assert out[1]["tool_calls"] == produced
    assert out[2]["tool_call_id"] == produced[0]["id"]
    assert out[3]["tool_call_id"] == produced[1]["id"]
    assert produced[0]["id"] != produced[1]["id"]


def test_trim_messages_no_tail_cut_by_default():
    """預設不砍尾部：後續 generation 必須完整重複前面的對話，Session 視圖才串得起來。"""
    msgs = [{"role": "user", "content": str(i)} for i in range(20)]
    assert len(lf.trim_messages(msgs)) == 20


def test_generation_output_is_chatml_array():
    out = lf.generation_output("x" * (lf.MESSAGE_MAX_CHARS + 10))
    assert isinstance(out, list)
    assert out[0]["role"] == "assistant"
    assert len(out[0]["content"]) == lf.MESSAGE_MAX_CHARS
    assert "tool_calls" not in out[0]


# ── 稽核修正（fix/langfuse-audit） ─────────────────────────────────


def test_usage_details_splits_cache_hit_into_exclusive_bucket():
    """cache 命中要從 input 扣掉放進 input_cached_tokens（Langfuse bucket 互斥），total 不變。"""
    assert _usage_details((100, 1000), cache_hit=900) == {
        "input": 100,
        "input_cached_tokens": 900,
        "output": 100,
        "total": 1100,
    }


def test_usage_details_cache_hit_zero_or_none_keeps_plain_input():
    assert _usage_details((10, 50), cache_hit=0) == {
        "input": 50,
        "output": 10,
        "total": 60,
    }
    assert _usage_details((10, 50), cache_hit=None) == {
        "input": 50,
        "output": 10,
        "total": 60,
    }


def test_usage_details_cache_hit_clamped_to_input():
    out = _usage_details((10, 50), cache_hit=80)
    assert out["input"] == 0 and out["input_cached_tokens"] == 50


def test_mask_secrets_known_env_value(monkeypatch):
    monkeypatch.setenv("MY_SERVICE_API_KEY", "abcd1234efgh5678")
    out = lf.mask_secrets(data={"result": "key is abcd1234efgh5678 ok"})
    assert out == {"result": f"key is {lf.MASK} ok"}


def test_mask_secrets_token_patterns():
    text = (
        "pk-lf-5e4cdd2f-fbe2-4961 sk-abcdefghijklmnop1234 "
        "AIzaSyA1234567890abcdefghijklmnopqrstu Bearer abcdefghijklmnopqrstuv"
    )
    out = lf.mask_secrets(data=text)
    assert "pk-lf-5e4c" not in out
    assert "sk-abcdef" not in out
    assert "AIzaSy" not in out
    assert "abcdefghijklmnopqrstuv" not in out


def test_mask_secrets_env_file_assignment_keeps_name():
    out = lf.mask_secrets(data="GEMINI_API_KEY=xyz-secret-value\nMODEL=qwen3")
    assert out == f"GEMINI_API_KEY={lf.MASK}\nMODEL=qwen3"


def test_mask_secrets_json_assignment():
    out = lf.mask_secrets(data='{"api_key": "hunter22", "name": "x"}')
    assert "hunter22" not in out and '"name": "x"' in out


def test_mask_secrets_leaves_token_counts_alone():
    """usage / 設定裡的 token 數字不是密鑰，不該被遮。"""
    text = "input_tokens: 16918\nMAX_TOKENS=4096\nmax_tokens: 2048"
    assert lf.mask_secrets(data=text) == text


def test_mask_secrets_keeps_chatml_structure():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash_exec", "arguments": "{}"},
                },
            ],
        }
    ]
    assert lf.mask_secrets(data=msgs) == msgs


def test_record_result_marks_errors():
    from core.tools.dispatch import _record_result
    from core.tools.result import ToolResult

    class _Obs:
        def __init__(self):
            self.kw = None

        def update(self, **kw):
            self.kw = kw

    ok, bad = _Obs(), _Obs()
    _record_result(ok, ToolResult("t", "fine"))
    _record_result(bad, ToolResult("t", "[Error] boom", is_error=True))
    assert "level" not in ok.kw
    assert bad.kw["level"] == "ERROR"
    assert bad.kw["status_message"] == "[Error] boom"
    _record_result(None, ToolResult("t", "x"))  # 未啟用時不炸


def test_generation_name_is_backend_agnostic():
    assert lf.GENERATION_NAME == "generate-response"


def test_mask_secrets_learns_values_from_assignments(monkeypatch):
    """賦值裡認出的值，之後單獨出現（例如模型 thinking 引用）也要遮。"""
    monkeypatch.setattr(lf, "_learned_secrets", set())
    lf.mask_secrets(data="用 bash 執行 echo DEMO_API_KEY=abc12345secret")
    out = lf.mask_secrets(
        data={"thinking": '"abc12345secret" looks like a placeholder'}
    )
    assert "abc12345secret" not in out["thinking"]


def test_mask_secrets_does_not_learn_short_values(monkeypatch):
    monkeypatch.setattr(lf, "_learned_secrets", set())
    lf.mask_secrets(data="password: abcd")
    assert lf.mask_secrets(data="abcd efgh") == "abcd efgh"


def test_compact_fallback_models_are_registered():
    """/compact 的預設模型必須是 REGISTRY 內的現行名稱，避免改名後寫死舊名。"""
    from core.agent import COMPACT_FALLBACK_DEEPSEEK, COMPACT_FALLBACK_GEMINI
    from core.models import REGISTRY

    assert REGISTRY[COMPACT_FALLBACK_DEEPSEEK].backend == "deepseek"
    assert REGISTRY[COMPACT_FALLBACK_GEMINI].backend == "gemini"
