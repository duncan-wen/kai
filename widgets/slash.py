"""Slash 指令表：所有 `/xxx` 指令的唯一定義來源。

送出輸入（`CommandsMixin.run_slash`）、自動完成（`ChatAutoComplete`）、
命令面板（`ChatCommands`）都從這裡讀，新增指令只需在 handler 方法上加 `@slash(...)`。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class SlashCommand:
    name: str
    """指令名稱，含開頭斜線，例如 `/ask`。"""
    help: str
    """一行說明，顯示在自動完成與命令面板。"""
    method: str
    """ChatApp 上的 handler 方法名；handler 簽名一律為 `async (self, arg: str) -> None`。"""
    needs_arg: bool = False
    """命令面板選取時，先把指令填進 prompt 讓使用者補參數，而不是直接執行。"""
    presets: tuple[tuple[str, str], ...] = field(default=())
    """常用的完整寫法（例如 `/tts off`），額外列進自動完成與命令面板。"""


_COMMANDS: dict[str, SlashCommand] = {}


def slash(
    name: str,
    help: str,
    *,
    needs_arg: bool = False,
    presets: tuple[tuple[str, str], ...] = (),
) -> Callable[[F], F]:
    """把方法註冊成 slash 指令 handler（依定義順序排列）。"""

    def deco(fn: F) -> F:
        if name in _COMMANDS:
            raise ValueError(f"slash 指令重複定義：{name}")
        _COMMANDS[name] = SlashCommand(name, help, fn.__name__, needs_arg, presets)
        return fn

    return deco


def get_command(name: str) -> SlashCommand | None:
    return _COMMANDS.get(name.lower())


def all_commands() -> list[SlashCommand]:
    return list(_COMMANDS.values())


def completion_items() -> list[tuple[str, str]]:
    """自動完成用的 (文字, 說明) 清單：每個指令本身，加上它的 presets。"""
    items: list[tuple[str, str]] = []
    for cmd in _COMMANDS.values():
        items.append((cmd.name, cmd.help))
        items.extend(cmd.presets)
    return items


def split_command(text: str) -> tuple[str, str]:
    """`"/load-agent a b"` → `("/load-agent", "a b")`；指令名轉小寫。"""
    parts = text.split(maxsplit=1)
    name = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    return name, arg
