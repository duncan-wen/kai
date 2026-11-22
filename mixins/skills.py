"""SkillsMixin — /skills 指令邏輯（鏡像 MCPMixin）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app import ChatApp as _MixinBase
else:
    _MixinBase = object  # type: ignore[assignment,misc]


class SkillsMixin(_MixinBase):
    async def _do_skills_list(self) -> None:
        from core.skills import discover_skills
        from widgets.skills_list import SkillsListScreen

        skills = discover_skills()
        if not skills:
            self.notify(
                "目前沒有可用的 skill（可放於 ~/.kai/skills 或 .kai/skills）",
                severity="warning",
            )
            return
        self.push_screen(SkillsListScreen(skills))
