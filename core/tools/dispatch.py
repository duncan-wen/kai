from loguru import logger
from pydantic import ValidationError

from .result import ToolResult
from .web import WebSearchArgs

OPEN_AGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "open_agent",
        "description": (
            "Open a specialized sub-agent tab in the UI. "
            "Use when the user asks to switch to, open, or activate a named agent (e.g. '幫我開台股分析師'). "
            "Pass the agent's name or a descriptive keyword as the query."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Agent name, nickname, or description keyword",
                },
            },
            "required": ["query"],
        },
    },
}


def _mask_args(raw) -> dict:
    """遮罩敏感欄位，避免把 API key / token 等寫進追蹤。"""
    if not isinstance(raw, dict):
        return raw
    masked = {}
    for k, v in raw.items():
        if any(s in str(k).lower() for s in ("key", "secret", "token", "password", "api")):
            masked[k] = "***"
        else:
            masked[k] = v
    return masked


def _record_result(obs, tr: ToolResult) -> None:
    """把工具結果寫回 Langfuse observation；失敗時標 level=ERROR，才能在 UI 篩出錯誤。"""
    if obs is None:
        return
    if tr.is_error:
        obs.update(
            output={"result": tr.output[:1000]},
            level="ERROR",
            status_message=tr.output[:200],
        )
    else:
        obs.update(output={"result": tr.output[:1000]})


# subagent dispatch 工具：官方 multi-agent best practice 要求 type=agent（非 tool）
SUBAGENT_DISPATCH_TOOLS = {"send_to_agent"}


async def run_tool(tc, tools_map: dict, mcp_client=None) -> ToolResult:
    """由工具對映表動態分發；MCP tool 優先，其次本地 tools_map。

    回傳結構化的 :class:`ToolResult`，呼叫端不需再靠字串關鍵字判斷錯誤。
    """
    import json

    fn_name = tc.function.name
    raw_args = tc.function.arguments
    raw = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

    if fn_name in SUBAGENT_DISPATCH_TOOLS:
        from ..langfuse_client import agent

        subagent = str(raw.get("name", "unknown")) if isinstance(raw, dict) else "unknown"
        with agent(name=f"agent:{subagent}", input={"args": _mask_args(raw)}) as a:
            tr = await _dispatch(fn_name, raw, tools_map, mcp_client)
            _record_result(a, tr)
            return tr

    from ..langfuse_client import tool

    with tool(name=fn_name, input={"args": _mask_args(raw)}) as t:
        tr = await _dispatch(fn_name, raw, tools_map, mcp_client)
        _record_result(t, tr)
        return tr


async def _dispatch(fn_name: str, raw: dict, tools_map: dict, mcp_client) -> ToolResult:
    """實際分發邏輯（不含追蹤）。

    錯誤一律以結構化旗標回報，不再依賴輸出字串內容。
    工具若自行回傳 :class:`ToolResult`（例如 bash_exec 帶 exit code），原樣透傳。
    """
    if mcp_client and fn_name in mcp_client.tool_names:
        try:
            result = await mcp_client.call(fn_name, raw)
            return ToolResult(fn_name, result)
        except Exception as e:
            logger.error(f"MCP tool '{fn_name}' 呼叫失敗：{e}")
            return ToolResult(fn_name, f"[MCP Error] {e}", is_error=True)

    if fn_name not in tools_map:
        return ToolResult(fn_name, f"[Error] 未知工具：{fn_name}", is_error=True)

    fn = tools_map[fn_name]
    if fn_name == "web_search":
        try:
            args = WebSearchArgs.model_validate(raw)
        except ValidationError as e:
            error_msg = f"[ValidationError] {e.error_count()} 個錯誤:\n{e}"
            logger.warning(f"tool call 參數校驗失敗：{error_msg}")
            return ToolResult(fn_name, error_msg, is_error=True)
        try:
            return ToolResult(fn_name, await fn(args.query, args.num_results))
        except Exception as e:
            logger.error(f"tool '{fn_name}' 執行例外：{e}")
            return ToolResult(fn_name, f"[Error] {e}", is_error=True)

    try:
        out = await fn(**raw)
    except Exception as e:
        logger.error(f"tool '{fn_name}' 執行例外：{e}")
        return ToolResult(fn_name, f"[Error] {e}", is_error=True)

    if isinstance(out, ToolResult):
        return out
    return ToolResult(fn_name, str(out))
