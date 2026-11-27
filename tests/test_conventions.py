"""CLAUDE.md 寫法規則的機器檢查：違反時直接紅燈，不必靠 review 人工對照。"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI_SOURCES = [ROOT / "app.py", *sorted((ROOT / "mixins").glob("*.py"))]


def _is_self_call(stmt: ast.stmt, name: str) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Attribute)
        and stmt.value.func.attr == name
        and isinstance(stmt.value.func.value, ast.Name)
        and stmt.value.func.value.id == "self"
    )


def _statement_lists(tree: ast.AST) -> Iterator[list[ast.stmt]]:
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if isinstance(stmts, list) and stmts and isinstance(stmts[0], ast.stmt):
                yield stmts


def test_mark_busy_is_immediately_followed_by_try_that_unmarks() -> None:
    """mark_busy 的下一個敘述必須是 try，且其 finally 呼叫 unmark_busy。"""
    bad: list[str] = []
    for path in UI_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for stmts in _statement_lists(tree):
            for i, stmt in enumerate(stmts):
                if not _is_self_call(stmt, "mark_busy"):
                    continue
                nxt = stmts[i + 1] if i + 1 < len(stmts) else None
                ok = isinstance(nxt, ast.Try) and any(
                    _is_self_call(s, "unmark_busy") for s in nxt.finalbody
                )
                if not ok:
                    bad.append(f"{path.relative_to(ROOT)}:{stmt.lineno}")
    assert not bad, f"mark_busy 之後要緊接 try … finally: unmark_busy：{bad}"


_CLASSES = re.compile(r"""classes\s*=\s*["']([^"']+)["']""")


def test_every_css_class_used_has_a_style() -> None:
    """程式裡掛上的 class 都要在 app.tcss 或 widget 的 DEFAULT_CSS 有樣式（Textual 不會報錯）。"""
    sources = [ROOT / "app.py", *(ROOT / "mixins").glob("*.py")]
    sources += (ROOT / "widgets").glob("*.py")
    styles = (ROOT / "app.tcss").read_text(encoding="utf-8")
    styles += "".join(p.read_text(encoding="utf-8") for p in sources)
    used = {
        cls
        for p in sources
        for m in _CLASSES.finditer(p.read_text(encoding="utf-8"))
        for cls in m.group(1).split()
    }
    missing = sorted(c for c in used if not re.search(rf"\.{re.escape(c)}\b", styles))
    assert not missing, f"這些 class 沒有任何樣式：{missing}"
