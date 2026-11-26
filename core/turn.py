"""core.turn — 一個 Turn：從 user 訊息送出到最終回覆（或結束原因）。

進入點 `run_turn(agent, request, sink, cancel)`：
- 取得 `agent.turn_lock`（同一個 agent 的 turn 依序執行），整輪包在 `agent.turn_scope()`
- 跑 tool calling loop：每個 Step 透過 `agent.model_stream`（`core.model_stream` seam）呼叫模型
- 過程以型別化事件送進 `sink`；結束時送出並回傳同一個 `Done`
- history 只在成功結束時一次 commit；錯誤 / 取消不動 history
- 模型 / 後端錯誤不 raise，變成 `Done(reason="error")`；只有程式 bug 才會 raise
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Literal, Union

from loguru import logger

from . import config as _cfg
from .langfuse_client import (
    assistant_message,
    get_langfuse,
    system_fingerprint,
    trace,
    user_message,
)
from .model_stream import AssistantStep, CancelToken, Delta, QuotaError, Usage
from .session_store import SessionStore
from .tool_log import log_tool_call, log_turn_start
from .tools import ToolResult, run_tool

if TYPE_CHECKING:
    from pathlib import Path

    from .agent import Agent
    from .model_stream import ToolCall


# ── 請求與事件 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TurnRequest:
    """一個 turn 的輸入。

    persist：`all` 存 user + 回覆；`reply_only` 只存回覆（greeting 的觸發訊息不存）；
    `none` 都不存（`/ask`，之後可用 `await agent.commit(done.new_messages)` 補存）。
    think：None 跟模型預設（`profile.supports_thinking`）。
    """

    user_msg: dict
    persist: Literal["all", "reply_only", "none"] = "all"
    think: bool | None = None


@dataclass(frozen=True)
class ThinkingDone:
    """本 step 的思考結束：開始出文字時送一次；只思考沒文字時在 step 結束送。"""

    text: str
    seconds: float


@dataclass(frozen=True)
class Token:
    """文字增量。`text` 是本 step 累積、已剝掉 `<next>` 的可見文字。"""

    delta: str
    text: str


@dataclass(frozen=True)
class StepReset:
    """後端重試本 step（Ollama XML 錯誤）：清掉本 step 已顯示的文字。"""


@dataclass(frozen=True)
class ToolEvent:
    """一個工具執行完畢。"""

    name: str
    args: dict
    result: ToolResult


@dataclass(frozen=True)
class Compacting:
    """context 爆了，開始壓縮 history。"""


@dataclass(frozen=True)
class Compacted:
    """壓縮完成，用同一則訊息重試。"""


@dataclass(frozen=True)
class TurnStats:
    """整個 turn 的統計。usage：gen_tokens 是各 step 加總，其餘取最後一個 step。"""

    elapsed: float = 0.0
    thinking_s: float | None = None
    usage: Usage = field(default_factory=Usage)


ErrorKind = Literal["overflow", "timeout", "quota", "other"]


@dataclass(frozen=True)
class TurnError:
    kind: ErrorKind
    message: str


DoneReason = Literal["complete", "empty", "cancelled", "tool_limit", "error"]


@dataclass(frozen=True)
class Done:
    """turn 結束，每輪恰好一次。`new_messages` 是本輪產生、可 commit 的訊息（user + 回覆）。"""

    reason: DoneReason
    reply: str = ""
    stats: TurnStats = field(default_factory=TurnStats)
    prediction: str = ""
    error: TurnError | None = None
    new_messages: tuple[dict, ...] = ()


TurnEvent = Union[
    ThinkingDone, Token, StepReset, ToolEvent, Compacting, Compacted, Done
]
Sink = Callable[[TurnEvent], Awaitable[None]]


# ── 純函式 ────────────────────────────────────────────────────────────────────

_NEXT = "<next>"
_NEXT_RE = re.compile(r"\s*<next>(.*?)</next>\s*$", re.DOTALL)
# 超過這個長度代表模型把回覆內容誤放進 <next>，不當預測
PREDICTION_MAX_CHARS = 50


def visible_text(acc: str) -> str:
    """串流中可顯示的文字：剝掉 `<next>` 之後的內容，也藏住結尾半截的 `<nex`。"""
    i = acc.find(_NEXT)
    if i != -1:
        return acc[:i].rstrip()
    for k in range(len(_NEXT) - 1, 0, -1):
        if acc.endswith(_NEXT[:k]):
            return acc[:-k]
    return acc


def split_prediction(text: str) -> tuple[str, str]:
    """最終回覆 → (回覆, 預測)。結尾 `<next>…</next>` 是預測；沒關上的 `<next>` 直接剝掉。"""
    m = _NEXT_RE.search(text)
    if m:
        pred = m.group(1).strip()
        return text[: m.start()].rstrip(), (
            pred if len(pred) <= PREDICTION_MAX_CHARS else ""
        )
    i = text.rfind(_NEXT)
    if i != -1 and "</next>" not in text[i:]:
        return text[:i].rstrip(), ""
    return text, ""


_OVERFLOW_KEYWORDS = (
    "context",
    "too long",
    "window",
    "kv cache",
    "resource_exhausted",
    "maximum context",
    "exceeds the maximum",
    "token limit",
    "context length",
    "prompt is too long",
    "reduce the length",
)


def classify_error(e: BaseException) -> ErrorKind:
    if isinstance(e, QuotaError):
        return "quota"
    if isinstance(e, TimeoutError):
        return "timeout"
    msg = str(e).lower()
    if any(kw in msg for kw in _OVERFLOW_KEYWORDS):
        return "overflow"
    return "other"


def parse_tool_args(tc: "ToolCall") -> tuple[dict, str | None]:
    """工具參數 → (args, 錯誤訊息)。空字串視為無參數。"""
    try:
        parsed = json.loads(tc.arguments or "{}")
    except json.JSONDecodeError as e:
        return {"_raw": tc.arguments}, f"工具參數不是合法的 JSON：{e}"
    if not isinstance(parsed, dict):
        return {"_raw": tc.arguments}, "工具參數必須是 JSON 物件"
    return parsed, None


# ── turn 本體 ─────────────────────────────────────────────────────────────────


class _StepFailed(Exception):
    """model_stream.step() 丟出的例外（後端錯誤），和程式 bug 分開處理。"""

    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


class _Turn:
    def __init__(
        self, agent: "Agent", request: TurnRequest, sink: Sink, cancel: CancelToken
    ) -> None:
        self.agent = agent
        self.request = request
        self.sink = sink
        self.cancel = cancel
        self.t0 = time.perf_counter()
        self.steps = 0
        self.thinking_s = 0.0
        self.usage = Usage()
        self.tool_log: list[str] = []
        # 本輪已完成的 assistant(tool_calls) + tool 訊息；overflow 重試時接著用，不重跑工具
        self.progress: list[dict] = []
        self.tool_rounds = 0
        self.think = (
            request.think
            if request.think is not None
            else agent.profile.supports_thinking
        )

    # ── 結束 ──

    def _done(self, reason: DoneReason, **kw) -> Done:
        stats = TurnStats(
            elapsed=time.perf_counter() - self.t0,
            thinking_s=self.thinking_s or None,
            usage=dataclasses.replace(self.usage),
        )
        return Done(reason=reason, stats=stats, **kw)

    async def _finish(
        self, reason: DoneReason, reply: str, asst: dict, prediction: str = ""
    ) -> Done:
        new = (self.request.user_msg, asst)
        if self.request.persist == "all":
            await self.agent.commit(list(new))
        elif self.request.persist == "reply_only":
            await self.agent.commit([asst])
        return self._done(reason, reply=reply, prediction=prediction, new_messages=new)

    # ── 主流程 ──

    async def run(self) -> Done:
        agent = self.agent
        user_msg = self.request.user_msg
        if "images" in user_msg and not agent.profile.supports_vision:
            return self._done(
                "error",
                error=TurnError("other", f"{agent.profile.name} 不支援圖片輸入"),
            )
        await agent.prepare_turn()
        log_turn_start(len(agent.history) + 1, agent.agent_dir)

        compacted = False
        while True:
            try:
                return await self._loop()
            except _StepFailed as f:
                kind = classify_error(f.cause)
                logger.error(f"模型串流錯誤（{kind}）：{f.cause}")
                if kind != "overflow":
                    return self._done("error", error=TurnError(kind, str(f.cause)))
                if compacted:
                    return self._done(
                        "error", error=TurnError(kind, f"壓縮後仍失敗：{f.cause}")
                    )
                if not agent.compact_available():
                    return self._done(
                        "error",
                        error=TurnError(
                            kind,
                            "沒有可用的壓縮後端（需要 GEMINI_API_KEY 或 DEEPSEEK_API_KEY）"
                            f"：{f.cause}",
                        ),
                    )
                compacted = True
                await self.sink(Compacting())
                try:
                    await agent.compact_history()
                except Exception as e:
                    logger.exception("autocompact 失敗")
                    return self._done(
                        "error", error=TurnError("other", f"壓縮失敗：{e}")
                    )
                await self.sink(Compacted())

    async def _loop(self) -> Done:
        agent = self.agent
        model = agent.model_stream
        # 重試時 history 已被壓縮，但本輪做完的工具步驟（progress）照樣接上
        messages = agent.messages_for(self.request.user_msg) + self.progress
        while True:
            step = await self._step(messages)
            if self.cancel.cancelled:
                return self._done("cancelled")

            asst: dict = {"role": "assistant", "content": step.text}
            if model.keeps_reasoning and step.thinking:
                asst["reasoning_content"] = step.thinking

            if not step.tool_calls:
                if not step.text:
                    logger.warning("模型未產生文字回覆（僅思考）")
                    return self._done("empty")
                reply, prediction = split_prediction(step.text)
                asst["content"] = reply
                return await self._finish("complete", reply, asst, prediction)

            asst["tool_calls"] = step.tool_calls
            round_msgs = [asst]
            results = await asyncio.gather(
                *[self._call_tool(tc) for tc in step.tool_calls]
            )
            # 取消時工具也已經跑完（可能有副作用）：照樣記 log、送 ToolEvent 再停
            for tc, (args, tr) in zip(step.tool_calls, results):
                log_tool_call(tr.fn_name, args, tr.output, agent.agent_dir)
                await self.sink(ToolEvent(tc.name, args, tr))
                self.tool_log.append(tc.name)
                round_msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": agent.trim_tool_result(tr.output),
                    }
                )
            if self.cancel.cancelled:
                return self._done("cancelled")
            messages.extend(round_msgs)
            self.progress.extend(round_msgs)

            self.tool_rounds += 1
            if self.tool_rounds >= _cfg.MAX_TOOL_CALLS:
                logger.warning(f"已達工具呼叫上限 {_cfg.MAX_TOOL_CALLS} 次，強制結束")
                tools_str = "、".join(self.tool_log) or "（無）"
                progress = (
                    f"⚠️ 已達工具呼叫上限（{_cfg.MAX_TOOL_CALLS} 次），本輪自動中止。\n"
                    f"本輪依序呼叫的工具：{tools_str}\n"
                    "下次對話說「繼續」即可接續未完成的任務。"
                )
                return await self._finish(
                    "tool_limit", progress, {"role": "assistant", "content": progress}
                )

    async def _call_tool(self, tc: "ToolCall") -> tuple[dict, ToolResult]:
        args, err = parse_tool_args(tc)
        if err:
            logger.warning(f"tool '{tc.name}' 參數解析失敗：{err}")
            return args, ToolResult(
                tc.name, f"[Error] {err}。請修正參數後重新呼叫。", is_error=True
            )
        agent = self.agent
        tools_map = {**agent.tools_map, **agent.extra_tools_map}
        return args, await run_tool(tc.name, args, tools_map, agent.mcp_client)

    async def _step(self, messages: list[dict]) -> AssistantStep:
        self.steps += 1
        t_step = time.perf_counter()
        text = ""
        thinking: list[str] = []
        thinking_sent = False

        async def emit_thinking() -> None:
            nonlocal thinking_sent
            thinking_sent = True
            secs = time.perf_counter() - t_step
            self.thinking_s += secs
            await self.sink(ThinkingDone("".join(thinking), secs))

        async def on_delta(d: Delta) -> None:
            nonlocal text, thinking_sent
            if d.kind == "thinking":
                thinking.append(d.text)
            elif d.kind == "reset":
                text = ""
                thinking.clear()
                thinking_sent = False
                await self.sink(StepReset())
            else:
                if thinking and not thinking_sent:
                    await emit_thinking()
                text += d.text
                await self.sink(Token(d.text, visible_text(text)))

        try:
            step = await self.agent.model_stream.step(
                messages,
                self.agent.tool_schemas(),
                think=self.think,
                on_delta=on_delta,
                cancel=self.cancel,
            )
        except Exception as e:
            raise _StepFailed(e) from e

        if thinking and not thinking_sent and not self.cancel.cancelled:
            await emit_thinking()
        u = step.usage
        if u.gen_tokens is not None:
            self.usage.gen_tokens = (self.usage.gen_tokens or 0) + u.gen_tokens
        if u.prompt_tokens is not None:
            self.usage.prompt_tokens = u.prompt_tokens
        self.usage.cache_hit = u.cache_hit
        self.usage.num_ctx = u.num_ctx
        logger.debug(
            f"step {self.steps}：{time.perf_counter() - t_step:.2f}s | "
            f"生成 {u.gen_tokens} tokens，輸入 {u.prompt_tokens} tokens"
            + (f"，cache hit {u.cache_hit}" if u.cache_hit is not None else "")
        )
        return step


def _user_text(user_msg: dict) -> str:
    content = user_msg.get("content")
    if isinstance(content, list):
        return " ".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content or "")


def _session_ids(history_file: "Path") -> tuple[str, str]:
    store = SessionStore(history_file)
    return store.name, store.session_id


async def run_turn(
    agent: "Agent",
    request: TurnRequest,
    sink: Sink,
    cancel: CancelToken | None = None,
) -> Done:
    """跑一個 turn；`Done` 會送進 sink，也是回傳值。"""
    cancel = cancel or CancelToken()
    async with agent.turn_lock:
        # 只在 Langfuse 啟用時才取 session ID（首次取用會在磁碟建立 .session_id）
        session_name, session_id = "", None
        if get_langfuse() is not None:
            session_name, session_id = await asyncio.to_thread(
                _session_ids, agent.history_file
            )
        meta = {
            "model": agent.profile.name,
            "backend": agent.profile.backend,
            "agent": agent.display_name,
            "session_name": session_name,
        }
        turn = _Turn(agent, request, sink, cancel)
        with agent.turn_scope():
            with trace(
                name="chat-turn",
                session_id=session_id,
                user_id=_cfg.USERNAME or None,
                # Langfuse Sessions（Messages 視圖）只讀 trace 根節點的 input/output，
                # 且必須是 {"role", "content"} 形狀才渲染得成對話泡泡。
                input=user_message(_user_text(request.user_msg)[:2000]),
                metadata=meta,
            ) as span:
                done = await turn.run()
                if span is not None:
                    sys_text = agent.system_prompt
                    # metadata 一次給齊：SDK 的 update 是整批覆蓋，不能只給增量
                    span.update(
                        output=assistant_message(done.reply[:2000]),
                        metadata={
                            **meta,
                            "system_sha": system_fingerprint(sys_text),
                            "system_chars": len(sys_text),
                            "generations": turn.steps,
                            "reply_chars": len(done.reply),
                            "reason": done.reason,
                        },
                    )
        await sink(done)
        return done
