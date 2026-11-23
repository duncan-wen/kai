"""core.skills — 掃描器（階段 0）：雙層掃描、frontmatter 解析、覆蓋與安全 fallback。"""

from pathlib import Path

import pytest

import core.skills as skills


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    local = tmp_path / "local"
    global_ = tmp_path / "global"
    monkeypatch.setattr(skills, "local_skills_dir", lambda: local)
    monkeypatch.setattr(skills, "global_skills_dir", lambda: global_)
    return local, global_


def _write(d: Path, name: str, fm: str, body: str = "# 步驟") -> Path:
    p = d / name
    p.mkdir(parents=True, exist_ok=True)
    (p / "SKILL.md").write_text(f"---\n{fm}\n---\n{body}\n", encoding="utf-8")
    return p


def test_empty_when_no_dirs(dirs):
    assert skills.discover_skills() == []
    assert skills.skills_index_text() == ""


def test_parse_frontmatter(dirs):
    local, _ = dirs
    _write(
        local,
        "pdf-summarizer",
        "name: pdf-summarizer\ndescription: 從 PDF 抽取文字\nwhen_to_use: 處理 PDF 時",
    )
    result = skills.discover_skills()
    assert len(result) == 1
    s = result[0]
    assert s.name == "pdf-summarizer"
    assert s.description == "從 PDF 抽取文字"
    assert s.when_to_use == "處理 PDF 時"
    assert s.body.strip() == "# 步驟"


def test_local_overrides_global(dirs):
    local, global_ = dirs
    _write(global_, "code-reviewer", "name: code-reviewer\ndescription: 全域版")
    _write(local, "code-reviewer", "name: code-reviewer\ndescription: 專案版")
    result = skills.discover_skills()
    assert len(result) == 1
    assert result[0].description == "專案版"


def test_missing_frontmatter_falls_back_to_dirname(dirs):
    local, _ = dirs
    p = local / "no-fm"
    p.mkdir(parents=True)
    (p / "SKILL.md").write_text("# 純說明\n沒有 frontmatter\n", encoding="utf-8")
    result = skills.discover_skills()
    assert len(result) == 1
    assert result[0].name == "no-fm"
    assert result[0].body.startswith("# 純說明")


def test_bad_yaml_does_not_crash(dirs):
    local, _ = dirs
    p = local / "bad"
    p.mkdir(parents=True)
    (p / "SKILL.md").write_text("---\nname: [unclosed\n---\nbody\n", encoding="utf-8")
    result = skills.discover_skills()
    assert len(result) == 1  # fallback：整篇當 body，name 用目錄名
    assert result[0].name == "bad"


def test_dir_without_skill_md_is_skipped(dirs):
    local, _ = dirs
    (local / "empty-dir").mkdir(parents=True)
    assert skills.discover_skills() == []


def test_load_skill(dirs):
    local, _ = dirs
    _write(local, "git-commit", "name: git-commit\ndescription: 產 commit message")
    s = skills.load_skill("git-commit")
    assert s is not None and s.name == "git-commit"
    assert skills.load_skill("不存在") is None


# ── 階段 3：scripts/ 捆綁 + create_skill ──────────────────────────────


def test_scripts_bundled_are_listed(dirs):
    local, _ = dirs
    p = local / "pdf-tool"
    (p / "scripts").mkdir(parents=True)
    (p / "SKILL.md").write_text(
        "---\nname: pdf-tool\ndescription: 處理 PDF\n---\n# 步驟\n", encoding="utf-8"
    )
    (p / "scripts" / "extract.py").write_text("print('hi')\n", encoding="utf-8")
    (p / "scripts" / "cleanup.sh").write_text("echo hi\n", encoding="utf-8")
    s = skills.discover_skills()[0]
    assert s.scripts == ["scripts/cleanup.sh", "scripts/extract.py"]


def test_no_scripts_dir_means_empty(dirs):
    local, _ = dirs
    _write(local, "plain", "name: plain\ndescription: 無腳本")
    s = skills.discover_skills()[0]
    assert s.scripts == []


def test_use_skill_includes_scripts_and_hint(dirs):
    import asyncio
    from core.tools.skills import use_skill

    local, _ = dirs
    p = local / "pdf-tool"
    (p / "scripts").mkdir(parents=True)
    (p / "SKILL.md").write_text(
        "---\nname: pdf-tool\ndescription: 處理 PDF\n---\n# 步驟\n", encoding="utf-8"
    )
    (p / "scripts" / "extract.py").write_text("print('hi')\n", encoding="utf-8")
    out = asyncio.run(use_skill("pdf-tool"))
    assert "捆綁腳本" in out
    assert "scripts/extract.py" in out
    assert "bash_exec" in out


def test_create_skill_local(dirs):
    local, _ = dirs
    s = skills.create_skill("my-skill", "測試用", "# 步驟一\n做某事")
    assert s.name == "my-skill"
    assert s.description == "測試用"
    assert s.body.strip() == "# 步驟一\n做某事"
    assert (local / "my-skill" / "SKILL.md").is_file()
    assert any(x.name == "my-skill" for x in skills.discover_skills())


def test_create_skill_global(dirs):
    _, global_ = dirs
    s = skills.create_skill(
        "g-skill", "全域", "# body", when_to_use="需要時", global_scope=True
    )
    assert s.name == "g-skill"
    assert s.when_to_use == "需要時"
    assert (global_ / "g-skill" / "SKILL.md").is_file()


def test_create_skill_empty_name_raises(dirs):
    with pytest.raises(ValueError):
        skills.create_skill("  ", "x", "body")
