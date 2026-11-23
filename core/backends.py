"""Streaming backend implementations for Ollama and OpenAI-compatible APIs.

Each function takes an Agent instance as first argument and mutates its
_last_usage / _last_cache_hit / _t_thinking_end fields in-place, the same
way the former Agent methods did.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import ollama
from loguru import logger
from openai import AsyncOpenAI

from . import config as _cfg
from .tools import clean_thinking
from .langfuse_client import (
    GENERATION_NAME,
    generation,
    generation_output,
    trim_messages,
)

if TYPE_CHECKING:
    from .agent import Agent


def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """將 Ollama 格式的 images key 轉換為 OpenAI vision 的 content list 格式。"""
    result = []
    for msg in messages:
        if "images" not in msg:
            result.append(msg)
            continue
        content: list = [{"type": "text", "text": msg.get("content", "")}]
        for img_path in msg["images"]:
            p = Path(img_path)
            mime = mimetypes.guess_type(img_path)[0] or "image/png"
            b64 = base64.b64encode(p.read_bytes()).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                }
            )
        result.append({"role": msg["role"], "content": content})
    return result


def _usage_details(usage, cache_hit: "int | None" = None) -> "dict | None":
    """把 agent._last_usage (output, input) 轉成 Langfuse usage_details。

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


def _generation_metadata(agent: "Agent", thinking_parts: list[str]) -> dict:
    """generation metadata 一次給齊（SDK update 是整批覆蓋）。"""
    meta: dict = {"backend": agent.profile.backend}
    if thinking_parts:
        meta["thinking"] = "".join(thinking_parts)[:2000]
    return meta


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _generation_messages(agent: "Agent", messages: list) -> "list | None":
    """依 turn 內第幾次模型呼叫，決定 trace 的 messages 精簡策略。

    每個 turn 的第一次呼叫記完整 system prompt（可還原當下動態上下文），
    後續 tool calling 輪次完全不放 system，避免 log 隨輪數膨脹。
    不用佔位符：內容與第一次不同的 system 訊息會被 Session 視圖當成
    對話中途插入的新 system prompt 顯示出來。
    Langfuse 未啟用時直接回 None，省下 trim 的成本。
    """
    from .langfuse_client import get_langfuse

    if get_langfuse() is None:
        return None
    first = getattr(agent, "_lf_gen_idx", 0) == 0
    agent._lf_gen_idx = getattr(agent, "_lf_gen_idx", 0) + 1
    return trim_messages(
        messages,
        include_system=first and getattr(_cfg, "LANGFUSE_TRACE_SYSTEM_PROMPT", True),
    )


async def stream_ollama(
    agent: "Agent",
    messages: list,
    active_tools: list,
    reply_parts: list[str],
    tool_calls: list,
    thinking_parts: list[str],
    on_token,
    on_thinking,
    on_retry,
) -> None:
    """Ollama 串流，含 XML 解析錯誤自動重試。"""
    _first_token_at: "datetime | None" = None
    with generation(
        name=GENERATION_NAME,
        model=agent.profile.name,
        input=_generation_messages(agent, messages),
        model_parameters={
            "num_ctx": agent.profile.num_ctx,
            "temperature": _cfg.TEMPERATURE,
            "top_k": _cfg.TOP_K,
            "top_p": _cfg.TOP_P,
            "think": agent.profile.supports_thinking,
        },
        metadata={"backend": agent.profile.backend},
    ) as gen:
        try:
            _xml_retry = 0
            _XML_RETRY_MAX = 2
            while True:
                try:
                    stream = await agent._ollama.chat(
                        model=agent.profile.name,
                        messages=messages,
                        think=agent.profile.supports_thinking,
                        tools=active_tools,
                        stream=True,
                        options={
                            "num_ctx": agent.profile.num_ctx,
                            "temperature": _cfg.TEMPERATURE,
                            "top_k": _cfg.TOP_K,
                            "top_p": _cfg.TOP_P,
                        },
                    )
                except httpx.ConnectError:
                    raise

                try:
                    _it = stream.__aiter__()
                    while True:
                        try:
                            chunk = await asyncio.wait_for(
                                _it.__anext__(), timeout=_cfg.OLLAMA_STREAM_TIMEOUT
                            )
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            raise asyncio.TimeoutError(
                                f"Ollama 超過 {_cfg.OLLAMA_STREAM_TIMEOUT}s 未回應，可能已卡住"
                            )
                        if agent._cancelled:
                            break
                        msg = chunk.message
                        agent._last_usage = (
                            getattr(chunk, "eval_count", None),
                            getattr(chunk, "prompt_eval_count", None),
                        )
                        if _first_token_at is None and (
                            msg.thinking or msg.content or msg.tool_calls
                        ):
                            _first_token_at = _now()

                        if msg.thinking:
                            thinking_parts.append(clean_thinking(msg.thinking))

                        if msg.content:
                            if not reply_parts and thinking_parts:
                                agent._t_thinking_end = time.perf_counter()
                                if on_thinking:
                                    await on_thinking("".join(thinking_parts))
                            reply_parts.append(msg.content)
                            if on_token:
                                await on_token(msg.content)

                        for tc in msg.tool_calls or []:
                            tool_calls.append(tc)
                    return

                except ollama.ResponseError as e:
                    if "XML syntax error" in str(e) and _xml_retry < _XML_RETRY_MAX:
                        _xml_retry += 1
                        logger.warning(
                            f"XML 解析錯誤，第 {_xml_retry}/{_XML_RETRY_MAX} 次重試：{e}"
                        )
                        reply_parts.clear()
                        tool_calls.clear()
                        thinking_parts.clear()
                        if on_retry:
                            await on_retry()
                        continue
                    raise
        finally:
            if gen is not None:
                # ChatML 訊息陣列（含 tool_calls），Session 視圖才能把這一步串進對話
                gen.update(
                    output=generation_output("".join(reply_parts), tool_calls),
                    metadata=_generation_metadata(agent, thinking_parts),
                    usage_details=_usage_details(agent._last_usage),
                    completion_start_time=_first_token_at,
                )


async def stream_openai_compat(
    agent: "Agent",
    client: AsyncOpenAI,
    messages: list,
    active_tools: list,
    reply_parts: list[str],
    tool_calls: list,
    thinking_parts: list[str],
    on_token,
    on_thinking,
) -> None:
    """OpenAI-compatible 串流（Gemini / DeepSeek）。"""
    from openai import NOT_GIVEN, RateLimitError

    _first_token_at: "datetime | None" = None
    is_deepseek = agent.profile.backend == "deepseek"
    agent._last_cache_hit = None  # 每次呼叫重算，避免沿用上一輪的命中數
    with generation(
        name=GENERATION_NAME,
        model=agent.profile.name,
        input=_generation_messages(agent, messages),
        model_parameters={
            "temperature": _cfg.TEMPERATURE,
            "top_p": _cfg.TOP_P,
            "thinking": bool(is_deepseek and agent.profile.supports_thinking),
        },
        metadata={"backend": agent.profile.backend},
    ) as gen:
        try:
            messages = _to_openai_messages(messages)
            extra: dict = {}
            if is_deepseek and agent.profile.supports_thinking:
                extra["extra_body"] = {"thinking": {"type": "enabled"}}
                extra["reasoning_effort"] = "high"
            elif is_deepseek:
                extra["extra_body"] = {"thinking": {"type": "disabled"}}
            try:
                stream = await client.chat.completions.create(
                    model=agent.profile.name,
                    messages=messages,
                    tools=active_tools or NOT_GIVEN,
                    temperature=_cfg.TEMPERATURE,
                    top_p=_cfg.TOP_P,
                    stream=True,
                    stream_options={"include_usage": True},
                    **extra,
                )
            except RateLimitError as e:
                logger.warning(f"API 配額不足（{agent.profile.backend}）：{e}")
                raise RuntimeError(f"❌ API 配額已用盡，請稍後再試。\n{e}") from e

            _tc_buf: dict[int, dict] = {}

            async for chunk in stream:
                if agent._cancelled:
                    break
                if chunk.usage:
                    agent._last_usage = (
                        chunk.usage.completion_tokens,
                        chunk.usage.prompt_tokens,
                    )
                    hit = getattr(chunk.usage, "prompt_cache_hit_tokens", None)
                    if hit is None and getattr(chunk.usage, "model_extra", None):
                        hit = chunk.usage.model_extra.get("prompt_cache_hit_tokens")
                    if hit is None:  # OpenAI / Gemini 形式
                        hit = getattr(
                            getattr(chunk.usage, "prompt_tokens_details", None),
                            "cached_tokens",
                            None,
                        )
                    if hit is not None:
                        agent._last_cache_hit = hit
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                reasoning = getattr(delta, "reasoning_content", None) or (
                    delta.model_extra.get("reasoning_content")
                    if getattr(delta, "model_extra", None)
                    else None
                )
                # 首個實質輸出（思考 / 文字 / 工具呼叫）才算首 token；第一個 chunk 常只帶 role
                if _first_token_at is None and (
                    reasoning or delta.content or delta.tool_calls
                ):
                    _first_token_at = _now()
                if reasoning:
                    thinking_parts.append(reasoning)

                if delta.content:
                    if not reply_parts and thinking_parts:
                        agent._t_thinking_end = time.perf_counter()
                        if on_thinking:
                            await on_thinking("".join(thinking_parts))
                    reply_parts.append(delta.content)
                    if on_token:
                        await on_token(delta.content)

                for tc_chunk in delta.tool_calls or []:
                    i = tc_chunk.index
                    if i not in _tc_buf:
                        _tc_buf[i] = {"id": tc_chunk.id or "", "name": "", "args": ""}
                    if tc_chunk.function:
                        _tc_buf[i]["name"] += tc_chunk.function.name or ""
                        _tc_buf[i]["args"] += tc_chunk.function.arguments or ""

            for idx in sorted(_tc_buf):
                d = _tc_buf[idx]
                tool_calls.append(
                    SimpleNamespace(
                        id=d["id"],
                        function=SimpleNamespace(name=d["name"], arguments=d["args"]),
                    )
                )
        finally:
            if gen is not None:
                # ChatML 訊息陣列（含 tool_calls），Session 視圖才能把這一步串進對話
                gen.update(
                    output=generation_output("".join(reply_parts), tool_calls),
                    metadata=_generation_metadata(agent, thinking_parts),
                    usage_details=_usage_details(
                        agent._last_usage, agent._last_cache_hit
                    ),
                    completion_start_time=_first_token_at,
                )
