"""
Session archive 測試：直接打在 core.session_archive.SessionArchive 這個 interface 上。

所有測試都使用 tmp_path 下的 history 檔，不依賴 cwd，不需要 TUI。
詞彙見 GLOSSARY.md：Session、Archive、Resumed session。
"""

from __future__ import annotations

import json
from pathlib import Path

from core.agent import Agent
from core.session_archive import SessionArchive


# ── helpers ──────────────────────────────────────────────────────────────────


def write_session(history_file: Path, *user_texts: str) -> None:
    """在 history 檔寫入一段 session（system + 每則 user 訊息各配一則 assistant 回覆）。"""
    msgs: list[dict] = [{"role": "system", "content": "sys"}]
    for t in user_texts:
        msgs += [
            {"role": "user", "content": t},
            {"role": "assistant", "content": f"re:{t}"},
        ]
    history_file.write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")


def user_texts(path: Path) -> list[str]:
    """讀出檔案中所有 user 訊息文字。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [m["content"] for m in data if m.get("role") == "user"]


# ── Agent 存取（既有測試，保留） ───────────────────────────────────────────────


def test_agent_save_and_reload(tmp_path: Path) -> None:
    """save_history → 新 Agent 載入 → 訊息完整。"""
    hf = tmp_path / "history.json"
    a = Agent(model="gemma3:4b", history_file=hf)
    a.history += [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    a.save_history()
    a2 = Agent(model="gemma3:4b", history_file=hf)
    assert [m["content"] for m in a2.history if m["role"] == "user"] == ["hello"]


# ── close：封存 ───────────────────────────────────────────────────────────────


def test_close_archives_session_into_history_dir(tmp_path: Path) -> None:
    hf = tmp_path / "history.json"
    write_session(hf, "A")

    result = SessionArchive(hf).close()

    assert result.kind == "archived"
    assert result.archive is not None
    assert result.archive.parent == tmp_path / ".history"
    assert result.archive.name.startswith("history_")
    assert user_texts(result.archive) == ["A"]
    assert not hf.exists()


def test_close_twice_in_same_second_keeps_both_archives(tmp_path: Path) -> None:
    hf = tmp_path / "history.json"
    write_session(hf, "first")
    a1 = SessionArchive(hf).close().archive
    write_session(hf, "second")
    a2 = SessionArchive(hf).close().archive

    assert a1 is not None and a2 is not None and a1 != a2
    assert user_texts(a1) == ["first"]
    assert user_texts(a2) == ["second"]


def test_close_session_without_conversation_is_empty(tmp_path: Path) -> None:
    hf = tmp_path / "history.json"
    write_session(hf)  # 只有 system 訊息

    assert SessionArchive(hf).close().kind == "empty"
    assert SessionArchive(tmp_path / "missing.json").close().kind == "empty"
    assert not (tmp_path / ".history").exists() or not list(
        (tmp_path / ".history").glob("history_*.json")
    )


# ── resume ────────────────────────────────────────────────────────────────────


def test_resume_copies_archive_and_marks_session_resumed(tmp_path: Path) -> None:
    hf = tmp_path / "history.json"
    write_session(hf, "A")
    arch = SessionArchive(hf).close().archive
    assert arch is not None

    SessionArchive(hf).resume(arch)

    assert user_texts(hf) == ["A"]
    assert arch.exists()  # copy，不是 move
    # 新 instance（等同重啟 app）仍知道這是 resumed session
    assert SessionArchive(hf).resumed_from == arch


def test_close_resumed_session_without_new_messages_discards(tmp_path: Path) -> None:
    hf = tmp_path / "history.json"
    write_session(hf, "A")
    sa = SessionArchive(hf)
    arch = sa.close().archive
    assert arch is not None
    sa.resume(arch)

    result = sa.close()

    assert result.kind == "discarded"
    assert not hf.exists()
    assert list((tmp_path / ".history").glob("history_*.json")) == [arch]
    assert sa.resumed_from is None


def resumed(tmp_path: Path, *texts: str) -> tuple[SessionArchive, Path, Path]:
    """建立一個 archive 並 resume 它；回傳 (sa, history_file, archive)。"""
    hf = tmp_path / "history.json"
    write_session(hf, *texts)
    sa = SessionArchive(hf)
    arch = sa.close().archive
    assert arch is not None
    sa.resume(arch)
    return sa, hf, arch


def test_resumed_session_with_new_message_archives_on_close(tmp_path: Path) -> None:
    sa, hf, arch = resumed(tmp_path, "original")
    write_session(hf, "original", "new")
    sa.mark_active()

    assert sa.resumed_from is None
    result = sa.close()

    assert result.kind == "archived"
    assert result.archive != arch
    assert result.archive is not None and user_texts(result.archive) == [
        "original",
        "new",
    ]
    assert user_texts(arch) == ["original"]  # 原 archive 不變


def test_switching_from_active_resumed_session_auto_archives_it(tmp_path: Path) -> None:
    sa, hf, arch_a = resumed(tmp_path, "A")
    write_session(hf, "B")
    sa.mark_active()
    arch_b = sa.close().archive
    assert arch_b is not None

    sa.resume(arch_a)
    write_session(hf, "A", "after A")
    sa.mark_active()
    result = sa.resume(arch_b)

    assert result.kind == "archived"
    assert result.archive is not None and user_texts(result.archive) == ["A", "after A"]
    assert user_texts(hf) == ["B"]
    assert len(list((tmp_path / ".history").glob("history_*.json"))) == 3


def test_switching_from_untouched_resumed_session_adds_no_archive(
    tmp_path: Path,
) -> None:
    sa, hf, arch_a = resumed(tmp_path, "A")
    write_session(hf, "B")
    sa.mark_active()
    arch_b = sa.close().archive
    assert arch_b is not None

    sa.resume(arch_a)
    result = sa.resume(arch_b)

    assert result.kind == "discarded"
    assert len(list((tmp_path / ".history").glob("history_*.json"))) == 2
    assert sa.resumed_from == arch_b


# ── 回歸：resume 中的 archive 被刪除（Q9） ────────────────────────────────────


def test_close_after_resumed_archive_deleted_externally_keeps_conversation(
    tmp_path: Path,
) -> None:
    sa, hf, arch = resumed(tmp_path, "precious")
    arch.unlink()  # 例如在檔案總管裡刪掉

    assert sa.resumed_from is None
    result = sa.close()

    assert result.kind == "archived"
    assert result.archive is not None and user_texts(result.archive) == ["precious"]


def test_close_after_deleting_resumed_archive_via_module_keeps_conversation(
    tmp_path: Path,
) -> None:
    sa, hf, arch = resumed(tmp_path, "precious")
    sa.delete_archive(arch)

    assert not arch.exists()
    result = sa.close()

    assert result.kind == "archived"
    assert result.archive is not None and user_texts(result.archive) == ["precious"]


# ── list_archives ─────────────────────────────────────────────────────────────


def test_list_archives_newest_first_with_preview(tmp_path: Path) -> None:
    hf = tmp_path / "history.json"
    sa = SessionArchive(hf)
    for t in ("old", "mid"):
        write_session(hf, t)
        sa.close()
    write_session(hf, "first q", "last question\nsecond line")
    newest = sa.close().archive

    entries = sa.list_archives()

    assert [e.path for e in entries][0] == newest
    assert len(entries) == 3
    e = entries[0]
    assert e.message_count == 2
    assert e.preview == "last question second line"
    assert len(e.timestamp) == 16 and e.timestamp[4] == "-" and e.timestamp[13] == ":"
    assert e.name == ""


def test_list_archives_survives_unreadable_archive(tmp_path: Path) -> None:
    hist = tmp_path / ".history"
    hist.mkdir()
    (hist / "history_20260101_120000.json").write_text("{broken", encoding="utf-8")

    (entry,) = SessionArchive(tmp_path / "history.json").list_archives()

    assert entry.message_count == 0
    assert entry.preview == "(無法讀取)"


def test_list_archives_empty_when_no_history_dir(tmp_path: Path) -> None:
    assert SessionArchive(tmp_path / "history.json").list_archives() == []


# ── 名稱 ──────────────────────────────────────────────────────────────────────


def names_by_path(sa: SessionArchive) -> dict[Path, str]:
    return {e.path: e.name for e in sa.list_archives()}


def test_named_session_carries_name_into_its_archive(tmp_path: Path) -> None:
    hf = tmp_path / "history.json"
    write_session(hf, "A")
    SessionArchive(hf).rename("專案討論")
    sa = SessionArchive(hf)  # 新 instance：名稱跨重啟保留
    assert sa.name == "專案討論"

    arch = sa.close().archive

    assert arch is not None
    assert names_by_path(sa)[arch] == "專案討論"
    assert sa.name == ""  # 下一個 session 從未命名開始


def test_resumed_session_shows_archive_name(tmp_path: Path) -> None:
    hf = tmp_path / "history.json"
    write_session(hf, "A")
    sa = SessionArchive(hf)
    sa.rename("舊名")
    arch = sa.close().archive
    assert arch is not None

    sa.resume(arch)

    assert sa.name == "舊名"


def test_renaming_untouched_resumed_session_renames_its_archive(tmp_path: Path) -> None:
    sa, hf, arch = resumed(tmp_path, "A")
    sa.rename("新名")

    assert sa.close().kind == "discarded"
    assert names_by_path(sa)[arch] == "新名"


def test_active_resumed_session_inherits_archive_name(tmp_path: Path) -> None:
    sa, hf, arch = resumed(tmp_path, "A")
    sa.rename_archive(arch, "原名")
    write_session(hf, "A", "more")
    sa.mark_active()

    assert sa.name == "原名"
    new_arch = sa.close().archive

    assert new_arch is not None
    names = names_by_path(sa)
    assert names[new_arch] == "原名"
    assert names[arch] == "原名"


def test_rename_archive_with_empty_name_clears_it(tmp_path: Path) -> None:
    hf = tmp_path / "history.json"
    write_session(hf, "A")
    sa = SessionArchive(hf)
    arch = sa.close().archive
    assert arch is not None
    sa.rename_archive(arch, "x")
    sa.rename_archive(arch, "")

    assert names_by_path(sa)[arch] == ""


def test_delete_archive_removes_it_from_listing(tmp_path: Path) -> None:
    hf = tmp_path / "history.json"
    sa = SessionArchive(hf)
    write_session(hf, "keep")
    keep = sa.close().archive
    write_session(hf, "drop")
    drop = sa.close().archive
    assert drop is not None
    sa.rename_archive(drop, "x")

    sa.delete_archive(drop)

    assert [e.path for e in sa.list_archives()] == [keep]


# ── main 與 agent 各自獨立 ────────────────────────────────────────────────────


def test_sessions_of_different_history_files_are_independent(tmp_path: Path) -> None:
    main_hf = tmp_path / ".kai" / "history.json"
    agent_hf = tmp_path / "agents" / "writer" / "history.json"
    for hf in (main_hf, agent_hf):
        hf.parent.mkdir(parents=True)
        write_session(hf, str(hf.parent.name))
    agent_sa = SessionArchive(agent_hf)
    arch = agent_sa.close().archive
    assert arch is not None
    agent_sa.resume(arch)

    main_sa = SessionArchive(main_hf)
    assert main_sa.resumed_from is None
    assert main_sa.list_archives() == []
    assert SessionArchive(agent_hf).resumed_from == arch
