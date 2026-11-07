"""Session archive — 一個 history 檔的 session 封存生命週期。

詞彙見 GLOSSARY.md：Session、Archive、Resumed session。
所有狀態都以磁碟為準；不依賴 Textual 或任何 UI。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from loguru import logger


@dataclass(frozen=True)
class CloseResult:
    """close() 的結果：archived＝已封存成新 archive；discarded＝resumed session 直接捨棄；empty＝沒有對話。"""

    kind: Literal["archived", "discarded", "empty"]
    archive: Path | None = None


@dataclass(frozen=True)
class ArchiveEntry:
    """list_archives() 的一筆：給 /resume 畫面顯示用。"""

    path: Path
    timestamp: str  # "YYYY-MM-DD HH:MM"
    message_count: int  # user 訊息數
    preview: str  # 最後一則 user 訊息的前 50 字
    name: str


def _text_of(content: object) -> str:
    """取出訊息 content 的文字；多模態 content（list）取第一個 text 區塊。"""
    if isinstance(content, list):
        return next(
            (
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ),
            "",
        )
    return str(content or "")


def _timestamp_of(archive: Path) -> str:
    """從 history_YYYYMMDD_HHMMSS[_N].json 取出 "YYYY-MM-DD HH:MM"。"""
    dt = archive.stem[8:]
    return f"{dt[:4]}-{dt[4:6]}-{dt[6:8]} {dt[9:11]}:{dt[11:13]}"


class SessionStore:
    """以一個 history 檔為單位，管理其 session 與 `.history/` 下的 archive。"""

    def __init__(self, history_file: Path) -> None:
        self._file = Path(history_file).resolve()
        self._dir = self._file.parent / ".history"

    # ── 狀態 ─────────────────────────────────────────────────────────────────

    @property
    def resumed_from(self) -> Path | None:
        """目前 session 若是 resumed session，回傳來源 archive；否則 None。"""
        try:
            name = self._marker.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not name:
            return None
        archive = self._dir / name
        # 來源 archive 已不存在 → 視為一般 session，避免 close 時把對話直接刪掉
        return archive if archive.exists() else None

    @property
    def name(self) -> str:
        """session 名稱；resumed session 未另外命名時沿用來源 archive 的名稱。"""
        own = self._read_current_name()
        if own:
            return own
        resumed = self.resumed_from
        return self._read_names().get(resumed.name, "") if resumed else ""

    def rename(self, name: str) -> None:
        """為 session 命名；尚未有新對話的 resumed session 直接改來源 archive 的名稱。"""
        resumed = self.resumed_from
        if resumed is not None:
            self.rename_archive(resumed, name)
            self._current_name_file.unlink(missing_ok=True)
        else:
            self._write_current_name(name)

    def mark_active(self) -> None:
        """session 多了一則非 ephemeral 的新訊息：不再是 resumed session（沿用來源 archive 的名稱）。"""
        resumed = self.resumed_from
        if resumed is not None and not self._read_current_name():
            self._write_current_name(self._read_names().get(resumed.name, ""))
        self._marker.unlink(missing_ok=True)

    # ── 生命週期 ─────────────────────────────────────────────────────────────

    def close(self) -> CloseResult:
        """結束目前 session：一般 session 封存成新 archive，resumed session 直接捨棄。"""
        resumed = self.resumed_from
        own_name = self._read_current_name()
        if resumed is not None:
            if own_name:
                self.rename_archive(resumed, own_name)
            self._file.unlink(missing_ok=True)
            self._marker.unlink(missing_ok=True)
            self._current_name_file.unlink(missing_ok=True)
            return CloseResult("discarded", resumed)
        if not self._has_conversation():
            return CloseResult("empty")
        self._dir.mkdir(exist_ok=True)
        archive = self._new_archive_path()
        self._file.rename(archive)
        self._marker.unlink(missing_ok=True)  # 來源 archive 已不存在時殘留的標記
        if own_name:
            self.rename_archive(archive, own_name)
        self._current_name_file.unlink(missing_ok=True)
        return CloseResult("archived", archive)

    def resume(self, archive: Path) -> CloseResult:
        """先 close 目前 session，再把 archive 複製回來成為 resumed session。"""
        if not archive.exists():
            raise FileNotFoundError(archive)
        result = self.close()
        self._current_name_file.unlink(missing_ok=True)
        shutil.copy2(archive, self._file)
        self._dir.mkdir(exist_ok=True)
        self._marker.write_text(archive.name, encoding="utf-8")
        return result

    # ── archive 管理 ─────────────────────────────────────────────────────────

    def list_archives(self) -> list[ArchiveEntry]:
        """列出所有 archive，最新的在前。"""
        if not self._dir.exists():
            return []
        names = self._read_names()
        entries: list[ArchiveEntry] = []
        for p in sorted(self._dir.glob("history_*.json"), reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                users = [
                    m for m in data if isinstance(m, dict) and m.get("role") == "user"
                ]
                count = len(users)
                preview = _text_of(users[-1].get("content")) if users else ""
                preview = preview[:50].replace("\n", " ")
                stamp = _timestamp_of(p)
            except (OSError, ValueError, TypeError):
                logger.warning(f"session archive：無法解析 {p.name}")
                count, preview, stamp = 0, "(無法讀取)", p.name
            entries.append(
                ArchiveEntry(
                    path=p,
                    timestamp=stamp,
                    message_count=count,
                    preview=preview,
                    name=names.get(p.name, ""),
                )
            )
        return entries

    def rename_archive(self, archive: Path, name: str) -> None:
        """為 archive 命名；空字串表示清除名稱。"""
        names = self._read_names()
        if name:
            names[archive.name] = name
        else:
            names.pop(archive.name, None)
        self._write_names(names)

    def delete_archive(self, archive: Path) -> None:
        """刪除一個 archive；若目前 session 正是從它 resume 來的，session 改視為一般 session。"""
        if self.resumed_from == archive:
            self.mark_active()
        archive.unlink(missing_ok=True)
        names = self._read_names()
        if names.pop(archive.name, None) is not None:
            self._write_names(names)

    # ── internals ────────────────────────────────────────────────────────────

    @property
    def _marker(self) -> Path:
        return self._dir / ".resumed_from"

    @property
    def _names_file(self) -> Path:
        return self._dir / ".names.json"

    @property
    def _current_name_file(self) -> Path:
        return self._dir / ".current_name"

    def _read_names(self) -> dict[str, str]:
        try:
            data = json.loads(self._names_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_names(self, names: dict[str, str]) -> None:
        self._dir.mkdir(exist_ok=True)
        self._names_file.write_text(
            json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _read_current_name(self) -> str:
        try:
            return self._current_name_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _write_current_name(self, name: str) -> None:
        if not name:
            self._current_name_file.unlink(missing_ok=True)
            return
        self._dir.mkdir(exist_ok=True)
        self._current_name_file.write_text(name, encoding="utf-8")

    def _has_conversation(self) -> bool:
        """history 檔中是否有任何非 system 訊息。"""
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return any(isinstance(m, dict) and m.get("role") != "system" for m in data)

    def _new_archive_path(self) -> Path:
        """產生不與既有 archive 衝突的檔名：history_<ts>[_N].json。"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive = self._dir / f"history_{ts}.json"
        n = 1
        while archive.exists():  # 同秒多次封存時避免覆蓋
            archive = self._dir / f"history_{ts}_{n}.json"
            n += 1
        return archive
