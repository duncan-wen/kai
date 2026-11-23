"""Config registry — 所有可設定項目的唯一來源。

新增設定只需在此文件加一筆 ConfigEntry，
_apply_env / current_kai_config / apply_kai_config / config_screen 全部自動更新。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ConfigEntry:
    key: str  # config.json / apply_kai_config key（snake_case）
    env: str  # 環境變數名稱（UPPER_CASE）
    default: Any  # 預設傀
    type: type  # int | float | str | bool
    unit: str  # /init-config 顯示單位
    desc: str  # /init-config 顯示說明
    api_key: bool = False  # True = 只能放 .env，不導出到 config.json


REGISTRY: list[ConfigEntry] = [
    # ── 模型 ─────────────────────────────────────────────────────────
    ConfigEntry("model", "MODEL", "qwen3-vl:4b", str, "str", "使用的模型"),
    ConfigEntry(
        "create_agent_model",
        "CREATE_AGENT_MODEL",
        "",
        str,
        "str",
        "/create-agent 設計模型（空=同 model）",
    ),
    ConfigEntry(
        "compact_model",
        "COMPACT_MODEL",
        "",
        str,
        "str",
        "/compact 壓縮模型（空=自動選擇）",
    ),
    # ── 生成參數 ────────────────────────────────────────────────────
    ConfigEntry(
        "num_ctx", "NUM_CTX", 0, int, "int", "Context window 大小（0=模型預設）"
    ),
    ConfigEntry(
        "temperature", "TEMPERATURE", 0.6, float, "0-1", "生成溫度（低=確定，高=創意）"
    ),
    ConfigEntry("top_k", "TOP_K", 20, int, "int", "Top-K 採樣（0=停用）"),
    ConfigEntry("top_p", "TOP_P", 0.95, float, "0-1", "Nucleus sampling"),
    # ── 對話與工具 ───────────────────────────────────────────────
    ConfigEntry(
        "history_keep", "HISTORY_KEEP", 10, int, "pair", "對話記憶保留幾個來回"
    ),
    ConfigEntry("max_tool_calls", "MAX_TOOL_CALLS", 15, int, "int", "每輪工具呼叫上限"),
    ConfigEntry("bash_timeout", "BASH_TIMEOUT", 30, int, "秒", "bash_exec 指令逾時"),
    ConfigEntry(
        "debug_tools", "DEBUG_TOOLS", False, bool, "bool", "顯示工具呼叫除錯卡片"
    ),
    ConfigEntry("username", "USERNAME", "你", str, "str", "對話中顯示的使用者名稱"),
    # ── 搜尋 ─────────────────────────────────────────────────────────
    ConfigEntry(
        "searxng_url",
        "SEARXNG_URL",
        "http://localhost:8888/search",
        str,
        "url",
        "SearXNG 搜尋 API 位址",
    ),
    ConfigEntry(
        "search_num_results", "SEARCH_NUM_RESULTS", 5, int, "int", "每次搜尋回傳筆數"
    ),
    ConfigEntry("search_timeout", "SEARCH_TIMEOUT", 8.0, float, "秒", "搜尋請求逾時"),
    ConfigEntry("search_retries", "SEARCH_RETRIES", 2, int, "int", "搜尋失敗重試次數"),
    ConfigEntry(
        "max_search_chars", "MAX_SEARCH_CHARS", 8000, int, "字元", "搜尋結果總字元上限"
    ),
    ConfigEntry(
        "fetch_max_chars",
        "FETCH_MAX_CHARS",
        12000,
        int,
        "字元",
        "fetch_url 單頁回傳上限",
    ),
    # ── 逾時 ─────────────────────────────────────────────────────────
    ConfigEntry(
        "ollama_stream_timeout",
        "OLLAMA_STREAM_TIMEOUT",
        120,
        int,
        "秒",
        "Ollama 每個 chunk 最長等待",
    ),
    ConfigEntry(
        "mcp_tool_timeout",
        "MCP_TOOL_TIMEOUT",
        60.0,
        float,
        "秒",
        "MCP 單次工具呼叫逾時",
    ),
    # ── Dify RAG ───────────────────────────────────────────────────
    ConfigEntry(
        "dify_api_url",
        "DIFY_API_URL",
        "http://localhost",
        str,
        "url",
        "Dify 伺服器位址",
    ),
    ConfigEntry("dify_top_k", "DIFY_TOP_K", 5, int, "int", "Dify RAG 回傳段落數"),
    ConfigEntry(
        "dify_rerank_provider",
        "DIFY_RERANK_PROVIDER",
        "xinference",
        str,
        "str",
        "Dify rerank 供應商",
    ),
    ConfigEntry(
        "dify_rerank_model", "DIFY_RERANK_MODEL", "", str, "str", "Dify rerank 模型名稱"
    ),
    # ── 可觀測性（Langfuse） ───────────────────────────────────────
    ConfigEntry(
        "langfuse_trace_system_prompt",
        "LANGFUSE_TRACE_SYSTEM_PROMPT",
        True,
        bool,
        "bool",
        "trace 記錄完整 system prompt（每個 turn 僅第一輪）",
    ),
    ConfigEntry(
        "langfuse_tracing_environment",
        "LANGFUSE_TRACING_ENVIRONMENT",
        "development",
        str,
        "str",
        "Langfuse environment（區隔開發與正式資料）",
    ),
    # ── API 金鑰（僅 .env） ─────────────────────────────────────────
    ConfigEntry(
        "gemini_api_key",
        "GEMINI_API_KEY",
        "",
        str,
        "—",
        "Google Gemini API key",
        api_key=True,
    ),
    ConfigEntry(
        "deepseek_api_key",
        "DEEPSEEK_API_KEY",
        "",
        str,
        "—",
        "DeepSeek API key",
        api_key=True,
    ),
    ConfigEntry(
        "jina_api_key",
        "JINA_API_KEY",
        "",
        str,
        "—",
        "Jina Reader API key",
        api_key=True,
    ),
    ConfigEntry(
        "azure_tts_key",
        "AZURE_TTS_KEY",
        "",
        str,
        "—",
        "Azure TTS subscription key",
        api_key=True,
    ),
    ConfigEntry(
        "azure_tts_region",
        "AZURE_TTS_REGION",
        "",
        str,
        "—",
        "Azure region（例如 eastasia）",
        api_key=True,
    ),
    ConfigEntry(
        "azure_tts_endpoint",
        "AZURE_TTS_ENDPOINT",
        "",
        str,
        "—",
        "自訂 Azure TTS endpoint",
        api_key=True,
    ),
    ConfigEntry(
        "dify_api_key",
        "DIFY_API_KEY",
        "",
        str,
        "—",
        "Dify Knowledge Base API key",
        api_key=True,
    ),
    ConfigEntry(
        "langfuse_public_key",
        "LANGFUSE_PUBLIC_KEY",
        "",
        str,
        "—",
        "Langfuse public key",
        api_key=True,
    ),
    ConfigEntry(
        "langfuse_secret_key",
        "LANGFUSE_SECRET_KEY",
        "",
        str,
        "—",
        "Langfuse secret key",
        api_key=True,
    ),
    ConfigEntry(
        "langfuse_base_url",
        "LANGFUSE_BASE_URL",
        "",
        str,
        "—",
        "Langfuse base URL（預設 cloud）",
        api_key=True,
    ),
]

# 快速查詢用
REGISTRY_BY_KEY = {e.key: e for e in REGISTRY}
REGISTRY_BY_ENV = {e.env: e for e in REGISTRY}
