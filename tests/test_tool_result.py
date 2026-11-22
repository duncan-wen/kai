"""core.tools.result / dispatch — 工具錯誤以結構化旗標回報（不再掃字串猜測）。

對應 docs/issues/tool-debug-display.md：
1. 誤塗紅：成功回傳的程式碼片段含 "Traceback" 字樣不應被判為錯誤。
2. 漏判：工具拋例外必須標記 is_error。
3. 不一致：streaming 與 agents 兩條路徑共用同一組旗標語意。
"""
import asyncio
import json


from core.tools.bash import bash_exec
from core.tools.dispatch import _dispatch, run_tool
from core.tools.result import ToolResult


class _FakeFn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeTC:
    def __init__(self, name, args):
        self.function = _FakeFn(name, json.dumps(args))


# ---------- ToolResult 語意 ----------

def test_tool_result_str_returns_output():
    tr = ToolResult("bash_exec", "hello")
    assert str(tr) == "hello"


def test_success_has_no_flags():
    tr = ToolResult("bash_exec", "ok", exit_code=0)
    assert tr.is_error is False
    assert tr.is_warn is False


def test_exit_code_1_is_warn_not_error():
    tr = ToolResult("bash_exec", "minor", exit_code=1)
    assert tr.is_error is False
    assert tr.is_warn is True


def test_exit_code_2_is_error():
    tr = ToolResult("bash_exec", "boom", is_error=True, exit_code=2)
    assert tr.is_error is True
    assert tr.is_warn is False


# ---------- bash_exec 回傳結構化結果 ----------

def test_bash_success_is_structured():
    tr = asyncio.run(bash_exec("echo hi"))
    assert isinstance(tr, ToolResult)
    assert tr.is_error is False
    assert tr.exit_code == 0
    assert "hi" in tr.output


def test_bash_exit_1_is_warn():
    tr = asyncio.run(bash_exec("exit 1"))
    assert tr.exit_code == 1
    assert tr.is_error is False
    assert tr.is_warn is True


def test_bash_exit_2_is_error():
    tr = asyncio.run(bash_exec("exit 2"))
    assert tr.exit_code == 2
    assert tr.is_error is True


# ---------- 回歸：不再用字串關鍵字誤判 ----------

def test_success_output_containing_traceback_is_not_error():
    """工具成功，但輸出含 Traceback 字樣（如 codegraph 回傳原始碼）→ 不得塗紅。"""
    tr = asyncio.run(bash_exec("printf 'Traceback (most recent call last):\\n  fake snippet\\n'"))
    assert tr.exit_code == 0
    assert tr.is_error is False, "成功的輸出含 Traceback 字樣不應被判為錯誤"


def test_success_output_containing_error_keywords_is_not_error():
    tr = asyncio.run(bash_exec("printf '[Error] [stderr] [MCP Error]\\n'"))
    assert tr.is_error is False


def test_failure_without_keywords_is_error():
    """失敗但訊息不含任何關鍵字 → 仍須標記為錯誤（舊邏輯會漏判）。"""
    tr = asyncio.run(bash_exec("exit 3"))
    assert tr.is_error is True


# ---------- dispatch 層 ----------

def test_dispatch_unknown_tool_is_error():
    tr = asyncio.run(_dispatch("nope", {}, {}, None))
    assert tr.is_error is True
    assert "未知工具" in tr.output


def test_dispatch_tool_exception_is_error():
    async def boom(**_):
        raise RuntimeError("kaboom")

    tr = asyncio.run(_dispatch("boom", {}, {"boom": boom}, None))
    assert tr.is_error is True
    assert "kaboom" in tr.output


def test_dispatch_passes_through_tool_result():
    async def tool(**_):
        return ToolResult("tool", "custom", is_error=True, exit_code=2)

    tr = asyncio.run(_dispatch("tool", {}, {"tool": tool}, None))
    assert tr.is_error is True
    assert tr.exit_code == 2


def test_dispatch_wraps_plain_string_tool():
    async def tool(**_):
        return "plain"

    tr = asyncio.run(_dispatch("tool", {}, {"tool": tool}, None))
    assert isinstance(tr, ToolResult)
    assert tr.output == "plain"
    assert tr.is_error is False


# ---------- run_tool 完整路徑 ----------

def test_run_tool_returns_tool_result():
    tc = _FakeTC("echo_tool", {"text": "yo"})

    async def echo_tool(text):
        return text

    tr = asyncio.run(run_tool(tc, {"echo_tool": echo_tool}))
    assert isinstance(tr, ToolResult)
    assert tr.fn_name == "echo_tool"
    assert tr.output == "yo"
