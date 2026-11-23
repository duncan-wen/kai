"""core.tools.skills — use_skill / list_skills 工具，走既有 run_tool 分發。

這兩個工具讓模型能在對話中動態載入 skill 的完整說明（SKILL.md 本體），
資料來源統一讀 ``core.skills`` 掃描器（單一真相來源），不另起爐灶。
"""

from __future__ import annotations

from ..skills import Skill, discover_skills, load_skill

USE_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "use_skill",
        "description": (
            "載入指定 skill 的完整說明文件（SKILL.md 本體）。"
            "當 system prompt 的「可用技能」索引提到某個 skill、且當前任務符合其觸發時機時，"
            "先呼叫本工具取得該 skill 的完整操作步驟，再依其指示執行。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "skill 名稱（見 system prompt 技能索引）",
                },
            },
            "required": ["name"],
        },
    },
}

LIST_SKILLS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_skills",
        "description": (
            "列出目前專案可用的所有 skills（名稱、描述、觸發時機）。"
            "不確定有哪些 skill、或 system prompt 索引被截斷時使用。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _format_skill(s: Skill) -> str:
    """把一個 skill 格式化成給模型閱讀的完整說明。"""
    parts = [f"# {s.name}", f"描述：{s.description or '—'}"]
    if s.when_to_use:
        parts.append(f"觸發時機：{s.when_to_use}")
    parts.append(f"路徑：{s.path}")
    if s.scripts:
        parts.append(_format_scripts(s))
    if s.body:
        parts.append("\n" + s.body)
    return "\n".join(parts)


def _format_scripts(s: Skill) -> str:
    """捆綁腳本清單 + 安全提示（引導模型用 bash_exec 跑現成腳本）。"""
    lines = [
        "\n## 📦 捆綁腳本（scripts/）",
        "以下腳本隨本 skill 提供、已由作者測試，請優先執行而非自己現寫：",
    ]
    for rel in s.scripts:
        lines.append(f"- {s.path / rel}")
    lines.append("執行方式：用 bash_exec 直接跑上述路徑（例如 `python <路徑>`）。")
    return "\n".join(lines)


async def use_skill(name: str) -> str:
    """回傳指定 skill 的完整 SKILL.md 內容；找不到回傳可用清單提示。"""
    skill = load_skill(name)
    if skill is None:
        available = "、".join(s.name for s in discover_skills()) or "（無）"
        return f"[Error] 找不到 skill：{name}\n可用 skills：{available}"
    return _format_skill(skill)


async def list_skills() -> str:
    """回傳所有可用 skill 的索引清單（名稱 + 描述 + 觸發時機）。"""
    skills = discover_skills()
    if not skills:
        return "目前沒有可用的 skill。"
    lines = []
    for s in skills:
        line = f"- {s.name}: {s.description or '—'}"
        if s.when_to_use:
            line += f"（觸發：{s.when_to_use}）"
        lines.append(line)
    return "\n".join(lines)
