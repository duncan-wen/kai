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
    """
    langfuse = get_langfuse()
    if langfuse is None:
        yield None
        return
    from langfuse import propagate_attributes

    with propagate_attributes(session_id=_ascii_session_id(session_id), trace_name=name):
        with langfuse.start_as_current_observation(
            as_type="span", name=name, input=input, metadata=metadata,
        ) as span:
            yield span


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


def trim_messages(messages: list, limit: int = 6, max_chars: int = 400) -> list[dict]:
    """把 messages 轉成可安全追蹤的精簡形式。

    排除 system prompt（可能含專案記憶等敏感內容）、每則截斷、只留尾部數則，
    避免把完整 history 與敏感上下文灌進 Langfuse。
    """
    out: list[dict] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "system":
            continue
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        text = str(content or "")
        if text:
            out.append({"role": m.get("role"), "content": text[:max_chars]})
    return out[-limit:]
