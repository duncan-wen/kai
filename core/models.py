from dataclasses import dataclass, replace


@dataclass
class ModelProfile:
    name: str
    supports_thinking: bool = False
    supports_vision: bool = False
    supports_tools: bool = True
    num_ctx: int = 4096
    backend: str = "ollama"  # "ollama" | "gemini"


# 已安裝模型（RTX 3050 4 GB VRAM / 32 GB RAM）
# num_ctx 原則：KV cache 只要落在 RAM，就直接開大；真正在 VRAM 的才保守設定
#   deepseek-r1:1.5b  1.1GB → VRAM 充足             → 16384
#   qwen2.5-coder:3b  1.9GB → VRAM 充足             → 8192（tools=False，context 需求低）
#   qwen3.5:2b        2.7GB → KV 16384 會 spill RAM → 16384（32GB RAM，spill 無代價）
#   qwen3-vl:4b       3.3GB → KV 幾乎全 RAM         → 16384
#   qwen3.5:4b        3.4GB → KV 幾乎全 RAM         → 16384
#   gemma4:e2b        7.2GB → 全 CPU / RAM           → 32768
#   gemma4:e4b        9.6GB → 全 CPU / RAM           → 32768
REGISTRY: dict[str, ModelProfile] = {
    "deepseek-r1:1.5b": ModelProfile(
        "deepseek-r1:1.5b",
        supports_thinking=True,
        supports_vision=False,
        supports_tools=False,
        num_ctx=16384,
    ),
    "qwen2.5-coder:3b": ModelProfile(
        "qwen2.5-coder:3b",
        supports_thinking=False,
        supports_vision=False,
        supports_tools=False,  # Ollama 0.24 不解析此 template 的 tool_call，輸出為純文字
        num_ctx=8192,
    ),
    "qwen3.5:2b": ModelProfile(
        "qwen3.5:2b",
        supports_thinking=False,  # 2B 推理能力有限，thinking 只增加延遲
        supports_vision=True,
        supports_tools=True,
        num_ctx=16384,
    ),
    "qwen3-vl:4b": ModelProfile(
        "qwen3-vl:4b",
        supports_thinking=True,
        supports_vision=True,
        supports_tools=True,
        num_ctx=16384,  # KV 本來就溢出到 RAM，開大不多付代價
    ),
    "qwen3.5:4b": ModelProfile(
        "qwen3.5:4b",
        supports_thinking=True,
        supports_vision=True,
        supports_tools=True,
        num_ctx=16384,  # 同上
    ),
    "gemma4:e2b": ModelProfile(
        "gemma4:e2b",
        supports_thinking=True,
        supports_vision=True,
        supports_tools=True,
        num_ctx=32768,  # 7.2GB CPU only，KV cache 吃 RAM，不受 VRAM 限制
    ),
    "gemma4:e4b": ModelProfile(
        "gemma4:e4b",
        supports_thinking=True,
        supports_vision=True,
        supports_tools=True,
        num_ctx=32768,  # 9.6GB CPU only，同上
    ),
    # ── DeepSeek (DeepSeek API) ───────────────────────────────────────────────
    "deepseek-flash": ModelProfile(
        "deepseek-flash",
        supports_thinking=True,
        supports_vision=False,
        supports_tools=True,
        backend="deepseek",
    ),
    "deepseek-v4-pro": ModelProfile(
        "deepseek-v4-pro",
        supports_thinking=True,
        supports_vision=False,
        supports_tools=True,
        backend="deepseek",
    ),
    # ── Gemini (Google API) ──────────────────────────────────────────────────
    "gemini-2.0-flash": ModelProfile(
        "gemini-2.0-flash",
        supports_thinking=False,
        supports_vision=True,
        supports_tools=True,
        backend="gemini",
    ),
    "gemini-2.5-flash": ModelProfile(
        "gemini-2.5-flash",
        supports_thinking=True,
        supports_vision=True,
        supports_tools=True,
        backend="gemini",
    ),
    "gemini-2.5-pro": ModelProfile(
        "gemini-2.5-pro",
        supports_thinking=True,
        supports_vision=True,
        supports_tools=True,
        backend="gemini",
    ),
}


def get_profile(model: str) -> ModelProfile:
    """回傳模型的 profile，未登記的模型用預設值做安全備選。"""
    profile = REGISTRY.get(model, ModelProfile(model))
    return replace(profile)  # copy 避免 Agent.__init__ 修改 num_ctx 時汙染 REGISTRY
