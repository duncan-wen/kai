import asyncio
import re

from loguru import logger

from .. import config as _cfg
from .result import ToolResult

BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash_exec",
        "description": (
            "Execute a bash command and return stdout/stderr. "
            "Use for file operations, running scripts, installing packages, checking system state. "
            "Prefer chaining commands with && over multiple tool calls."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Bash command to execute"},
            },
            "required": ["command"],
        },
    },
}

CLEAN_PATTERN = re.compile(
    r"\x1b\[[0-9;]*[mABCDHJKSTf]"
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
    r"|<\/?(?:think|thinking|scratchpad|internal)[^>]*>"
)

_SUDO_NONINTERACTIVE = (
    r'sudo(){ command sudo -n "$@"; }; export -f sudo 2>/dev/null; '
)


def clean_thinking(text: str) -> str:
    """移除 ANSI 跳脱碼、控制字元、常見隱藏標簽。"""
    return CLEAN_PATTERN.sub("", text)


async def bash_exec(command: str, timeout: int = 0) -> ToolResult:
    """執行 bash 指令，回傳結構化結果（stdout + stderr 與 exit code）。"""
    t = timeout or _cfg.BASH_TIMEOUT

    async def _collect(reader: asyncio.StreamReader, chunks: list[bytes]) -> None:
        try:
            while chunk := await reader.read(65536):
                chunks.append(chunk)
        except Exception:
            pass

    try:
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash", "-c", _SUDO_NONINTERACTIVE + command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        out_chunks: list[bytes] = []
        err_chunks: list[bytes] = []
        out_task = asyncio.create_task(_collect(proc.stdout, out_chunks))
        err_task = asyncio.create_task(_collect(proc.stderr, err_chunks))

        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), timeout=t)
        except asyncio.TimeoutError:
            timed_out = True
        finally:
            out_task.cancel()
            err_task.cancel()
            await asyncio.gather(out_task, err_task, return_exceptions=True)

        if timed_out:
            logger.debug(f"bash_exec timeout {t}s: {command[:60]}")
            return ToolResult("bash_exec", f"[Error] 逾時 {t}s", is_error=True)

        out = b"".join(out_chunks).decode(errors="replace").strip()
        err = b"".join(err_chunks).decode(errors="replace").strip()
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr]\n{err}" if proc.returncode != 0 else err)
        result = "\n".join(parts) or "(no output)"
        logger.debug(f"bash_exec exit={proc.returncode}: {command[:60]}")
        if proc.returncode != 0:
            result = f"[exit code {proc.returncode}]\n{result}"
        # exit code 語意：>= 2 視為錯誤、== 1 視為警告（有輸出但不致命）
        return ToolResult(
            "bash_exec",
            result,
            is_error=proc.returncode >= 2,
            exit_code=proc.returncode,
        )

    except Exception as e:
        return ToolResult("bash_exec", f"[Error] {e}", is_error=True)
