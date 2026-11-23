"""tool_log.py — Tool call 除錯日誌，DEBUG_TOOLS=1 時才寫檔。

格式（tool_calls.log，append mode）：
  [YYYY-MM-DD HH:MM:SS.mmm] #N  tool_name
  ARGS:
    {json pretty-print}
  RESULT: (前 2000 字元，超過截斷)
  ────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import config as _cfg

_MAX_RESULT = 2000
_SEPARATOR = "─" * 64

_call_counters: dict[Path, int] = {}  # 每個 log 檔各自的 call counter
_session_written: set[Path] = set()  # 每個 log 檔各自記是否已寫過 header


def _log_path(agent_dir: "Path | None") -> Path:
    """決定 log 路徑：subagent → agent_dir/tool_calls.log；main chat → cwd/.kai/tool_calls.log。"""
    if agent_dir is not None:
        return agent_dir / "tool_calls.log"
    return Path(".kai/tool_calls.log")


def _write_session_header(p: Path) -> None:
    """每個 log 檔在 process 生命週期內只寫一次 session 分隔線。"""
    if p in _session_written:
        return
    _session_written.add(p)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n{'═' * 64}\n  SESSION START  {ts}\n{'═' * 64}\n"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(header)
    except Exception:
        pass


def log_turn_start(turn: int, agent_dir: "Path | None" = None) -> None:
    """記錄新的對話輪次開始（chat() 被呼叫）。"""
    if not _cfg.DEBUG_TOOLS:
        return
    p = _log_path(agent_dir)
    _write_session_header(p)
    ts = datetime.now().strftime("%H:%M:%S")
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(f"\n▶ Turn #{turn}  [{ts}]\n")
    except Exception:
        pass


def log_tool_call(
    tool_name: str, args: dict, result: str, agent_dir: "Path | None" = None
) -> None:
    """記錄單次 tool call 的名稱、參數、回應。DEBUG_TOOLS=0 時直接返回。"""
    if not _cfg.DEBUG_TOOLS:
        return
    p = _log_path(agent_dir)
    _call_counters[p] = _call_counters.get(p, 0) + 1
    n = _call_counters[p]

    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    args_str = json.dumps(args, ensure_ascii=False, indent=2)
    if len(result) > _MAX_RESULT:
        result_str = result[:_MAX_RESULT] + f"\n…（截斷，共 {len(result)} 字元）"
    else:
        result_str = result

    block = (
        f"[{ts}] #{n}  {tool_name}\n"
        f"ARGS:\n{args_str}\n"
        f"RESULT:\n{result_str}\n"
        f"{_SEPARATOR}\n"
    )
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(block)
    except Exception:
        pass
