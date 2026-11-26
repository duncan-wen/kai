"""模型後端 adapter：Ollama 與 OpenAI 相容 API（Gemini / DeepSeek）。

兩個 adapter 都實作 `core.model_stream.ModelStream`：吃中立訊息格式，
負責轉成各自的 wire 格式、串流、把結果正規化成 `AssistantStep`。
SDK client 在第一次使用時建立，每個 backend 在 module 層級只快取一份。
Langfuse 的 generation 記錄也留在 adapter 裡（只有這裡看得到 wire 訊息）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from . import config as _cfg
from .langfuse_client import (
    GENERATION_NAME,
    generation,
    generation_output,
    trim_messages,
)
from .model_stream import (
    AssistantStep,
    CancelToken,
    Delta,
    OnDelta,
    QuotaError,
    ToolCall,
)
from .tools import clean_thinking

if TYPE_CHECKING:
    import ollama
    from openai import AsyncOpenAI

    from .models import ModelProfile


# ── SDK client（lazy，module 層級快取）─────────────────────────────────────────

_OPENAI_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "deepseek": "https://api.deepseek.com",
}
_clients: dict[str, Any] = {}


def ollama_client() -> "ollama.AsyncClient":
    if "ollama" not in _clients:
        import ollama

        _clients["ollama"] = ollama.AsyncClient()
    return _clients["ollama"]


def _api_key(backend: str) -> str | None:
    return {"gemini": _cfg.GEMINI_API_KEY, "deepseek": _cfg.DEEPSEEK_API_KEY}.get(
        backend
    )


def openai_client(backend: str) -> "AsyncOpenAI | None":
    """Gemini / DeepSeek 的 AsyncOpenAI；沒有 API key 回 None。"""
    if backend not in _clients:
        key = _api_key(backend)
        if not key:
            return None
        from openai import AsyncOpenAI

        _clients[backend] = AsyncOpenAI(
            api_key=key, base_url=_OPENAI_BASE_URLS[backend]
        )
    return _clients[backend]


# ── 中立訊息 → wire ────────────────────────────────────────────────────────────


def _args_str(arguments: str | dict) -> str:
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, ensure_ascii=False)


def _args_dict(arguments: str | dict) -> dict:
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def to_ollama_messages(messages: list[dict]) -> list[dict]:
    """中立訊息 → Ollama：tool 結果用 `name` 對應，不帶 reasoning_content。"""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant":
            w: dict = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                w["tool_calls"] = [
                    {
                        "function": {
                            "name": tc.name,
                            "arguments": _args_dict(tc.arguments),
                        }
                    }
                    for tc in m["tool_calls"]
                ]
            out.append(w)
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "content": m.get("content", ""),
                    "name": m.get("name", ""),
                }
            )
        else:
            w = {"role": role, "content": m.get("content", "")}
            if m.get("images"):
                w["images"] = m["images"]
            out.append(w)
    return out


def _image_content(text: str, images: list[str]) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": text}]
    for img_path in images:
        mime = mimetypes.guess_type(img_path)[0] or "image/png"
        b64 = base64.b64encode(Path(img_path).read_bytes()).decode()
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        )
    return content


def to_openai_messages(messages: list[dict], *, keeps_reasoning: bool) -> list[dict]:
    """中立訊息 → OpenAI 相容：tool 結果用 `tool_call_id` 對應，圖片轉 base64 content list。"""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant":
            calls = m.get("tool_calls") or []
            w: dict = {
                "role": "assistant",
                "content": m.get("content") or (None if calls else ""),
            }
            if calls:
                w["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": _args_str(tc.arguments),
                        },
                    }
                    for tc in calls
                ]
            if keeps_reasoning and m.get("reasoning_content"):
                w["reasoning_content"] = m["reasoning_content"]
            out.append(w)
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "content": m.get("content", ""),
                }
            )
        elif m.get("images"):
            out.append(
                {
                    "role": role,
                    "content": _image_content(m.get("content", ""), m["images"]),
                }
            )
        else:
            out.append({"role": role, "content": m.get("content", "")})
    return out


class ToolCallAccumulator:
    """OpenAI 串流的 tool_call 是分段送的（依 index 累加 name / arguments）。"""

    def __init__(self) -> None:
        self._buf: dict[int, dict] = {}

    def add(self, tc_chunk: Any) -> None:
        d = self._buf.setdefault(tc_chunk.index, {"id": "", "name": "", "args": ""})
        if tc_chunk.id:
            d["id"] = tc_chunk.id
        if tc_chunk.function:
            d["name"] += tc_chunk.function.name or ""
            d["args"] += tc_chunk.function.arguments or ""

    def calls(self) -> list[ToolCall]:
        return [
            ToolCall(id=d["id"] or f"call_{i}", name=d["name"], arguments=d["args"])
            for i, d in sorted(self._buf.items())
        ]


# ── Langfuse helpers ──────────────────────────────────────────────────────────


def _usage_details(usage, cache_hit: "int | None" = None) -> "dict | None":
    """把 (output, input) tokens 轉成 Langfuse usage_details。

    Langfuse 的 usage key 是互斥的 bucket：供應商回報的 prompt tokens 含 cache 命中，
    必須拆成 input（未命中）+ input_cached_tokens（命中），否則命中部分會以
    一般輸入價計費，成本被高估。
    """
    if not usage or usage[0] is None:
        return None
    out = usage[0] or 0
    inp = usage[1] or 0
    details = {"input": inp, "output": out, "total": out + inp}
    if cache_hit:
        hit = min(int(cache_hit), inp)
        details["input"] = inp - hit
        details["input_cached_tokens"] = hit
    return details


def _generation_metadata(backend: str, thinking: str) -> dict:
    """generation metadata 一次給齊（SDK update 是整批覆蓋）。"""
    meta: dict = {"backend": backend}
    if thinking:
        meta["thinking"] = thinking[:2000]
    return meta


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _generation_messages(messages: list) -> "list | None":
    """決定 trace 的 messages 精簡策略。

    turn 的第一個 step（最後一則是 user 訊息）記完整 system prompt（可還原當下動態上下文），
    後續 tool calling step（最後一則是 tool 結果）完全不放 system，避免 log 隨輪數膨脹。
    不用佔位符：內容與第一次不同的 system 訊息會被 Session 視圖當成
    對話中途插入的新 system prompt 顯示出來。
    Langfuse 未啟用時直接回 None，省下 trim 的成本。
    """
    from .langfuse_client import get_langfuse

    if get_langfuse() is None:
        return None
    first = not messages or messages[-1].get("role") != "tool"
    return trim_messages(
        messages,
        include_system=first and getattr(_cfg, "LANGFUSE_TRACE_SYSTEM_PROMPT", True),
    )


# ── Adapters ──────────────────────────────────────────────────────────────────

_XML_RETRY_MAX = 2


class OllamaStream:
    """Ollama 串流，含 XML 解析錯誤自動重試（對外只送 `Delta("reset")`）。"""

    keeps_reasoning = False

    def __init__(self, profile: "ModelProfile") -> None:
        self.profile = profile

    async def step(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        think: bool,
        on_delta: OnDelta,
        cancel: CancelToken,
    ) -> AssistantStep:
        import ollama

        p = self.profile
        wire = to_ollama_messages(messages)
        options = {
            "num_ctx": p.num_ctx,
            "temperature": _cfg.TEMPERATURE,
            "top_k": _cfg.TOP_K,
            "top_p": _cfg.TOP_P,
        }
        step = AssistantStep(num_ctx=p.num_ctx)
        text: list[str] = []
        thinking: list[str] = []
        raw_calls: list = []
        first_token_at: "datetime | None" = None
        with generation(
            name=GENERATION_NAME,
            model=p.name,
            input=_generation_messages(wire),
            model_parameters={**options, "think": think},
            metadata={"backend": p.backend},
        ) as gen:
            try:
                xml_retry = 0
                while True:
                    stream = await ollama_client().chat(
                        model=p.name,
                        messages=wire,
                        think=think,
                        tools=tools,
                        stream=True,
                        options=options,
                    )
                    try:
                        it = stream.__aiter__()
                        while True:
                            try:
                                chunk = await asyncio.wait_for(
                                    it.__anext__(), timeout=_cfg.OLLAMA_STREAM_TIMEOUT
                                )
                            except StopAsyncIteration:
                                break
                            except asyncio.TimeoutError:
                                raise TimeoutError(
                                    f"Ollama 超過 {_cfg.OLLAMA_STREAM_TIMEOUT}s 未回應，可能已卡住"
                                )
                            if cancel.cancelled:
                                break
                            msg = chunk.message
                            if getattr(chunk, "eval_count", None) is not None:
                                step.gen_tokens = chunk.eval_count
                            if getattr(chunk, "prompt_eval_count", None) is not None:
                                step.prompt_tokens = chunk.prompt_eval_count
                            if first_token_at is None and (
                                msg.thinking or msg.content or msg.tool_calls
                            ):
                                first_token_at = _now()
                            if msg.thinking:
                                t = clean_thinking(msg.thinking)
                                thinking.append(t)
                                await on_delta(Delta("thinking", t))
                            if msg.content:
                                text.append(msg.content)
                                await on_delta(Delta("text", msg.content))
                            raw_calls.extend(msg.tool_calls or [])
                        break
                    except ollama.ResponseError as e:
                        if "XML syntax error" in str(e) and xml_retry < _XML_RETRY_MAX:
                            xml_retry += 1
                            logger.warning(
                                f"XML 解析錯誤，第 {xml_retry}/{_XML_RETRY_MAX} 次重試：{e}"
                            )
                            text.clear()
                            thinking.clear()
                            raw_calls.clear()
                            await on_delta(Delta("reset"))
                            continue
                        raise
            finally:
                if gen is not None:
                    # ChatML 訊息陣列（含 tool_calls），Session 視圖才能把這一步串進對話
                    gen.update(
                        output=generation_output("".join(text), raw_calls),
                        metadata=_generation_metadata(p.backend, "".join(thinking)),
                        usage_details=_usage_details(
                            (step.gen_tokens, step.prompt_tokens)
                        ),
                        completion_start_time=first_token_at,
                    )
        step.text = "".join(text)
        step.thinking = "".join(thinking)
        step.tool_calls = [
            ToolCall(
                id=f"call_{i}",
                name=tc.function.name,
                arguments=tc.function.arguments,
            )
            for i, tc in enumerate(raw_calls)
        ]
        return step


class OpenAICompatStream:
    """OpenAI 相容串流（Gemini / DeepSeek）。"""

    def __init__(self, profile: "ModelProfile") -> None:
        self.profile = profile
        # DeepSeek thinking 模式要求 assistant 訊息帶回 reasoning_content
        self.keeps_reasoning = profile.backend == "deepseek"

    async def step(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        think: bool,
        on_delta: OnDelta,
        cancel: CancelToken,
    ) -> AssistantStep:
        from openai import NOT_GIVEN, RateLimitError

        p = self.profile
        client = openai_client(p.backend)
        if client is None:
            raise RuntimeError(f"未設定 {p.backend.upper()}_API_KEY，無法使用 {p.name}")
        is_deepseek = p.backend == "deepseek"
        wire = to_openai_messages(messages, keeps_reasoning=self.keeps_reasoning)
        step = AssistantStep()
        text: list[str] = []
        thinking: list[str] = []
        acc = ToolCallAccumulator()
        first_token_at: "datetime | None" = None
        with generation(
            name=GENERATION_NAME,
            model=p.name,
            input=_generation_messages(wire),
            model_parameters={
                "temperature": _cfg.TEMPERATURE,
                "top_p": _cfg.TOP_P,
                "thinking": bool(is_deepseek and think),
            },
            metadata={"backend": p.backend},
        ) as gen:
            try:
                extra: dict = {}
                if is_deepseek and think:
                    extra["extra_body"] = {"thinking": {"type": "enabled"}}
                    extra["reasoning_effort"] = "high"
                elif is_deepseek:
                    extra["extra_body"] = {"thinking": {"type": "disabled"}}
                try:
                    stream = await client.chat.completions.create(
                        model=p.name,
                        messages=wire,
                        tools=tools or NOT_GIVEN,
                        temperature=_cfg.TEMPERATURE,
                        top_p=_cfg.TOP_P,
                        stream=True,
                        stream_options={"include_usage": True},
                        **extra,
                    )
                except RateLimitError as e:
                    logger.warning(f"API 配額不足（{p.backend}）：{e}")
                    raise QuotaError(f"API 配額已用盡，請稍後再試。\n{e}") from e

                async for chunk in stream:
                    if cancel.cancelled:
                        break
                    if chunk.usage:
                        step.gen_tokens = chunk.usage.completion_tokens
                        step.prompt_tokens = chunk.usage.prompt_tokens
                        hit = _cache_hit(chunk.usage)
                        if hit is not None:
                            step.cache_hit = hit
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    reasoning = getattr(delta, "reasoning_content", None) or (
                        delta.model_extra.get("reasoning_content")
                        if getattr(delta, "model_extra", None)
                        else None
                    )
                    # 首個實質輸出（思考 / 文字 / 工具呼叫）才算首 token；第一個 chunk 常只帶 role
                    if first_token_at is None and (
                        reasoning or delta.content or delta.tool_calls
                    ):
                        first_token_at = _now()
                    if reasoning:
                        thinking.append(reasoning)
                        await on_delta(Delta("thinking", reasoning))
                    if delta.content:
                        text.append(delta.content)
                        await on_delta(Delta("text", delta.content))
                    for tc_chunk in delta.tool_calls or []:
                        acc.add(tc_chunk)
            finally:
                step.text = "".join(text)
                step.thinking = "".join(thinking)
                step.tool_calls = acc.calls()
                if gen is not None:
                    wire_calls = to_openai_messages(
                        [{"role": "assistant", "tool_calls": step.tool_calls}],
                        keeps_reasoning=False,
                    )[0].get("tool_calls")
                    gen.update(
                        output=generation_output(step.text, wire_calls),
                        metadata=_generation_metadata(p.backend, step.thinking),
                        usage_details=_usage_details(
                            (step.gen_tokens, step.prompt_tokens), step.cache_hit
                        ),
                        completion_start_time=first_token_at,
                    )
        return step


def _cache_hit(usage: Any) -> int | None:
    """DeepSeek 用 prompt_cache_hit_tokens；OpenAI / Gemini 用 prompt_tokens_details.cached_tokens。"""
    hit = getattr(usage, "prompt_cache_hit_tokens", None)
    if hit is None and getattr(usage, "model_extra", None):
        hit = usage.model_extra.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = getattr(
            getattr(usage, "prompt_tokens_details", None), "cached_tokens", None
        )
    return hit
