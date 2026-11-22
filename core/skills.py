"""Skill 掃描器 — 單一真相來源。

掃描全域 ``~/.kai/skills`` 與專案 ``<cwd>/.kai/skills`` 兩層，
解析 Claude Skills 格式的 ``SKILL.md``（YAML frontmatter + Markdown body），
並掃描捆綁的 ``scripts/`` 目錄。

system prompt 索引、側邊欄 ``🧩 Skills``、``/skills`` 指令、``use_skill``/``list_skills``
工具與 ``create_skill`` app tool 全部讀這裡，確保單一真相來源。

設計與決策見 ``docs/skill-system.md``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

GLOBAL_SKILLS_DIR = Path.home() / ".kai" / "skills"


@dataclass
class Skill:
    """一個 skill 的解析結果。"""

    name: str
    description: str
    when_to_use: str
    path: Path  # skill 目錄（含 SKILL.md，可附 scripts/、references/）
    body: str  # frontmatter 之後的 Markdown 本體
    scripts: list[str] = field(default_factory=list)  # scripts/ 下檔案（相對 skill 目錄）


def local_skills_dir() -> Path:
    """``<cwd>/.kai/skills/``（專案專屬，可 commit）。"""
    return Path.cwd() / ".kai" / "skills"


def global_skills_dir() -> Path:
    """``~/.kai/skills/``（跨專案共用）。"""
    return GLOBAL_SKILLS_DIR


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """把 SKILL.md 拆成 (frontmatter dict, body)。

    無 frontmatter 或解析失敗時回傳 (空 dict, 原文)，不拋錯。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    fm_text = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    try:
        data = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return data, body


def _list_scripts(skill_dir: Path) -> list[str]:
    """收集 skill 捆綁的 ``scripts/`` 下檔案，回傳相對 skill 目錄的路徑。"""
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.is_dir():
        return []
    return sorted(
        str(p.relative_to(skill_dir)) for p in scripts_dir.rglob("*") if p.is_file()
    )


def _parse_skill_md(skill_dir: Path) -> Skill | None:
    """解析單一目錄下的 SKILL.md；缺檔或格式怪異時回傳 None。"""
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return None
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return None
    fm, body = _split_frontmatter(text)
    name = str(fm.get("name") or skill_dir.name).strip()
    description = str(fm.get("description") or "").strip()
    when_to_use = str(fm.get("when_to_use") or "").strip()
    return Skill(
        name=name,
        description=description,
        when_to_use=when_to_use,
        path=skill_dir,
        body=body,
        scripts=_list_scripts(skill_dir),
    )


def discover_skills() -> list[Skill]:
    """掃兩層目錄並回傳所有 skill；專案層覆蓋全域同名，且排前面。

    目錄不存在、單一 skill 解析失敗時皆安全跳過，不拋錯。
    """
    seen: dict[str, Skill] = {}
    for base in (local_skills_dir(), global_skills_dir()):
        if not base.is_dir():
            continue
        for p in sorted(base.iterdir()):
            if not p.is_dir():
                continue
            skill = _parse_skill_md(p)
            if skill is None or skill.name in seen:
                continue
            seen[skill.name] = skill
    return list(seen.values())


def skills_index_text() -> str:
    """產「名稱 + 描述」索引，供 system prompt 與側邊欄用；無 skill 回傳空字串。"""
    skills = discover_skills()
    if not skills:
        return ""
    return "\n".join(f"- {s.name}: {s.description}" for s in skills)


def load_skill(name: str) -> Skill | None:
    """依名稱回傳完整 skill（含 body）；找不到回傳 None。"""
    for s in discover_skills():
        if s.name == name:
            return s
    return None


def create_skill(
    name: str,
    description: str,
    body: str,
    when_to_use: str = "",
    global_scope: bool = False,
) -> Skill:
    """建立一個新 skill：寫 ``SKILL.md`` 到目標層並回傳解析後的 Skill。

    ``global_scope=False`` → ``<cwd>/.kai/skills/<name>/``（專案，可 commit）；
    ``True`` → ``~/.kai/skills/<name>/``（跨專案）。
    已存在同名 skill 會覆寫其 SKILL.md（scripts/ 不受影響）。
    """
    name = name.strip()
    if not name:
        raise ValueError("skill 名稱不可為空")
    base = global_skills_dir() if global_scope else local_skills_dir()
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm: dict[str, Any] = {"name": name, "description": description.strip()}
    if when_to_use.strip():
        fm["when_to_use"] = when_to_use.strip()
    frontmatter = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    content = f"---\n{frontmatter}\n---\n\n{body.strip()}\n"
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    skill = _parse_skill_md(skill_dir)
    if skill is None:  # 剛寫入，理論上不會發生；防呆
        raise RuntimeError(f"SKILL.md 寫入後無法解析：{skill_dir}")
    return skill
