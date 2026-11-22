from loguru import logger
from pydantic import ValidationError

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


async def run_tool(tc, tools_map: dict, mcp_client=None) -> tuple[str, str]:
    """由工具對映表動態分發；MCP tool 優先，其次本地 tools_map。"""
    import json

    from ..langfuse_client import tool

    fn_name = tc.function.name
    raw_args = tc.function.arguments
    raw = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

    with tool(name=fn_name, input={"args": _mask_args(raw)}) as t:
        fn, result = await _dispatch(fn_name, raw, tools_map, mcp_client)
        if t is not None:
            t.update(output={"result": str(result)[:1000]})
        return fn, result


async def _dispatch(fn_name: str, raw: dict, tools_map: dict, mcp_client) -> tuple[str, str]:
    """實際分發邏輯（不含追蹤）。"""
    if mcp_client and fn_name in mcp_client.tool_names:
        try:
            result = await mcp_client.call(fn_name, raw)
            return fn_name, result
        except Exception as e:
            logger.error(f"MCP tool '{fn_name}' 呼叫失敗：{e}")
            return fn_name, f"[MCP Error] {e}"

    if fn_name not in tools_map:
        return fn_name, f"[Error] 未知工具：{fn_name}"

    fn = tools_map[fn_name]
    if fn_name == "web_search":
        try:
            args = WebSearchArgs.model_validate(raw)
        except ValidationError as e:
            error_msg = f"[ValidationError] {e.error_count()} 個錯誤:\n{e}"
            logger.warning(f"tool call 參數校驗失敗：{error_msg}")
            return fn_name, error_msg
        return fn_name, await fn(args.query, args.num_results)

    return fn_name, await fn(**raw)
