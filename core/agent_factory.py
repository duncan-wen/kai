"""Agent Factory — /create-agent, /load-agent, /list-agents 的核心邏輯。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator

REQUIRED_FIELDS = {"name", "model", "system_prompt"}

DEFAULT_TTS_VOICE = "en-US-AriaNeural"


class AgentDefinition(BaseModel):
    """磁碟上 agents/<name>/config.json 的內容；開啟或 reload 時套用到 Agent。"""

    name: str
    model: str
    system_prompt: str
    nickname: str | None = None
    description: str = ""
    greeting: str = ""
    history_keep: int | None = None  # None = 用全域 HISTORY_KEEP
    datasets: list[str] = []
    mcp_servers: dict | None = None  # {} = 不用 MCP；None 或非空 = 共用 main agent 的 MCP
    tts: dict | None = None

    @field_validator("tts", mode="before")
    @classmethod
    def _normalize_tts(cls, raw: Any) -> dict | None:
        """True → 預設語音；非空 dict → 原樣；其他（False、{}、None）→ 不啟用。"""
        if raw is True:
            return {"voice": DEFAULT_TTS_VOICE}
        if isinstance(raw, dict) and raw:
            return raw
        return None

    @property
    def display_name(self) -> str:
        return self.nickname or self.name

GLOBAL_AGENTS_DIR = Path.home() / ".kai" / "agents"

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(*filenames: str) -> str:
    """載入一或多個 prompt 檔並拼接。"""
    return "\n\n".join(
        (_PROMPTS_DIR / fn).read_text(encoding="utf-8").rstrip() for fn in filenames
    )


def _apply_template(template: str, **kwargs: str) -> str:
    """簡單占位符替換：{key} → value。不使用 .format()，避免與 JSON 大括號衝突。"""
    for key, val in kwargs.items():
        template = template.replace(f"{{{key}}}", val)
    return template


DESIGN_SYSTEM_PROMPT = _load_prompt("design_name.md", "design_conditions.md")
_DESIGN_BASE = _load_prompt("design_full.md", "design_conditions.md")
DESIGN_FULL_PROMPT = _DESIGN_BASE


async def _call_backend(design_model: str, messages: list[dict]) -> str:
    """直接呼叫 ollama / openai-compat backend，回傳原始文字。"""
    import core.config as _cfg
    from core.models import get_profile
    profile = get_profile(design_model)
    if profile.backend == "ollama":
        import ollama
        resp = await ollama.AsyncClient().chat(model=profile.name, messages=messages)
        return resp.message.content or ""
    from openai import AsyncOpenAI
    if profile.backend == "gemini":
        client = AsyncOpenAI(
            api_key=_cfg.GEMINI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    elif profile.backend == "deepseek":
        client = AsyncOpenAI(api_key=_cfg.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    else:
        raise ValueError(f"未知 backend：{profile.backend!r}，請確認 REGISTRY 設定")
    resp = await client.chat.completions.create(model=profile.name, messages=messages)
    return resp.choices[0].message.content or ""


async def call_full_design(description: str) -> dict[str, Any]:
    """用 CREATE_AGENT_MODEL 從自由文字描述生成完整 agent config dict。"""
    import core.config as _cfg
    from core.models import REGISTRY
    design_model = _cfg.CREATE_AGENT_MODEL or _cfg.MODEL
    model_list = ", ".join(REGISTRY.keys())
    prompt = _apply_template(
        _DESIGN_BASE, description=description, models=model_list, default_model=_cfg.MODEL,
    )
    raw = await _call_backend(design_model, [{"role": "user", "content": prompt}])
    try:
        return extract_json(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"設計模型回傳非 JSON 格式：{e}\n原始回覆：{raw[:300]}") from e


async def call_design_model(name: str, description: str) -> tuple[str, str, dict]:
    """用 CREATE_AGENT_MODEL 生成 agent 設定，回傳 (system_prompt, greeting, extras)。
    extras 可能包含 datasets、tts 等 LLM 自行判斷需要的條件欄位。
    """
    import core.config as _cfg
    design_model = _cfg.CREATE_AGENT_MODEL or _cfg.MODEL
    prompt = _apply_template(DESIGN_SYSTEM_PROMPT, name=name, description=description or name)
    raw = await _call_backend(design_model, [{"role": "user", "content": prompt}])
    try:
        data = extract_json(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"設計模型回傳非 JSON 格式：{e}\n原始回覆：{raw[:300]}") from e
    extras = {k: data[k] for k in ("datasets", "tts") if k in data}
    return data.get("system_prompt", ""), data.get("greeting", ""), extras


def _slugify(name: str) -> str:
    """將 agent 名稱轉為合法目錄名（保留中文、移除特殊字元）。"""
    slug = name.strip().replace(" ", "-").replace("/", "-")
    slug = re.sub(r'[<>:"/\\|?*]', "-", slug)
    return slug or "agent"


def local_agents_dir() -> Path:
    """cwd/agents/（專案專屬，可 commit）。"""
    d = Path.cwd() / "agents"
    d.mkdir(exist_ok=True)
    return d


def global_agents_dir() -> Path:
    """~/.kai/agents/（跨專案共用）。"""
    d = GLOBAL_AGENTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def agents_dir() -> Path:
    """向後相容別名，回傳 local agents 目錄。"""
    return local_agents_dir()


def agent_dir(name: str, global_scope: bool = False) -> Path:
    """回傳 agent 目錄（不存在則建立）。"""
    base = global_agents_dir() if global_scope else local_agents_dir()
    d = base / _slugify(name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_agents() -> list[dict[str, Any]]:
    """列出 local（cwd/agents/）和 global（~/.kai/agents/）的所有 agent。

    回傳清單中每筆有 scope: "local" | "global"。
    local 排在前面；同一 scope 內按目錄名排序。
    """
    result = []
    for scope, base in (("local", Path.cwd() / "agents"), ("global", GLOBAL_AGENTS_DIR)):
        if not base.exists():
            continue
        for p in sorted(base.iterdir()):
            if not p.is_dir():
                continue
            cfg = p / "config.json"
            if not cfg.exists():
                continue
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                result.append({
                    "dir": p.name,
                    "scope": scope,
                    "name": data.get("name", p.name),
                    "nickname": data.get("nickname", ""),
                    "model": data.get("model", ""),
                    "description": data.get("description", ""),
                })
            except Exception:
                pass
    return result


def _name_matches(a: dict[str, Any], q: str) -> bool:
    """精確或 substring 比對 name / nickname（不含 dir）。"""
    name = a["name"].lower()
    nick = (a["nickname"] or "").lower()
    return q == name or q == nick or q in name or bool(nick and q in nick)


def find_all_agents(query: str) -> list[dict[str, Any]]:
    """回傳所有符合 query 的 agent dict（含 scope 欄位），local 排前面。

    比對：精確 dir 名、精確/substring name 或 nickname。
    """
    q = query.strip().lower()
    return [a for a in list_agents() if a["dir"].lower() == q or _name_matches(a, q)]


def find_agent(query: str) -> str | None:
    """模糊比對 agent，回傳 "local:<dir>" 或 "global:<dir>"；找不到回傳 None。

    比對優先順序：
      1. 精確 dir 名
      2. 精確 name 或 nickname
      3. name / nickname 包含 query（substring）
      4. query 包含 name / nickname
      5. description 包含 query
    同優先級內 local 優先。
    """
    q = query.strip().lower()
    agents = list_agents()

    checks = [
        lambda a: a["dir"].lower() == q,
        lambda a: a["name"].lower() == q or (a["nickname"] or "").lower() == q,
        lambda a: q in a["name"].lower() or bool(a["nickname"] and q in a["nickname"].lower()),
        lambda a: a["name"].lower() in q or bool(a["nickname"] and a["nickname"].lower() in q),
        lambda a: bool(a.get("description") and q in a["description"].lower()),
    ]
    for check in checks:
        for scope in ("local", "global"):
            for a in agents:
                if a["scope"] == scope and check(a):
                    return f"{scope}:{a['dir']}"
    return None


def find_agent_dir(name: str) -> Path:
    """找出 agent 目錄。

    name 可為：
    - "local:<dir>"  / "global:<dir>"  — 指定 scope
    - 純 dir 名稱 — 搜尋 local 後再 global
    """
    scope: str | None = None
    if name.startswith("local:"):
        scope, name = "local", name[6:]
    elif name.startswith("global:"):
        scope, name = "global", name[7:]

    bases: list[Path] = []
    if scope == "local" or scope is None:
        bases.append(Path.cwd() / "agents")
    if scope == "global" or scope is None:
        bases.append(GLOBAL_AGENTS_DIR)

    for base in bases:
        for candidate in (name, _slugify(name), name.lower().replace(" ", "-")):
            if (base / candidate / "config.json").exists():
                return base / candidate

    raise FileNotFoundError(
        f"找不到 agent：{name}（搜尋：{[str(b) for b in bases]}）"
    )


def read_definition(config_path: Path) -> AgentDefinition:
    """讀取 config.json 成 AgentDefinition；格式錯誤時 raise ValueError。"""
    text = config_path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = json.loads(_escape_json_strings(text))
    return AgentDefinition.model_validate(data)


def load_agent(name: str) -> tuple[AgentDefinition, Path]:
    """依名稱找到 agent 目錄並讀取它的 definition。"""
    adir = find_agent_dir(name)
    return read_definition(adir / "config.json"), adir


def save_agent(data: dict[str, Any], global_scope: bool = False) -> Path:
    """儲存 agent config；首次建立時同步寫入初始 history.json。"""
    d = agent_dir(data["name"], global_scope=global_scope)
    system_prompt_content = data["system_prompt"]
    if system_prompt_content != "{{include:system_prompt.md}}":
        (d / "system_prompt.md").write_text(system_prompt_content, encoding="utf-8")
        data = {**data, "system_prompt": "{{include:system_prompt.md}}"}
    (d / "config.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    history_file = d / "history.json"
    if not history_file.exists():
        history_file.write_text(
            json.dumps(
                [{"role": "system", "content": system_prompt_content}],
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
    return d


def delete_agent(scoped_name: str) -> str:
    """刪除 agent 目錄（含 config.json / history.json），回傳 agent 顯示名稱。"""
    import shutil
    adir = find_agent_dir(scoped_name)
    try:
        display_name = json.loads((adir / "config.json").read_text(encoding="utf-8")).get("name", adir.name)
    except Exception:
        display_name = adir.name
    shutil.rmtree(adir)
    return display_name


def validate_agent(data: dict[str, Any]) -> list[str]:
    """回傳缺少的必要欄位清單。"""
    return [f for f in REQUIRED_FIELDS if not data.get(f)]


def _escape_json_strings(text: str) -> str:
    """將 JSON 字串值內的 literal 控制字元轉為轉義序列。"""
    result: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if escaped:
            result.append(ch)
            escaped = False
        elif ch == "\\" and in_string:
            result.append(ch)
            escaped = True
        elif ch == '"':
            result.append(ch)
            in_string = not in_string
        elif in_string and ch == "\n":
            result.append("\\n")
        elif in_string and ch == "\r":
            result.append("\\r")
        elif in_string and ch == "\t":
            result.append("\\t")
        else:
            result.append(ch)
    return "".join(result)


def extract_json(text: str) -> dict[str, Any]:
    """從 LLM 回覆中提取 JSON（處理 markdown code block 與字串內控制字元）。"""
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_escape_json_strings(text))
