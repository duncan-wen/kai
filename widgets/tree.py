"""File tree widgets: RightClickTree, TreeContextMenu, FileTree."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Label, OptionList, Tree
from textual.widgets._tree import TreeNode

from textual_fspicker import FileSave
from .utils import _HIDDEN_DIRS, _copy_to_clipboard, _file_icon


class ConfirmDeleteScreen(ModalScreen[bool]):
    """刪除確認對話框；支援單項或批次。"""

    DEFAULT_CSS = """
    ConfirmDeleteScreen {
        align: center middle;
    }
    ConfirmDeleteScreen Vertical {
        width: 50;
        height: auto;
        padding: 1 2;
        border: round $error;
        background: $surface;
    }
    ConfirmDeleteScreen Label {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }
    ConfirmDeleteScreen Horizontal {
        width: 100%;
        height: auto;
        align: center middle;
    }
    ConfirmDeleteScreen Button {
        margin: 0 1;
    }
    """

    def __init__(self, paths: list[Path]) -> None:
        super().__init__()
        self._paths = paths

    def compose(self) -> ComposeResult:
        with Vertical():
            if len(self._paths) == 1:
                kind = "資料夾" if self._paths[0].is_dir() else "檔案"
                yield Label(f"刪除{kind}？")
                yield Label(self._paths[0].name, markup=False)
            else:
                yield Label(f"刪除 {len(self._paths)} 個項目？")
                names = "\n".join(p.name for p in self._paths[:5])
                if len(self._paths) > 5:
                    names += f"\n… 還有 {len(self._paths) - 5} 個"
                yield Label(names, markup=False)
            with Horizontal():
                yield Button("刪除", id="confirm", variant="error")
                yield Button("取消", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel").focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


def _relative_display(path: Path) -> str:
    """回傳相對於 cwd 的路徑字串；資料夾加尾斜線。"""
    try:
        rel = path.relative_to(Path(os.getcwd()))
    except ValueError:
        rel = path
    return str(rel) + ("/" if path.is_dir() else "")


def _copy_filename_bg(app, path: Path) -> None:
    """背景複製路徑，失敗才補 error toast。"""
    ok = _copy_to_clipboard(_relative_display(path))
    if not ok:
        app.call_from_thread(app.notify, "複製失敗", severity="warning", timeout=1.5)


class RightClickTree(Tree):
    """Tree subclass：攔截右鍵、Ctrl+click；Space/Ctrl+A 鍵盤多選。"""

    BINDINGS = [
        Binding("space", "toggle_select", "選取", show=False),
        Binding("ctrl+a", "select_all", "全選", show=False),
        Binding("escape", "clear_select", "清除選取", show=False),
    ]

    class NodeRightClicked(Message):
        def __init__(self, path: Path, sx: int, sy: int) -> None:
            super().__init__()
            self.path = path
            self.sx = sx
            self.sy = sy

    class NodeCtrlClicked(Message):
        def __init__(self, path: Path, line: int) -> None:
            super().__init__()
            self.path = path
            self.line = line

    class SpacePressed(Message):
        pass

    class SelectAllPressed(Message):
        pass

    class ClearSelectPressed(Message):
        pass

    def on_mouse_down(self, event: events.MouseDown) -> None:
        line = int(event.y) + int(self.scroll_y)
        node = self.get_node_at_line(line)
        path = node.data if node else None

        if event.button == 3:
            if isinstance(path, Path):
                self.move_cursor(node)
                event.stop()
                self.post_message(
                    self.NodeRightClicked(path, event.screen_x, event.screen_y)
                )
            return

        if event.button == 1 and isinstance(path, Path) and event.ctrl:
            self.move_cursor(node)
            event.stop()
            self.post_message(self.NodeCtrlClicked(path, line))

    def action_toggle_select(self) -> None:
        self.post_message(self.SpacePressed())

    def action_select_all(self) -> None:
        self.post_message(self.SelectAllPressed())

    def action_clear_select(self) -> None:
        self.post_message(self.ClearSelectPressed())


class TreeContextMenu(ModalScreen):
    """檔案樹右鍵選單；多選時顯示批次刪除。"""

    DEFAULT_CSS = """
    TreeContextMenu {
        background: transparent;
        align: left top;
    }
    TreeContextMenu OptionList {
        width: 26;
        height: auto;
        max-height: 12;
        border: round $primary;
        background: $surface;
    }
    """

    _OPT_COPY = 0
    _OPT_NEW_FILE = 1
    _OPT_DELETE = 2

    def __init__(self, path: Path | None, sx: int, sy: int) -> None:
        super().__init__()
        self._path = path
        self._sx = sx
        self._sy = sy
        self._batch_paths: list[Path] = []

    def compose(self) -> ComposeResult:
        try:
            ft = self.app.query_one(FileTree)
            selected = ft._multi_selected
        except Exception:
            selected = set()

        if len(selected) >= 2 and self._path in selected:
            self._batch_paths = list(selected)
            yield OptionList(
                "複製檔名",
                "新增檔案",
                f"刪除選取 ({len(selected)} 項)",
                id="ctx-options",
            )
        else:
            self._batch_paths = []
            yield OptionList("複製檔名", "新增檔案", "刪除", id="ctx-options")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        x = min(self._sx + 6, self.app.size.width - 28)
        y = min(self._sy, self.app.size.height - 8)
        ol.styles.offset = (x, y)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not self._path:
            self.dismiss()
            return
        if event.option_index == self._OPT_COPY:
            path = self._path
            app = self.app
            app.notify(f"已複製：{_relative_display(path)}", timeout=1.5)
            self.dismiss()
            app.run_worker(lambda: _copy_filename_bg(app, path), thread=True)
        elif event.option_index == self._OPT_NEW_FILE:
            path = self._path
            app = self.app
            self.dismiss()
            start_dir = str(path.parent if path.is_file() else path)

            async def _do_new_file() -> None:
                async def _on_path(new_path) -> None:
                    if not new_path:
                        return
                    try:
                        Path(new_path).touch()
                        app._open_file_tab(Path(new_path))
                        try:
                            ft = app.query_one(FileTree)
                            ft.refresh_tree()
                        except Exception:
                            pass
                    except Exception as e:
                        app.notify(f"新增失敗：{e}", severity="error")

                app.push_screen(
                    FileSave(start_dir, title="新增檔案"),
                    callback=_on_path,
                )

            app.call_after_refresh(lambda: app.run_worker(_do_new_file()))
        elif event.option_index == self._OPT_DELETE:
            paths = self._batch_paths if self._batch_paths else [self._path]
            app = self.app
            self.dismiss()

            async def _do_delete() -> None:
                confirmed = await app.push_screen(
                    ConfirmDeleteScreen(paths), wait_for_dismiss=True
                )
                if not confirmed:
                    return
                failed = []
                for p in paths:
                    try:
                        if p.is_dir():
                            shutil.rmtree(p)
                        else:
                            p.unlink()
                    except Exception as e:
                        failed.append(f"{p.name}: {e}")
                if failed:
                    app.notify("刪除失敗：" + "；".join(failed), severity="error")
                try:
                    ft = app.query_one(FileTree)
                    ft.clear_multi_select()
                    ft.refresh_tree()
                except Exception:
                    pass
                count = len(paths) - len(failed)
                if count:
                    label = paths[0].name if len(paths) == 1 else f"{count} 個項目"
                    app.notify(f"已刪除：{label}", timeout=2)

            app.call_after_refresh(lambda: app.run_worker(_do_delete()))
        else:
            self.dismiss()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 3:
            return
        widget, _ = self.screen.get_widget_at(event.screen_x, event.screen_y)
        ol = self.query_one(OptionList)
        if ol in widget.ancestors_with_self:
            return
        new_path = self._node_at_screen(event.screen_x, event.screen_y)
        self.dismiss()
        if new_path:
            self.app.push_screen(
                TreeContextMenu(new_path, event.screen_x, event.screen_y)
            )

    def _node_at_screen(self, sx: int, sy: int) -> Path | None:
        try:
            tree = self.app.query_one(RightClickTree)
            r = tree.region
            if not r.contains(sx, sy):
                return None
            line = int(sy - r.y) + int(tree.scroll_y)
            node = tree.get_node_at_line(line)
            path = node.data if node else None
            if isinstance(path, Path):
                tree.move_cursor(node)
                return path
        except Exception:
            pass
        return None

    def on_click(self, event: events.Click) -> None:
        widget, _ = self.screen.get_widget_at(event.screen_x, event.screen_y)
        ol = self.query_one(OptionList)
        if ol not in widget.ancestors_with_self:
            self.dismiss()


class FileTree(Widget):
    """左側懶載入檔案樹；選取檔案後填入 prompt；Ctrl/Shift+click 多選批次刪除。"""

    class FileChosen(Message):
        def __init__(self, path: Path) -> None:
            super().__init__()
            self.path = path

    def compose(self) -> ComposeResult:
        cwd = Path(os.getcwd())
        tree: RightClickTree[Path] = RightClickTree(cwd.name)
        tree.root.data = cwd
        tree.root.expand()
        yield tree

    def on_mount(self) -> None:
        self._last_selected: Path | None = None
        self._last_select_time: float = 0.0
        self._multi_selected: set[Path] = set()
        self._path_to_node: dict[Path, TreeNode] = {}
        self._anchor_line: int | None = None
        self._dir_mtimes: dict[str, float] = self._scan_mtimes(Path(os.getcwd()))
        self._populate(self.query_one(RightClickTree).root)
        self.set_interval(2.0, self._check_external_change)

    # ── multi-select ──────────────────────────────────────────────────────────

    def _node_label(self, path: Path, selected: bool) -> str:
        mark = "[bold green]✓[/bold green] " if selected else ""
        if path.is_dir():
            return f"{mark}📁 {path.name}"
        return f"{mark}{_file_icon(path)}{path.name}"

    def _set_node_selected(self, path: Path, selected: bool) -> None:
        node = self._path_to_node.get(path)
        if node:
            node.set_label(self._node_label(path, selected))

    def _toggle_path(self, path: Path) -> None:
        if path in self._multi_selected:
            self._multi_selected.discard(path)
            self._set_node_selected(path, False)
        else:
            self._multi_selected.add(path)
            self._set_node_selected(path, True)

    def _select_range(self, from_line: int, to_line: int) -> None:
        tree = self.query_one(RightClickTree)
        lo, hi = min(from_line, to_line), max(from_line, to_line)
        for line in range(lo, hi + 1):
            node = tree.get_node_at_line(line)
            if node and isinstance(node.data, Path):
                path = node.data
                self._multi_selected.add(path)
                self._path_to_node[path] = node
                self._set_node_selected(path, True)

    def _cursor_line(self) -> int | None:
        tree = self.query_one(RightClickTree)
        node = tree.cursor_node
        if node is None:
            return None
        for i in range(tree.last_line + 1):
            if tree.get_node_at_line(i) is node:
                return i
        return None

    def clear_multi_select(self) -> None:
        for path in list(self._multi_selected):
            self._set_node_selected(path, False)
        self._multi_selected.clear()
        self._anchor_line = None

    def on_right_click_tree_node_ctrl_clicked(
        self, event: RightClickTree.NodeCtrlClicked
    ) -> None:
        self._toggle_path(event.path)
        self._anchor_line = event.line

    def on_right_click_tree_space_pressed(
        self, event: RightClickTree.SpacePressed
    ) -> None:
        tree = self.query_one(RightClickTree)
        node = tree.cursor_node
        if not node or not isinstance(node.data, Path):
            return
        line = self._cursor_line()
        if self._anchor_line is not None and self._multi_selected:
            # 有 anchor：把 anchor 到目前游標的範圍全選
            self._select_range(self._anchor_line, line or self._anchor_line)
            self._anchor_line = line
        else:
            self._toggle_path(node.data)
            self._anchor_line = line

    def on_right_click_tree_select_all_pressed(
        self, event: RightClickTree.SelectAllPressed
    ) -> None:
        tree = self.query_one(RightClickTree)
        if self._multi_selected:
            self.clear_multi_select()
        else:
            for i in range(tree.last_line + 1):
                node = tree.get_node_at_line(i)
                if node and isinstance(node.data, Path):
                    self._multi_selected.add(node.data)
                    self._path_to_node[node.data] = node
                    self._set_node_selected(node.data, True)

    def on_right_click_tree_clear_select_pressed(
        self, event: RightClickTree.ClearSelectPressed
    ) -> None:
        if self._multi_selected:
            self.clear_multi_select()
        else:
            self.app.query_one("#prompt").focus()

    # ── file system watcher ───────────────────────────────────────────────────

    def _scan_mtimes(self, root: Path) -> dict[str, float]:
        result: dict[str, float] = {}
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in _HIDDEN_DIRS]
                p = Path(dirpath)
                try:
                    result[str(p)] = p.stat().st_mtime
                except OSError:
                    pass
                for fname in filenames:
                    fp = p / fname
                    try:
                        result[str(fp)] = fp.stat().st_mtime
                    except OSError:
                        pass
        except Exception:
            pass
        return result

    def _check_external_change(self) -> None:
        cwd = Path(os.getcwd())
        self.run_worker(lambda: self._scan_worker(cwd), thread=True)

    def _scan_worker(self, cwd: Path) -> None:
        new_mtimes = self._scan_mtimes(cwd)
        if new_mtimes != self._dir_mtimes:
            self._dir_mtimes = new_mtimes
            self.app.call_from_thread(self.refresh_tree)

    # ── tree population ───────────────────────────────────────────────────────

    def _populate(self, node: TreeNode[Path]) -> None:
        path: Path | None = node.data
        if not path or not path.is_dir():
            return
        node.remove_children()
        try:
            entries = sorted(
                path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
        except PermissionError:
            node.add_leaf("🔒 無權限", data=None)
            return
        for entry in entries:
            if entry.name in _HIDDEN_DIRS:
                continue
            selected = entry in self._multi_selected
            label = self._node_label(entry, selected)
            if entry.is_dir():
                child = node.add(label, data=entry)
                child.add_leaf("…", data=None)
            else:
                child = node.add_leaf(label, data=entry)
            self._path_to_node[entry] = child

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        if node.data and isinstance(node.data, Path) and node.data.is_dir():
            children = list(node.children)
            if len(children) == 1 and children[0].data is None:
                self._populate(node)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        path: Path | None = event.node.data
        if not path or not path.is_file():
            return
        now = time.monotonic()
        if path == self._last_selected and now - self._last_select_time < 0.4:
            self.post_message(FileTree.FileChosen(path))
            self._last_selected = None
        else:
            self._last_selected = path
            self._last_select_time = now

    def on_right_click_tree_node_right_clicked(
        self, event: RightClickTree.NodeRightClicked
    ) -> None:
        self.app.push_screen(TreeContextMenu(event.path, event.sx, event.sy))

    def _collect_expanded_paths(self) -> set[Path]:
        """收集目前所有展開的目錄節點路徑（root 除外）。"""
        expanded: set[Path] = set()
        try:
            tree = self.query_one(RightClickTree)

            def _walk(node: TreeNode) -> None:
                if (
                    node is not tree.root
                    and node.is_expanded
                    and isinstance(node.data, Path)
                ):
                    expanded.add(node.data)
                for child in node.children:
                    _walk(child)

            _walk(tree.root)
        except Exception:
            pass
        return expanded

    def refresh_tree(self) -> None:
        expanded_paths = self._collect_expanded_paths()
        self._path_to_node.clear()
        tree = self.query_one(RightClickTree)
        cwd = Path(os.getcwd())
        tree.root.label = cwd.name
        tree.root.data = cwd
        self._populate(tree.root)
        tree.root.expand()
        # 由淺到深恢復展開狀態，每層先 _populate 再 expand
        for path in sorted(expanded_paths, key=lambda p: len(p.parts)):
            node = self._path_to_node.get(path)
            if node and not node.is_expanded:
                self._populate(node)
                node.expand()
        self._dir_mtimes = self._scan_mtimes(cwd)
