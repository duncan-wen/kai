"""CenterTabs：中間的 TabbedContent（Chat tab、agent tab、檔案 tab）。

擁有檔案 tab 的建立／去重、tab 關閉（含未存檔確認、點 “×” 關閉）、左右移動，
以及依 tab id 找訊息容器。對外送 `TabClosed` / `ActiveChanged`；agent tab 的 id
由 core.agent_registry 配發，關閉後要不要清 registry 由 App 決定。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger
from textual import events, on, work
from textual.message import Message
from textual.widgets import TabbedContent, TabPane
from textual.widgets._content_switcher import ContentSwitcher
from textual.widgets._tabbed_content import ContentTab, ContentTabs

from .chat import ConfirmCloseScreen, FileViewer
from .chat_log import ChatLog
from .utils import _SUFFIX_TO_LANG


class CenterTabs(TabbedContent):
    """`CHAT_ID` 的 pane 固定在最左、不能關也不能移動。"""

    CHAT_ID = "chat"

    class TabClosed(Message):
        """某個 tab 已從畫面上移除。"""

        def __init__(self, tab_id: str) -> None:
            super().__init__()
            self.tab_id = tab_id

    class ActiveChanged(Message):
        """畫面上作用中的 tab 換了（已讀 `active`，過時的 TabActivated 不會誤報）。"""

        def __init__(self, tab_id: str) -> None:
            super().__init__()
            self.tab_id = tab_id

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._file_tabs: dict[str, Path] = {}
        self._file_tab_counter = 0

    # ── 作用中 tab ────────────────────────────────────────────────────────────

    @on(TabbedContent.TabActivated)
    def _on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        # 讀 self.active 而不是 event.pane.id：晚到的過時事件也只會回報畫面上真正的 tab
        event.stop()
        if self.active:
            self.post_message(self.ActiveChanged(self.active))

    def messages_for(self, tab_id: str) -> ChatLog:
        """回傳 tab 的訊息容器；找不到（例如檔案 tab）時退回 Chat tab 的。"""
        if tab_id != self.CHAT_ID:
            try:
                return self.get_pane(tab_id).query_one(ChatLog)
            except Exception:
                pass
        return self.get_pane(self.CHAT_ID).query_one(ChatLog)

    # ── 開啟 ──────────────────────────────────────────────────────────────────

    async def add_agent_tab(self, tab_id: str, label: str) -> ChatLog:
        """為 agent 開一個 tab（id 由 AgentRegistry 配發），回傳它的訊息容器。"""
        log = ChatLog(id=f"msgs-{tab_id}")
        await self.add_pane(TabPane(f"🤖 {label} ×", log, id=tab_id))
        return log

    @work
    async def open_file(self, path: Path) -> None:
        """在檔案 tab 開啟檔案；已開啟就切過去。"""
        for tid, p in self._file_tabs.items():
            if p == path:
                self.active = tid
                return
        self._file_tab_counter += 1
        tab_id = f"ft-{self._file_tab_counter}"
        try:
            content = await asyncio.to_thread(
                path.read_text, encoding="utf-8", errors="replace"
            )
        except Exception as e:
            self.notify(f"無法讀取 {path.name}：{e}", severity="error")
            return
        viewer = FileViewer(
            content,
            language=_SUFFIX_TO_LANG.get(path.suffix.lower()),
            show_line_numbers=True,
            soft_wrap=False,
            file_path=path,
        )
        self._file_tabs[tab_id] = path
        await self.add_pane(TabPane(f"{path.name} ×", viewer, id=tab_id))
        self.active = tab_id

    # ── 關閉 ──────────────────────────────────────────────────────────────────

    async def request_close(self, tab_id: str) -> None:
        """關閉 tab；檔案有未存檔修改時先以 modal 確認。Chat tab 不關。"""
        if not tab_id or tab_id == self.CHAT_ID:
            return
        try:
            viewer = self.get_pane(tab_id).query_one(FileViewer)
        except Exception:
            viewer = None
        if viewer is not None and viewer._modified:
            fname = viewer._file_path.name if viewer._file_path else "檔案"

            async def _on_confirm(confirmed: bool | None) -> None:
                if confirmed:
                    await self._close(tab_id)

            self.app.push_screen(ConfirmCloseScreen(fname), callback=_on_confirm)
            return
        await self._close(tab_id)

    async def _close(self, tab_id: str) -> None:
        self._file_tabs.pop(tab_id, None)
        await self.remove_pane(tab_id)
        self.post_message(self.TabClosed(tab_id))

    def on_click(self, event: events.Click) -> None:
        """點 tab 標籤最右側的 “×” 關閉該 tab。"""
        tab = event.widget
        if not isinstance(tab, ContentTab):
            return
        tab_id = ContentTab.sans_prefix(tab.id or "")
        if tab_id == self.CHAT_ID:
            return
        if event.screen_x >= tab.region.x + tab.region.width - 2:
            event.stop()
            self.run_worker(self.request_close(tab_id))

    # ── 移動 ──────────────────────────────────────────────────────────────────

    def move_active(self, direction: int) -> None:
        """將作用中 tab 往左（-1）或右（+1）移一格；不能移動 Chat tab，也不能越過它。"""
        active_id = self.active
        if not active_id or active_id == self.CHAT_ID:
            return
        try:
            tabs_list = self.get_child_by_type(ContentTabs).query_one("#tabs-list")
            tabs = list(tabs_list.query("Tab").results())
            cur = next(
                (
                    i
                    for i, t in enumerate(tabs)
                    if t.id == ContentTab.add_prefix(active_id)
                ),
                None,
            )
            if cur is None:
                return
            tgt = cur + direction
            if tgt < 0 or tgt >= len(tabs):
                return
            if ContentTab.sans_prefix(tabs[tgt].id or "") == self.CHAT_ID:
                return
            if direction == -1:
                tabs_list.move_child(tabs[cur], before=tabs[tgt])
            else:
                tabs_list.move_child(tabs[cur], after=tabs[tgt])
            switcher = self.get_child_by_type(ContentSwitcher)
            panes = list(switcher.children)
            pi = next((i for i, p in enumerate(panes) if p.id == active_id), None)
            if pi is not None:
                ni = pi + direction
                if 0 <= ni < len(panes):
                    if direction == -1:
                        switcher.move_child(panes[pi], before=panes[ni])
                    else:
                        switcher.move_child(panes[pi], after=panes[ni])
        except Exception as e:
            logger.warning(f"move_tab 失敗（tab/pane 可能不同步）：{e}")

    def tab_order(self) -> list[str]:
        """目前標籤列由左到右的 tab id。"""
        tabs_list = self.get_child_by_type(ContentTabs).query_one("#tabs-list")
        return [ContentTab.sans_prefix(t.id or "") for t in tabs_list.query("Tab")]
