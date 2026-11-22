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
    _write(local, "pdf-summarizer",
           "name: pdf-summarizer\ndescription: 從 PDF 抽取文字\nwhen_to_use: 處理 PDF 時")
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
