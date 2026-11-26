"""core.model_stream — turn 與模型後端之間的 seam。

turn（`core/turn.py`）只認這裡的中立型別；各後端的 wire 格式差異
（assistant tool_calls 形狀、tool 訊息用 `tool_call_id` 或 `name`、
`reasoning_content`、圖片編碼）全部收在 adapter（`core/backends.py`）裡。

中立訊息格式（turn 內部使用，history 也存這個形狀）：

- ``{"role": "system" | "user", "content": str, "images"?: [path]}``
- ``{"role": "assistant", "content": str, "tool_calls"?: [ToolCall], "reasoning_content"?: str}``
- ``{"role": "tool", "tool_call_id": str, "name": str, "content": str}``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Literal, Protocol

if TYPE_CHECKING:
    from .models import ModelProfile


class CancelToken:
    """取消旗標：UI 持有並在 Esc 時呼叫 `cancel()`；turn 與 adapter 只讀。"""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


@dataclass(frozen=True)
class Delta:
    """串流中的增量：`thinking` / `text` 帶文字；`reset` 表示本 step 重來（清掉已送出的文字）。"""

    kind: Literal["thinking", "text", "reset"]
    text: str = ""


@dataclass(frozen=True)
class ToolCall:
    """模型要求的一次工具呼叫。`arguments` 一律是模型給的原始 JSON 字串（adapter 負責正規化）。"""

    id: str
    name: str
    arguments: str


@dataclass
class Usage:
    """一次模型呼叫的 token 用量；adapter 串流時逐步填入。"""

    gen_tokens: int | None = None
    prompt_tokens: int | None = None
    cache_hit: int | None = None
    # 後端回報的 context window；只有會把 prompt 塞進固定 window 的後端（Ollama）才填
    num_ctx: int | None = None


@dataclass
class AssistantStep:
    """一次模型呼叫（step）的正規化結果。"""

    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


class QuotaError(Exception):
    """API 配額用盡（rate limit）。adapter 把供應商的例外轉成這個型別。"""


OnDelta = Callable[[Delta], Awaitable[None]]


class ModelStream(Protocol):
    # assistant 訊息要帶 reasoning_content 回送給模型（DeepSeek 的 thinking 模式要求）
    keeps_reasoning: bool

    async def step(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        think: bool,
        on_delta: OnDelta,
        cancel: CancelToken,
    ) -> AssistantStep: ...


def make_model_stream(profile: "ModelProfile") -> ModelStream:
    """依 profile.backend 建立 adapter。profile 以參照保存，之後改 num_ctx 也會生效。"""
    from .backends import OllamaStream, OpenAICompatStream

    if profile.backend in ("gemini", "deepseek"):
        return OpenAICompatStream(profile)
    return OllamaStream(profile)
