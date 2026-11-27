# 設定參考

agent `config.json`、TTS、環境變數、MCP 與 SearXNG 的完整欄位。

## Agent config (`agents/<name>/config.json`)

Each agent directory contains a `config.json`. All fields:

| 欄位 | 必填 | 說明 |
|------|------|------|
| `name` | ✓ | Agent 名稱（目錄名） |
| `nickname` | | 顯示暱稱（tab 標題、header） |
| `model` | ✓ | 使用的模型（需在 REGISTRY 內） |
| `system_prompt` | ✓ | System prompt 全文；支援模板變數（見下方） |
| `description` | | 一行說明，`/list-agents` 顯示 |
| `greeting` | | 全新對話時 AI 主動說第一句話的 prompt |
| `history_keep` | | 記住幾個來回（pair）；預設用 env `HISTORY_KEEP=10` |
| `mcp_servers` | | 設為 `{}` 則此 agent 不使用 MCP；省略或非空則共用 main agent 的 MCP client（尚未支援 agent 專屬 server） |
| `tts` | | TTS 設定（見下表）；省略則不啟用語音 |

### `system_prompt` 模板變數

`_resolve_prompt()` in `Agent` expands these before the prompt is sent:

| 變數 | 展開為 |
|------|--------|
| `{AGENT_DIR}` | agent 目錄的絕對路徑 |
| `{CWD}` | 目前工作目錄 |
| `{DATE}` | 今天日期 `YYYY-MM-DD` |
| `{{include:filename}}` | 載入 `<agent_dir>/filename` 的文字內容 |

展開時機：`/load-agent`、`/list-agents` 選擇、`/reload-agent`、`/cd`。

```json
{
  "system_prompt": "你的工作目錄是 {AGENT_DIR}。\n\n以下是 API 文件：\n{{include:api_docs.md}}"
}
```

### `tts` 欄位

| 欄位 | 預設 | 說明 |
|------|------|------|
| `engine` | `"edge-tts"` | `"edge-tts"` / `"melotts"` / `"kokoro-api"` / `"kokoro"` / `"azure-tts"` |
| `voice` | 依 engine | 見下方 voice 表 |
| `lang` | `"en-us"` | 見下方 lang 表 |
| `player` | `"auto"` | `"auto"` / `"mpg123"` / `"paplay"` / `"ffplay"` / `"mpv"` |
| `host` | `"localhost"` | Docker server 位址（melotts / kokoro-api 用） |
| `port` | 8886 | melotts=8886，kokoro-api=8885 |
| `speed` | `1.0` | 語速倍率 |
| `key` | `""` | Azure TTS subscription key（azure-tts 用） |
| `region` | `""` | Azure region，例如 `"eastasia"`（azure-tts 用） |
| `endpoint` | `""` | 自訂 Azure endpoint（覆蓋 region 推斷，azure-tts 用） |

**voice / lang 對照表：**

| engine | voice 範例 | lang |
|--------|-----------|------|
| `edge-tts` | `"en-US-AriaNeural"` `"zh-TW-HsiaoChenNeural"` | 不需填 |
| `melotts` | `"EN-US"` `"EN-BR"` `"EN-AU"` `"ZH"` | `"en"` `"zh"` `"fr"` `"jp"` |
| `kokoro-api` | `"af_bella"` `"af_sky"` `"am_adam"` | `"en-us"` |
| `kokoro` | `"af_sky"` `"af_bella"` | `"en-us"` `"en-gb"` |
| `azure-tts` | `"zh-TW-HsiaoChenNeural"` `"en-US-AriaNeural"` | 不需填 |

**`player` 自動選擇邏輯（`"auto"`）：**

| 格式 | 優先順序 |
|------|---------|
| MP3（edge-tts） | `mpg123` → `mpv` → `ffplay` → `mplayer` |
| WAV/OGG（kokoro / melotts） | `paplay` → `mpg123` → `mpv` → `ffplay` |

**WSL2 安裝建議（`player: "auto"` 前提）：**

```bash
sudo apt install mpg123 pulseaudio-utils   # mpg123=MP3, paplay=WAV
```

WSLg（Windows 11 21H2+）必須啟用，`$PULSE_SERVER` 才會有值。勿用 `"player": "powershell"`，會造成打字輸入卡頓。

### Agent system prompt 更新

`_load_history()` always strips `role: system` from `history.json` — the system message is always rebuilt from `_custom_system_prompt` (set from `config.json`) plus dynamic content (date, cwd, MCP context). Editing `history.json`'s system message has no effect.

To update a running agent's system prompt: edit `agents/<name>/config.json` → run `/reload-agent`. The agent's `_custom_system_prompt` is updated and `_rebuild_system_prompt()` is called in-place; conversation history is preserved.

### `history_keep` 語義

`history_keep = N` 代表「記住最近 N 個 user/assistant 來回（pair）」：
- 每次送給 AI：最後 N pair（= N×2 則訊息）
- 存入 history.json：同樣 N pair
- 全域預設：env `HISTORY_KEEP=10`（10 pair）

## MCP configuration

Two config files control MCP servers (subagents share the main agent's MCP client):

| File | Scope | Purpose |
|------|-------|---------|
| `~/.kai/mcp_servers.json` | global (user home) | MCP servers available to all agents across all projects |
| `cwd/.kai/mcp_servers.json` | project-local | project-specific MCP servers; merged with global |

Disabled servers use a `_` prefix in `mcp_servers.json` (e.g. `_playwright`, `_filesystem`). The `_` prefix convention prevents accidental activation — remove it to enable.

## Environment variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL` | `qwen3-vl:4b` | Active model |
| `CREATE_AGENT_MODEL` | `""` | Model used for `/create-agent` design step (empty = same as MODEL) |
| `COMPACT_MODEL` | `""` | Model used for `/compact` history compression (empty = auto-select) |
| `DEBUG_TOOLS` | `0` | Set `1` to enable tool debug cards in UI + append to `tool_calls.log` |
| `NUM_CTX` | `0` | Context window override (0 = model default) |
| `MAX_TOOL_CALLS` | `15` | Max tool calls per turn |
| `TEMPERATURE` | `0.6` | Generation temperature |
| `TOP_K` | `20` | Top-k sampling (0 = disable) |
| `TOP_P` | `0.95` | Nucleus sampling |
| `USERNAME` | `你` | Display name in chat bubbles and prompt title |
| `HISTORY_KEEP` | `10` | Default history pairs to keep (agent config overrides per-agent) |
| `BASH_TIMEOUT` | `30` | `bash_exec` default timeout (seconds) |
| `OLLAMA_STREAM_TIMEOUT` | `120` | Seconds before Ollama stream declared stuck |
| `SEARXNG_URL` | `http://localhost:8888/search` | SearXNG API endpoint |
| `SEARCH_NUM_RESULTS` | `5` | Number of search results to return |
| `MAX_SEARCH_CHARS` | `8000` | Max characters per search result |
| `FETCH_MAX_CHARS` | `12000` | Max characters returned by `fetch_url` |
| `GEMINI_API_KEY` | — | Google Gemini API key |
| `DEEPSEEK_API_KEY` | — | DeepSeek API key |
| `CONTEXT7_API_KEY` | — | Context7 MCP API key |
| `AZURE_TTS_KEY` | `""` | Azure TTS subscription key |
| `AZURE_TTS_REGION` | `""` | Azure region，例如 `eastasia` |
| `AZURE_TTS_ENDPOINT` | `""` | 自訂 Azure TTS endpoint（覆蓋 region 推斷） |
| `DIFY_API_URL` | `"http://localhost"` | Dify server URL |
| `DIFY_API_KEY` | — | Dify Knowledge Base API key（金鑰，只放 `.env`） |
| `DIFY_TOP_K` | `5` | Number of chunks returned per RAG query |
| `DIFY_RERANK_PROVIDER` | `"xinference"` | Rerank model provider name in Dify |
| `DIFY_RERANK_MODEL` | `""` | Rerank model name (empty = reranking disabled) |

Config priority (highest → lowest): `cwd/.kai/config.json` > `~/.kai/config.json` > `.env` > code defaults. Use `/init-config` to export current values and `/reload-config` to hot-apply changes. Only put API keys in `.env`; all other tuning belongs in `.kai/config.json`. Change hardcoded defaults in `core/config.py`; per-model context windows in `core/models.py`.

## SearXNG

Runs in Docker on port 8888 with a Valkey sidecar for rate-limiting. Config is `searxng/settings.yml` (mounted read-only). `formats: [html, json]` must remain enabled for the JSON API used by `web_search`. `limiter: false` is intentional — this is a local private instance.
