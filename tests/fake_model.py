"""FakeModel：照腳本回應的 ModelStream adapter，讓 turn 不需要模型伺服器就能測。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Callable

from core.model_stream import AssistantStep, CancelToken, Delta, ToolCall, Usage


@dataclass
class Scripted:
    """一個 step 的腳本：先送 thinking，再逐段送 chunks，最後回傳 tool_calls。"""

    chunks: list[str] = field(default_factory=list)
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    gen_tokens: int | None = None
    prompt_tokens: int | None = None
    num_ctx: int | None = None
    # step 一開始被呼叫（可用來記錄當下狀態）
    on_start: Callable[[], None] | None = None
    # 在第 N 個 chunk 之前送 Delta("reset")（模擬 Ollama XML 重試）
    reset_before: int | None = None


@dataclass
class Call:
    messages: list[dict]
    tools: list[dict]
    think: bool


def reply(*chunks: str, **kw) -> Scripted:
    return Scripted(chunks=list(chunks), **kw)


def tool_step(*calls: tuple[str, str | dict], text: str = "", **kw) -> Scripted:
    return Scripted(
        chunks=[text] if text else [],
        tool_calls=[
            ToolCall(
                id=f"c{i}",
                name=name,
                arguments=args
                if isinstance(args, str)
                else json.dumps(args, ensure_ascii=False),
            )
            for i, (name, args) in enumerate(calls)
        ],
        **kw,
    )


class FakeModel:
    def __init__(
        self, script: list[Scripted | BaseException], *, keeps_reasoning: bool = False
    ) -> None:
        self.script = list(script)
        self.keeps_reasoning = keeps_reasoning
        self.calls: list[Call] = []

    async def step(self, messages, tools, *, think, on_delta, cancel: CancelToken):
        self.calls.append(Call([dict(m) for m in messages], list(tools), think))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if item.on_start:
            item.on_start()
        await asyncio.sleep(0)
        if item.thinking:
            await on_delta(Delta("thinking", item.thinking))
        sent: list[str] = []
        for i, chunk in enumerate(item.chunks):
            if cancel.cancelled:
                break
            if item.reset_before == i:
                sent.clear()
                await on_delta(Delta("reset"))
            sent.append(chunk)
            await on_delta(Delta("text", chunk))
            await asyncio.sleep(0)
        return AssistantStep(
            text="".join(sent),
            thinking=item.thinking,
            tool_calls=list(item.tool_calls),
            usage=Usage(
                gen_tokens=item.gen_tokens,
                prompt_tokens=item.prompt_tokens,
                num_ctx=item.num_ctx,
            ),
        )
