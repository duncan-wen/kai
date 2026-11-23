import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# kai 資料目錄（相對 cwd，跟著 os.chdir 變）
KAI_DIR = Path(".kai")
# kai package 目錄（全域，不跟著 cwd 走）
_KAI_PKG_DIR = Path(__file__).parent.parent
HISTORY_FILE = Path(os.getenv("HISTORY_FILE", str(KAI_DIR / "history.json")))
# bundled mcp_servers.json（隨 package 走），僅供首次啟動複製到 ~/.kai/ 用
BUNDLED_MCP_FILE = _KAI_PKG_DIR / "mcp_servers.json"


def _apply_env(g: dict) -> None:
    """從 REGISTRY 讀取環境變數並套用到 globals。
    新增設定只需在 core/config_registry.py 加一筆 ConfigEntry。
    """
    from .config_registry import REGISTRY

    for entry in REGISTRY:
        raw = os.getenv(entry.env)
        if raw is None:
            g[entry.env] = entry.default
        elif entry.type is bool:
            g[entry.env] = raw.strip().lower() in ("1", "true", "yes")
        elif entry.type is int:
            g[entry.env] = int(raw)
        elif entry.type is float:
            g[entry.env] = float(raw)
        else:
            g[entry.env] = raw


_apply_env(globals())


def _normalize(model: str) -> str:
    """qwen3-vl:4b → qwen3_vl_4b（供 NICKNAME_ 查找用）"""
    import re

    return re.sub(r"[^a-zA-Z0-9]", "_", model)


def get_nickname(model: str) -> str:
    """回傳 .env 設定的 NICKNAME_<model>；若無則回傳原名。"""
    return os.getenv(f"NICKNAME_{_normalize(model)}", model)


def ensure_kai_dir() -> Path:
    """確保 cwd/.kai/ 存在，回傳路徑。每次 os.chdir 後需重新呼叫。"""
    d = Path(".kai")
    d.mkdir(exist_ok=True)
    return d


def reload_env() -> None:
    """重新讀取 .env 並更新 module globals（支援 /reload-config 熱套用）。"""
    load_dotenv(override=True)
    _apply_env(globals())


def load_kai_config() -> dict:
    """讀取 ~/.kai/config.json（全域）+ cwd/.kai/config.json（專案），shallow merge。
    專案設定優先；兩者皆不存在時回傳 {}。
    """

    def _read(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    global_cfg = _read(Path.home() / ".kai" / "config.json")
    local_cfg = _read(Path(".kai") / "config.json")
    return {**global_cfg, **local_cfg}


def config_sources() -> dict[str, str]:
    """回傳每個 config key 的讀取來源：'local' / 'global' / '.env'。"""
    from .config_registry import REGISTRY

    g = globals()

    def _read(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    local = _read(Path(".kai") / "config.json")
    global_ = _read(Path.home() / ".kai" / "config.json")

    sources: dict[str, str] = {}
    for entry in REGISTRY:
        if entry.api_key:
            continue
        current = g[entry.env]

        def _matches(raw: object) -> bool:
            try:
                return entry.type(raw) == current
            except (ValueError, TypeError):
                return False

        if entry.key in local and _matches(local[entry.key]):
            sources[entry.key] = "local"
        elif entry.key in global_ and _matches(global_[entry.key]):
            sources[entry.key] = "global"
        else:
            sources[entry.key] = ".env"
    return sources


def current_kai_config() -> dict:
    """回傳目前所有生效的 config 傀（可寫入 config.json 的 key）。"""
    from .config_registry import REGISTRY

    g = globals()
    return {e.key: g[e.env] for e in REGISTRY if not e.api_key}


def apply_kai_config(cfg: dict) -> None:
    """將 config dict 套用到 module globals。API key 類不支援（留在 .env）。"""
    from .config_registry import REGISTRY

    g = globals()
    key_map = {e.key: e for e in REGISTRY if not e.api_key}
    for key, val in cfg.items():
        if key not in key_map:
            continue
        entry = key_map[key]
        if entry.type is bool:
            g[entry.env] = (
                bool(val)
                if isinstance(val, bool)
                else str(val).lower() in ("1", "true", "yes")
            )
        elif entry.type is int:
            g[entry.env] = int(val)
        elif entry.type is float:
            g[entry.env] = float(val)
        else:
            g[entry.env] = str(val)


# 只輸出
# 只輸出 DEBUG 以上的 log 到 stderr，不干擾 stdout 的對話輸出
logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <5}</level> | {message}",
)
