"""Langfuse 可觀測性 client — lazy singleton + no-op fallback。

未設定 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 時，get_langfuse() 回傳 None，
所有埋點 helper 回傳 nullcontext()，零 overhead、零副作用。

使用方式：
    from core.langfuse_client import trace, generation, tool, flush

    with trace(name="chat-turn", session_id=..., input=...) as span:
        span.update(output=...)

    with generation(name="llm-call", model="...", input=...) as gen:
        gen.update(output=..., usage_details=...)
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

from loguru import logger

_client: Any = None
_checked: bool = False


def get_langfuse():
    """lazy 初始化 Langfuse client；未設憑證或初始化失敗回傳 None。

    於第一次呼叫時才 import langfuse（在 config 已 load_dotenv 之後），
    避免「Langfuse import before env vars loaded」的常見錯誤。
    """
    global _client, _checked
    if _checked:
        return _client
    _checked = True
    from . import config as _cfg

    if not (_cfg.LANGFUSE_PUBLIC_KEY and _cfg.LANGFUSE_SECRET_KEY):
        _client = None
        return None
    try:
        from langfuse import get_client as _get_client

        _client = _get_client()
        logger.info("Langfuse 追蹤已啟用")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Langfuse 初始化失敗（追蹤停用）：{e}")
        _client = None
    return _client


def _ascii_session_id(session_id: Optional[str]) -> Optional[str]:
    """session_id 需 US-ASCII ≤200 字元（propagate_attributes 要求）。
    非 ASCII（如中文檔名）轉成 sha1 hex，保留「同一 session 同組」的語意。"""
    if session_id is None:
        return None
    try:
        session_id.encode("ascii")
        return session_id[:200]
    except UnicodeEncodeError:
        import hashlib

        return hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:16]


def flush() -> None:
    """flush 待上傳的 span（app 關閉前呼叫，避免 TUI 退出掉資料）。"""
    if _client is not None:
        try:
            _client.flush()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Langfuse flush 失敗：{e}")


def user_message(text: str) -> list[dict]:
    """Langfuse Sessions（Messages 視圖）要求的對話訊息形狀。

    ChatML 解析器只認「訊息陣列」（List[dict]）；單一 dict 會被當成一般 JSON，
    Messages 視圖因此渲染不出對話泡泡。
    """
    return [{"role": "user", "content": text}]


def assistant_message(text: str) -> list[dict]:
    """Langfuse Sessions（Messages 視圖）要求的對話訊息形狀（訊息陣列）。"""
    return [{"role": "assistant", "content": text}]


@contextmanager
def trace(
    *,
    name: str,
    session_id: Optional[str] = None,
    input: Any = None,
    metadata: Optional[dict] = None,
) -> Iterator[Any]:
    """建立 root span（trace）並傳播 session 屬性。未啟用回傳 nullcontext。

    內部建立的 generation / tool span 會自動嵌套到此 trace 下，
    因此一輪對話（含 tool calling loop 的多次模型呼叫）會被串成單一 trace。

    強制成為「新的 trace 根節點」：進入前先 attach 一個空的 OTel context，
    否則若呼叫端外層已有 span（例如 subagent dispatch），本 span 會被掛成子節點，
    而 Langfuse Sessions 只讀 trace 根節點的 input/output → 對話就渲染不出來。
    """
    langfuse = get_langfuse()
    if langfuse is None:
        yield None
        return
    from langfuse import propagate_attributes

    token = None
    try:  # pragma: no cover - 依賴 opentelemetry 內部行為
        from opentelemetry import context as _ctx

        token = _ctx.attach(_ctx.Context())
    except Exception:  # noqa: BLE001
        token = None
    try:
        with propagate_attributes(session_id=_ascii_session_id(session_id), trace_name=name):
            with langfuse.start_as_current_observation(
                as_type="span", name=name, input=input, metadata=metadata,
            ) as span:
                yield span
    finally:
        if token is not None:  # pragma: no cover
            try:
                from opentelemetry import context as _ctx

                _ctx.detach(token)
            except Exception:  # noqa: BLE001
                pass


@contextmanager
def generation(
    *,
    name: str,
    model: Optional[str] = None,
    input: Any = None,
) -> Iterator[Any]:
    """建立 generation span（一次 LLM 呼叫）。未啟用回傳 nullcontext。"""
    langfuse = get_langfuse()
    if langfuse is None:
        yield None
        return
    with langfuse.start_as_current_observation(
        as_type="generation", name=name, model=model, input=input,
    ) as gen:
        yield gen


@contextmanager
def tool(*, name: str, input: Any = None) -> Iterator[Any]:
    """建立 tool span（一次工具呼叫）。未啟用回傳 nullcontext。"""
    langfuse = get_langfuse()
    if langfuse is None:
        yield None
        return
    with langfuse.start_as_current_observation(
        as_type="tool", name=name, input=input,
    ) as t:
        yield t


@contextmanager
def agent(*, name: str, input: Any = None) -> Iterator[Any]:
    """建立 agent observation（subagent dispatch）。未啟用回傳 nullcontext。

    官方 multi-agent best practice：dispatch 其他 agent 的動作要用
    as_type="agent"（而非 tool/span），讓其在 Langfuse Agent Graph 中
    顯示為獨立子 agent 節點。本專案用於 send_to_agent。
    """
    langfuse = get_langfuse()
    if langfuse is None:
        yield None
        return
    with langfuse.start_as_current_observation(
        as_type="agent", name=name, input=input,
    ) as a:
        yield a


SYSTEM_PLACEHOLDER_TEXT = "[same as turn-1 generation]"


def system_fingerprint(text: str) -> str:
    """system prompt 的短指紋（sha256 前 12 碼）。

    放進 trace metadata，可追蹤 system prompt 的版本變化，
    又不必把可能含專案記憶的內容本體寫進 metadata。
    """
    import hashlib

    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def _flatten_content(content: Any) -> str:
    """多模態 content 取純文字部分。"""
    if isinstance(content, list):
        return " ".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content or "")


MESSAGE_MAX_CHARS = 2000
"""generation input 每則訊息與 generation output 共用的截斷長度。

Session 視圖靠「後一個 generation 的 input 重複前面的訊息」把對話串起來，
所以同一則訊息在 output 與之後的 input 必須截成一模一樣的文字。
"""


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """同時支援 dict 與物件屬性（ollama ToolCall / SimpleNamespace）。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def tool_call_dict(tc: Any, index: int = 0) -> dict:
    """把各後端的 tool call 正規化成 OpenAI 形狀，供 Langfuse ChatML 解析。

    Ollama 的 tool call 沒有 id：以「名稱 + 參數 + 在該則訊息中的位置」雜湊出
    決定性的 id，讓 generation output 與下一輪 input 裡的同一個呼叫拿到相同 id，
    tool 結果也才對得上。
    """
    import hashlib
    import json

    fn = _get(tc, "function") or {}
    name = str(_get(fn, "name", "") or "")
    args = _get(fn, "arguments", "")
    if not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    tc_id = _get(tc, "id")
    if not tc_id:
        digest = hashlib.sha1(f"{index}\x00{name}\x00{args}".encode("utf-8")).hexdigest()[:12]
        tc_id = f"call_{digest}"
    return {
        "id": str(tc_id),
        "type": "function",
        "function": {"name": name, "arguments": args[:MESSAGE_MAX_CHARS]},
    }


def generation_output(content: str, tool_calls: Optional[list] = None) -> list[dict]:
    """generation output 的 ChatML 形狀：單則 assistant 訊息（含 tool_calls）的陣列。

    與 trim_messages 對同一則 assistant 訊息產生的結果一致，Session 視圖才串得起來。
    """
    msg: dict = {"role": "assistant", "content": (content or "")[:MESSAGE_MAX_CHARS]}
    if tool_calls:
        msg["tool_calls"] = [tool_call_dict(tc, i) for i, tc in enumerate(tool_calls)]
    return [msg]


def trim_messages(
    messages: list,
    limit: Optional[int] = None,
    max_chars: int = MESSAGE_MAX_CHARS,
    *,
    include_system: bool = False,
    system_placeholder: bool = False,
    system_max_chars: int = 6000,
) -> list[dict]:
    """把 messages 轉成可安全追蹤的 ChatML 形式。

    預設排除 system prompt（可能含專案記憶等敏感內容）、每則截斷 max_chars。
    不預設砍尾部：messages 已由 history_keep 限縮，而 Session 視圖要求每個
    generation 的 input 完整重複前面的對話，砍掉開頭會讓 tool calling 的
    各輪被拆成互不相連、渲染不出訊息的 thread。

    tool calling 訊息保留結構：assistant 的 tool_calls（含 id）與 tool 結果的
    tool_call_id，讓 Session 視圖能把工具呼叫與結果配對。

    多輪 tool calling loop 的取捨：
      include_system=True     → 開頭放完整 system prompt（截斷 system_max_chars），
                                用於每個 turn 的第一次模型呼叫
      system_placeholder=True → 開頭放佔位符「同本 turn 第一次呼叫」，
                                用於同 turn 的第 2～N 輪，避免重複整份 prompt
    """
    head: list[dict] = []
    if include_system or system_placeholder:
        sys_text = next(
            (
                m.get("content")
                for m in messages
                if isinstance(m, dict) and m.get("role") == "system"
            ),
            None,
        )
        if include_system:
            text = _flatten_content(sys_text)
            if text:
                head.append({"role": "system", "content": text[:system_max_chars]})
        else:
            head.append({"role": "system", "content": SYSTEM_PLACEHOLDER_TEXT})

    body: list[dict] = []
    pending_ids: list[str] = []  # Ollama 的 tool 結果沒有 tool_call_id，依序對應前一則的呼叫
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "system":
            continue
        text = _flatten_content(m.get("content"))[:max_chars]
        if role == "assistant" and m.get("tool_calls"):
            calls = [tool_call_dict(tc, i) for i, tc in enumerate(m["tool_calls"])]
            pending_ids = [c["id"] for c in calls]
            body.append({"role": "assistant", "content": text, "tool_calls": calls})
            continue
        if role == "tool":
            tc_id = m.get("tool_call_id") or (pending_ids.pop(0) if pending_ids else None)
            tool_msg: dict = {"role": "tool", "content": text}
            if tc_id:
                tool_msg["tool_call_id"] = tc_id
            if m.get("name"):
                tool_msg["name"] = m["name"]
            body.append(tool_msg)
            continue
        if text:
            body.append({"role": role, "content": text})
    if limit is not None:
        body = body[-limit:]
    return head + body
