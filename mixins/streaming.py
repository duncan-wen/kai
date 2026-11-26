"""StreamingMixin — AI 串流回覆 worker。"""

from __future__ import annotations

import asyncio
import json

from loguru import logger
from textual import work
from textual.widgets import Collapsible, Markdown, Static


from widgets.chat import AIMessage
from widgets.input import HistoryInput, PromptSpinner
from widgets.sidebar import Sidebar
from widgets.tree import FileTree

from typing import TYPE_CHECKING

from core.model_stream import CancelToken
from core.turn import (
    Compacted,
    Compacting,
    Done,
    StepReset,
    ThinkingDone,
    Token,
    ToolEvent,
    TurnEvent,
    TurnRequest,
    TurnStats,
    run_turn,
)

if TYPE_CHECKING:
    from app import ChatApp as _MixinBase
else:
    _MixinBase = object  # type: ignore[assignment,misc]


def format_stats(stats: TurnStats) -> str:
    """turn 結束後的統計行：思考秒數、耗時、tokens、context 使用率、cache 命中。"""
    parts: list[str] = []
    if stats.thinking_s:
        parts.append(f"思考 {stats.thinking_s:.1f}s")
    parts.append(f"共 {stats.elapsed:.1f}s")
    u = stats.usage
    if u.gen_tokens:
        parts.append(f"{u.gen_tokens} tokens")
    prompt_tok = u.prompt_tokens
    if prompt_tok:
        if u.num_ctx:
            pct = prompt_tok * 100 // u.num_ctx
            parts.append(f"ctx {pct}% ({prompt_tok}/{u.num_ctx})")
        else:
            parts.append(f"輸入 {prompt_tok} tokens")
        if u.cache_hit is not None:
            parts.append(f"cache {u.cache_hit * 100 // prompt_tok}%")
    return " · ".join(parts)


_ERROR_PREFIX = {
    "timeout": "⏱ 模型無回應（逾時）：",
    "overflow": "❌ Context 已滿：",
    "quota": "❌ ",
    "other": "❌ 錯誤：",
}


def error_text(done: Done) -> str:
    assert done.error is not None
    return _ERROR_PREFIX[done.error.kind] + done.error.message


class StreamingMixin(_MixinBase):
    @work(exclusive=True)
    async def _send(
        self,
        text: str,
        images: list[str] | None = None,
        ephemeral: bool = False,
        original_text: str | None = None,
    ) -> None:
        # 快照 agent 與 msgs：worker 執行期間 self.agent 可能因 tab 切換被替換，
        # 固定使用送出當下的 agent，避免回覆跑到錯誤的 tab。
        agent = self.agent
        msgs = self._active_msgs()
        prompt = self.query_one("#prompt", HistoryInput)
        stream_msg: AIMessage | None = None
        cur_text = ""  # 本 step 目前可見的文字（已剝 <next>）
        done: Done | None = None

        self.input_state.last_send_text = original_text or text
        # 記錄本次 streaming 所屬的 tab_id
        _my_tab_id = self.active_tab
        if not ephemeral:
            # 有新訊息代表已脫離 resume 快照
            self._session_store(agent).mark_active()

        cancel = CancelToken()
        spinner = PromptSpinner(
            prompt, "等待回覆", visible=lambda: self.active_tab == _my_tab_id
        )

        def _at_bottom() -> bool:
            return msgs.scroll_y >= msgs.max_scroll_y - 3

        async def notice(text: str) -> None:
            await msgs.mount(Static(text, classes="error-msg", markup=False))
            msgs.scroll_end(animate=False)

        async def finalize(content: str) -> None:
            """把目前串流中的泡泡定稿成 Markdown。"""
            nonlocal stream_msg, cur_text
            if stream_msg is not None:
                await stream_msg.update_content(content, final=True)
            stream_msg = None
            cur_text = ""

        _last_render: float = 0.0

        async def on_thinking(ev: ThinkingDone) -> None:
            spinner.label = "思考中"
            preview = ev.text[:2000] + ("…" if len(ev.text) > 2000 else "")
            await msgs.mount(
                Collapsible(
                    Static(preview, markup=False),
                    title="💡 思考過程",
                    collapsed=True,
                    classes="thinking",
                )
            )
            msgs.scroll_end(animate=False)

        async def on_token(ev: Token) -> None:
            nonlocal stream_msg, _last_render, cur_text
            spinner.label = "生成中"
            cur_text = ev.text
            if stream_msg is None:
                await msgs.mount(
                    Static(
                        f"🤖 {agent.display_name}",
                        classes="ai-header",
                        markup=False,
                    )
                )
                stream_msg = AIMessage()
                await msgs.mount(stream_msg)
            now = asyncio.get_event_loop().time()
            if now - _last_render >= 0.1:
                _last_render = now
                await stream_msg.update_content(cur_text)
                if _at_bottom():
                    msgs.scroll_end(animate=False)

        async def on_tool(ev: ToolEvent) -> None:
            spinner.label = "工具回應中"
            await finalize(cur_text)
            name, args, tr = ev.name, ev.args, ev.result
            # 錯誤判定完全來自分發層的結構化旗標，不再掃描輸出字串
            # （避免回傳程式碼含 "Traceback" 字樣被誤塗紅）
            result = tr.output
            is_err = tr.is_error
            is_warn = tr.is_warn
            _FS_MUTATING = {
                "write_file",
                "create_directory",
                "move_file",
                "delete_file",
                "edit_file",
            }
            if not is_err and not is_warn and name in _FS_MUTATING:
                self.query_one("#file-tree", FileTree).refresh_tree()
            icon = "❌" if is_err else ("⚠️" if is_warn else "🔧")
            card_class = "tool-debug tool-debug-error" if is_err else "tool-debug"
            if not self.debug_tools:
                if is_err:
                    args_preview = json.dumps(args, ensure_ascii=False)
                    if len(args_preview) > 300:
                        args_preview = args_preview[:300] + "…"
                    body = f"參數：{args_preview}\n\n{result[:600]}"
                    await msgs.mount(
                        Collapsible(
                            Static(body, markup=False),
                            title=f"{icon} {name}  [dim]工具錯誤[/dim]",
                            collapsed=True,
                            classes=card_class,
                        )
                    )
                    if _at_bottom():
                        msgs.scroll_end(animate=False)
                return
            args_str = json.dumps(args, ensure_ascii=False, indent=2)
            cap = 1000
            result_preview = result[:cap] + (
                f"\n…（共 {len(result)} 字元）" if len(result) > cap else ""
            )
            at_bot = _at_bottom()
            from textual.containers import Vertical

            await msgs.mount(
                Collapsible(
                    Vertical(
                        Markdown(f"**參數**\n```json\n{args_str}\n```"),
                        Static(
                            result_preview, markup=False, classes="tool-result-body"
                        ),
                    ),
                    title=f"{icon} {name}",
                    collapsed=not is_err,
                    classes=card_class,
                )
            )
            if at_bot:
                msgs.scroll_end(animate=False)

        async def on_done(ev: Done) -> None:
            await finalize(ev.reply if ev.reason == "complete" else cur_text)
            if ev.reason == "empty":
                await msgs.mount(
                    Static(
                        "🤖 [dim]（已完成，無文字回覆）[/dim]",
                        markup=True,
                        classes="ai-header",
                    )
                )
            elif ev.reason == "tool_limit":
                await notice(ev.reply)
            elif ev.reason == "error":
                await notice(error_text(ev))
            if ev.reason in ("complete", "empty", "tool_limit"):
                at_bot = _at_bottom()
                await msgs.mount(
                    Static(format_stats(ev.stats), classes="stats", markup=False)
                )
                if at_bot:
                    msgs.scroll_end(animate=False)
            self.query_one("#sidebar", Sidebar).refresh_git()
            self.query_one("#file-tree", FileTree).refresh_tree()
            if ev.reason == "complete" and not ephemeral:
                self._speak(agent, ev.reply)

        async def sink(ev: TurnEvent) -> None:
            nonlocal done
            if isinstance(ev, ThinkingDone):
                await on_thinking(ev)
            elif isinstance(ev, Token):
                await on_token(ev)
            elif isinstance(ev, StepReset):
                if stream_msg is not None:
                    await stream_msg.update_content("")
            elif isinstance(ev, ToolEvent):
                await on_tool(ev)
            elif isinstance(ev, Compacting):
                spinner.label = "壓縮中"
                await finalize(cur_text)
                await notice("⚡ Context 已滿，正在壓縮對話歷史…")
            elif isinstance(ev, Compacted):
                await notice("✅ 已壓縮，繼續對話")
            elif isinstance(ev, Done):
                done = ev
                await on_done(ev)

        # mark_busy 緊接 try：之後任何例外都會走到 finally 解鎖
        self.mark_busy(_my_tab_id, cancel)
        try:
            # 只在送出時的 tab 上畫：切到別的 tab 時不蓋掉那邊的標題
            spinner.start()
            prompt.placeholder = ""
            user_msg: dict = {"role": "user", "content": text}
            if images:
                user_msg["images"] = images
            await run_turn(
                agent,
                TurnRequest(
                    user_msg,
                    persist="none" if ephemeral else "all",
                    think=self._thinking_active(),
                ),
                sink,
                cancel,
            )
        except Exception as e:
            # run_turn 只有程式 bug 才 raise；接住以免 @work 關掉整個 app
            logger.exception("turn 執行例外")
            await notice(f"❌ 錯誤：{e}")

        finally:
            spinner.stop()
            # 若使用者還在這個 tab，prompt 自動解鎖並 focus
            self.unmark_busy(_my_tab_id, cancel)
            if self.active_tab == _my_tab_id:
                prompt.reset_prediction(f"傳訊息給 {agent.display_name}...")
                # turn 中途要求的換模型此時才生效，同步顯示
                self.refresh_agent_view()
                if self.input_state.restore_input:
                    self.input_state.restore_input = False
                    restored = self.input_state.last_send_text
                    prompt.value = restored
                    prompt.cursor_position = len(restored)
            else:
                # 使用者已切到其他 tab：回填的輸入不適用那個 tab，丟掉
                if self.input_state.restore_input:
                    self.input_state.restore_input = False

        if ephemeral:
            if done is not None and done.new_messages:
                self.input_state.last_ephemeral = list(done.new_messages)
                self.notify("💾 /save 可將此回答存入對話歷史", timeout=4)
            return
        if done is not None and done.prediction:
            prompt.show_prediction(done.prediction)
        _pending = self.tab_state.pending_open_agent
        if _pending:
            self.tab_state.pending_open_agent = None
            await self._do_load_agent(_pending)

    def _speak(self, agent, text: str) -> None:
        """TTS：整段回覆完成後在獨立執行緒播放，完全隔離 Textual event loop。"""
        tts_cfg = getattr(agent, "tts_config", None)
        if not tts_cfg or not text:
            return
        from core.tts import cancel_current, get_tts_loop, speak

        cancel_current()  # 終止上一段語音（如正在播放）
        # 同時取消排隊中但尚未開始的 future，避免多輪 tool call 造成 TTS 積壓
        prev = getattr(self, "_tts_future", None)
        if prev and not prev.done():
            prev.cancel()
        engine = tts_cfg.get("engine", "edge-tts")
        logger.debug(f"tts: schedule engine={engine} chars={len(text)}")
        tts_kw = dict(
            voice=tts_cfg.get("voice", "en-US-AriaNeural"),
            player=tts_cfg.get("player", "auto"),
            engine=engine,
            lang=tts_cfg.get("lang", "en-us"),
            host=tts_cfg.get("host", "localhost"),
            port=int(tts_cfg.get("port", 8886)),
            speed=float(tts_cfg.get("speed", 1.0)),
            key=tts_cfg.get("key", ""),
            region=tts_cfg.get("region", ""),
            endpoint=tts_cfg.get("endpoint", ""),
        )
        self._tts_future = asyncio.run_coroutine_threadsafe(
            speak(text, **tts_kw), get_tts_loop()
        )
