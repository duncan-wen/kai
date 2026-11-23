"""Type stubs for core.config — auto-mirrors config_registry.REGISTRY."""

from pathlib import Path

# 不可設定的計算屬性
KAI_DIR: Path
HISTORY_FILE: Path
BUNDLED_MCP_FILE: Path

# 模型
MODEL: str
CREATE_AGENT_MODEL: str
COMPACT_MODEL: str

# 生成參數
NUM_CTX: int
TEMPERATURE: float
TOP_K: int
TOP_P: float

# 對話與工具
HISTORY_KEEP: int
MAX_TOOL_CALLS: int
BASH_TIMEOUT: int
DEBUG_TOOLS: bool
USERNAME: str

# 搜尋
SEARXNG_URL: str
SEARCH_NUM_RESULTS: int
SEARCH_TIMEOUT: float
SEARCH_RETRIES: int
MAX_SEARCH_CHARS: int
FETCH_MAX_CHARS: int

# 逾時
OLLAMA_STREAM_TIMEOUT: int
MCP_TOOL_TIMEOUT: float

# Dify RAG
DIFY_API_URL: str
DIFY_TOP_K: int
DIFY_RERANK_PROVIDER: str
DIFY_RERANK_MODEL: str

# API 金鑰（只能在 .env 設定）
GEMINI_API_KEY: str
DEEPSEEK_API_KEY: str
JINA_API_KEY: str
AZURE_TTS_KEY: str
AZURE_TTS_REGION: str
AZURE_TTS_ENDPOINT: str
DIFY_API_KEY: str

def get_nickname(model: str) -> str: ...
def ensure_kai_dir() -> Path: ...
def reload_env() -> None: ...
def load_kai_config() -> dict: ...
def current_kai_config() -> dict: ...
def apply_kai_config(cfg: dict) -> None: ...
