"""StreamingMixin — AI 串流回覆 worker。"""

from __future__ import annotations

import asyncio

from loguru import logger
from textual import work

from core.model_stream import CancelToken
from core.turn import Done, ToolEvent, TurnEvent, TurnRequest, run_turn
from widgets.chat_log import ChatLog
from widgets.input import HistoryInput, PromptSpinner
from widgets.sidebar import Sidebar
from widgets.tree import FileTree

from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from app import ChatApp as _MixinBase
    from core.agent import Agent
else:
    _MixinBase = object  # type: ignore[assignment,misc]


_FS_MUTATING = frozenset(
    {"write_file", "create_directory", "move_file", "delete_file", "edit_file"}
)


class StreamingMixin(_MixinBase):
    def _turn_sink(
        self, chat: ChatLog, agent: "Agent", spinner: PromptSpinner
    ) -> Callable[[TurnEvent], Awaitable[None]]:
        """三個 turn 消費者共用的 sink：畫到 ChatLog，並同步檔案樹 / git。"""
        reply = chat.start_reply(
            agent,
            debug=self.debug_tools,
            on_phase=lambda label: setattr(spinner, "label", label),
        )

        async def sink(ev: TurnEvent) -> None:
            await reply(ev)
            if isinstance(ev, ToolEvent):
                tr = ev.result
                if ev.name in _FS_MUTATING and not tr.is_error and not tr.is_warn:
                    self.query_one("#file-tree", FileTree).refresh_tree()
            elif isinstance(ev, Done):
                self.query_one("#sidebar", Sidebar).refresh_git()
                self.query_one("#file-tree", FileTree).refresh_tree()

        return sink

    @work(exclusive=True)
    async def _send(
        self,
        text: str,
        images: list[str] | None = None,
        ephemeral: bool = False,
        original_text: str | None = None,
    ) -> None:
        # 快照 agent 與 chat（訊息容器）：worker 執行期間 self.agent 可能因 tab 切換被替換，
        # 固定使用送出當下的 agent，避免回覆跑到錯誤的 tab。
        agent = self.agent
        chat = self._active_msgs()
        prompt = self.query_one("#prompt", HistoryInput)
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
        sink = self._turn_sink(chat, agent, spinner)

        self.mark_busy(_my_tab_id, cancel)
        try:
            # 只在送出時的 tab 上畫：切到別的 tab 時不蓋掉那邊的標題
            spinner.start()
            prompt.placeholder = ""
            user_msg: dict = {"role": "user", "content": text}
            if images:
                user_msg["images"] = images
            done = await run_turn(
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
            await chat.add_error(f"❌ 錯誤：{e}")

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

        # TTS 只唸使用者自己送出的訊息
        if done is not None and done.reason == "complete" and not ephemeral:
            self._speak(agent, done.reply)
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

    def _speak(self, agent: "Agent", text: str) -> None:
        """TTS：整段回覆完成後在獨立執行緒播放，完全隔離 Textual event loop。"""
        tts_cfg = getattr(agent, "tts_config", None)
        if not tts_cfg or not text:
            return
        from core.tts import TTSConfig, cancel_current, get_tts_loop, speak

        cfg = TTSConfig.from_dict(tts_cfg)
        if cfg is None:
            return
        cancel_current()  # 終止上一段語音（如正在播放）
        # 同時取消排隊中但尚未開始的 future，避免多輪 tool call 造成 TTS 積壓
        prev = getattr(self, "_tts_future", None)
        if prev and not prev.done():
            prev.cancel()
        logger.debug(f"tts: schedule engine={cfg.engine} chars={len(text)}")
        self._tts_future = asyncio.run_coroutine_threadsafe(
            speak(text, cfg), get_tts_loop()
        )
