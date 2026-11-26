"""core.backends — adapter 的純函式：中立訊息 → wire、OpenAI tool_call 分段累積。"""

from types import SimpleNamespace

from core.backends import ToolCallAccumulator, to_ollama_messages, to_openai_messages
from core.model_stream import ToolCall

TC = ToolCall(id="c0", name="echo", arguments='{"text": "hi"}')
NEUTRAL = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "q"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [TC],
        "reasoning_content": "想",
    },
    {"role": "tool", "tool_call_id": "c0", "name": "echo", "content": "hi"},
]


def test_to_ollama_messages():
    out = to_ollama_messages(NEUTRAL)
    assert out[2] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "echo", "arguments": {"text": "hi"}}}],
    }
    assert out[3] == {"role": "tool", "content": "hi", "name": "echo"}


def test_to_openai_messages_keeps_reasoning_only_when_asked():
    out = to_openai_messages(NEUTRAL, keeps_reasoning=True)
    assert out[2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "c0",
                "type": "function",
                "function": {"name": "echo", "arguments": '{"text": "hi"}'},
            }
        ],
        "reasoning_content": "想",
    }
    assert out[3] == {"role": "tool", "tool_call_id": "c0", "content": "hi"}
    plain = to_openai_messages(NEUTRAL, keeps_reasoning=False)
    assert "reasoning_content" not in plain[2]


def test_to_openai_messages_encodes_images(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG")
    (out,) = to_openai_messages(
        [{"role": "user", "content": "看圖", "images": [str(img)]}],
        keeps_reasoning=False,
    )
    assert out["content"][0] == {"type": "text", "text": "看圖"}
    assert out["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def _chunk(index, id=None, name=None, args=None):
    return SimpleNamespace(
        index=index, id=id, function=SimpleNamespace(name=name, arguments=args)
    )


def test_tool_call_accumulator_joins_fragments():
    acc = ToolCallAccumulator()
    acc.add(_chunk(0, id="x", name="web_", args='{"q":'))
    acc.add(_chunk(1, name="ping", args=""))
    acc.add(_chunk(0, name="search", args=' "a"}'))
    assert acc.calls() == [
        ToolCall(id="x", name="web_search", arguments='{"q": "a"}'),
        ToolCall(id="call_1", name="ping", arguments=""),
    ]
