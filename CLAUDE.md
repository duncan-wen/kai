# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (all extras = local TTS engines kokoro / melotts)
uv sync --all-extras

# Run the Textual TUI
uv run python app.py

# Lint
uv run ruff check .
uv run ruff format .

# Run tests
uv run pytest tests/

# Start SearXNG + Valkey (default)
docker compose up -d

# Start all services (TTS + DB included)
docker compose --profile tts --profile db up -d
```

## Architecture

Single entry point (`app.py`) on top of a UI-independent core:

```
app.py              → Textual TUI; ChatApp composes all mixins
core/
  agent.py          → Agent class: history, streaming, tool-call loop
  agent_factory.py  → /create-agent, /load-agent, /list-agents logic (AI-based agent design)
  backends.py       → stream_ollama / stream_openai_compat (backend-specific streaming)
  app_state.py      → ChatApp state dataclasses (InputState, SessionState, UIState, TabState)
  session_store.py  → SessionStore: /cls, /resume, session naming, archive list/rename/delete (disk-only state)
  tools/            → built-in tools: web.py (web_search / fetch_urls), bash.py (bash_exec), dify.py (RAG), dispatch.py (run_tool)
  models.py         → ModelProfile dataclass + REGISTRY
  config.py         → all env-var constants (loaded once at import)
  config_registry.py → single source of truth for every configurable key
  mcp_client.py     → MCP server lifecycle + tool dispatch
  tts.py            → TTS engine abstraction (edge-tts, melotts, kokoro-api, kokoro-onnx)
  tool_log.py       → tool call debug log (append to .kai/tool_calls.log, only when DEBUG_TOOLS=1)
mixins/             → ChatApp behaviour split into focused mixins (all inherit _MixinBase = ChatApp)
  agents.py         → AgentsMixin: /create-agent, /load-agent, /list-agents, /reload-agent, /delete-agent
  app_tools.py      → AppToolsMixin: 19 AI-callable app tools (load/create/delete agent, model switch, TTS, MCP…)
  bash.py           → BashMixin: ! command execution
  mcp.py            → MCPMixin: /mcp-list, /mcp-reload, /mcp-add, /mcp-del
  session.py        → SessionMixin: /cd, /compact, /cls, /resume, /model, /rename, /nickname, /reload-config, /init-config
  streaming.py      → StreamingMixin: _send worker, on_token, on_done, TTS playback (run_in_executor thread)
widgets/
  input.py          → HistoryInput, ChatAutoComplete
  tree.py           → RightClickTree, TreeContextMenu, FileTree
  sidebar.py        → ResizeHandle, Sidebar
  chat.py           → ImageBubble, FileViewer, CopyButton, CapabilityBadge, CapLabel, AIMessage
  commands.py       → ChatCommands (command palette provider)
  agents_screen.py  → AgentsScreen ModalScreen (DataTable agent selector)
  mcp_list.py       → MCPListScreen ModalScreen (MCP server / tool listing)
  resume.py         → ResumeScreen ModalScreen (session archive browser + rename)
  config_screen.py  → ConfigScreen ModalScreen (runtime config viewer + interactive export)
  utils.py          → shared TUI utilities: _copy_to_clipboard, file icons, _line_height, _wrap_box_drawing
```

`core/` must not import `widgets/`. `widgets/` may import `core/`. Both layers use relative imports internally.

## TUI (`app.py`)

### Layout

```
┌─ FileTree ─┬─── TabbedContent ────────────────┬─ Sidebar ─┐
│ RightClick │  Chat tab  │  file tabs (×)       │ cwd       │
│ Tree       │  messages  │  FileViewer          │ model     │
│            │            │  (syntax highlight)  │ context   │
│            ├────────────┴──────────────────────┤ git       │
│            │  cap-bar (Think / MCP badges)     │ MCP tools │
│            │  #prompt (HistoryInput)           │           │
└────────────┴───────────────────────────────────┴───────────┘
```

### Key widgets

| Widget | File | Purpose |
|--------|------|---------|
| `ChatApp` | app.py | root App, composes all mixins, event handlers |
| `HistoryInput` | widgets/input.py | single-line input with Up/Down history, Ctrl+Z undo, multiline paste intercept |
| `ChatAutoComplete` | widgets/input.py | slash command / path autocomplete |
| `FileTree` | widgets/tree.py | lazy-loading dir tree; double-click opens file tab; Ctrl+click / Space multi-select for batch delete; 2s deep fs watcher |
| `RightClickTree` | widgets/tree.py | Tree subclass; intercepts right-click + Ctrl+click; Space/Ctrl+A/Escape key bindings |
| `TreeContextMenu` | widgets/tree.py | ModalScreen right-click menu; shows "刪除選取 (N 項)" when multi-selected |
| `FileViewer` | widgets/chat.py | TextArea with syntax highlighting + WSL-aware Ctrl+C multi-line copy; mtime polling for external change detection; Ctrl+S save, F2 save-as |
| `AIMessage` | widgets/chat.py | AI response widget; Static stream-view during streaming, Markdown md-view on final |
| `Sidebar` | widgets/sidebar.py | cwd / model / git status / MCP tools |
| `ResizeHandle` | widgets/sidebar.py | drag-to-resize handle between file tree and center |
| `ChatCommands` | widgets/commands.py | Ctrl+P command palette: clear, switch model |
| `AgentsScreen` | widgets/agents_screen.py | ModalScreen DataTable for /list-agents selection |
| `MCPListScreen` | widgets/mcp_list.py | ModalScreen showing connected MCP servers + tools |
| `ResumeScreen` | widgets/resume.py | ModalScreen session archive browser with inline rename |
| `ConfigScreen` | widgets/config_screen.py | ModalScreen runtime config viewer; Space toggle keys, L→local G→global export |

### Slash commands

| Command | Action |
|---------|--------|
| `/clear` | Clear the chat screen only (history kept) |
| `/cls` | Archive history to `.history/`, then clear memory and UI (guard: skips if only system msg) |
| `/resume [N]` | List or restore archived sessions (ResumeScreen picker) |
| `/rename <name>` | Name current session (shown in ResumeScreen) |
| `/compact` | Compress history with Gemini to free context window |
| `/model <name>` | Switch model (hot-swap, preserves history) |
| `/nickname [name]` | Query or set display nickname for current model |
| `/cd <path>` | Change directory (saves old history, loads new) |
| `/think` | Toggle thinking mode |
| `/debug` | Toggle tool debug cards |
| `/image <path> [q]` | Send image to vision model |
| `/ask <question>` | Ephemeral question — not saved to history |
| `/save` | Promote last `/ask` answer into permanent history |
| `/remember <note>` | Write note to `.kai/notes.md` (persistent across sessions) |
| `/mcp-list` | Show connected MCP servers and tool descriptions |
| `/mcp-reload` | Restart MCP servers without quitting the app |
| `/mcp-add <name> <cmd> [args...]` | Add a new MCP server to `mcp_servers.json` |
| `/mcp-del <name>` | Remove an MCP server from `mcp_servers.json` |
| `/create-agent <description>` | Use AI (CREATE_AGENT_MODEL) to design a new agent config in `agents/` |
| `/load-agent <name>` | Load an agent config and spawn a new tab (dedup: switches to existing tab if already open) |
| `/list-agents` | Show all agents in `agents/` (AgentsScreen picker) |
| `/delete-agent [name]` | Delete agent(s); omit name to open multi-select panel |
| `/close-agent [name]` | Close an agent tab; omit name to close the current tab |
| `/greeting on\|off` | Toggle subagent opening greeting |
| `/reload-agent` | Re-read `config.json` and hot-swap system prompt, model, nickname, tts in running agent |
| `/reload-config` | Re-read `.kai/config.json` and apply all settings to the running process |
| `/init-config` | Open interactive ConfigScreen: select keys, L→`cwd/.kai/config.json`, G→`~/.kai/config.json` |
| `/tts <engine\|off>` | Switch TTS engine or disable: `edge-tts`, `melotts`, `kokoro-api`, `kokoro`, `azure-tts`, `off` (in-memory only; does not persist to config.json) |

### `!` bash commands

Any input starting with `!` runs as a shell command via `/bin/bash -c` (supports brace expansion, `$((expr))`, `[[` etc.).
Special case:
- `!cd <path>` — intercepted, calls `os.chdir` (subprocess `cd` has no effect on parent process)
- After every bash command: file tree and git sidebar auto-refresh

### File tree multi-select

| Operation | Effect |
|-----------|--------|
| `Ctrl+click` | Toggle item in multi-select (shows green ✓) |
| `Space` | Toggle cursor item; if anchor set, range-select from anchor to cursor |
| `Ctrl+A` | Select all / deselect all visible nodes |
| `Escape` | Clear multi-select (if none, focus prompt) |
| Right-click on selected item | Shows "刪除選取 (N 項)" for batch delete |

Multi-select state is cleared after delete and after `refresh_tree`.

### File tree filesystem watcher

`FileTree._check_external_change` runs every 2 seconds in a background thread (via `run_worker(thread=True)`). It does a deep `os.walk` (skipping `_HIDDEN_DIRS`) comparing all file/dir mtimes. Any external change (VSCode delete, `touch`, etc.) triggers `refresh_tree`. The scan is off the event loop to prevent render glitches.

### FileViewer external change detection

`FileViewer._check_external_change` polls the file's `st_mtime` every 1 second. If changed externally (and not modified locally), it reloads content and shows a toast. `action_save` updates `_mtime` after write to suppress false triggers.

### Streaming rendering

During streaming (`update_content(final=False)`): text is written to a `Static(markup=False)` widget using `RichText(overflow="fold")` — no markdown parsing, O(1) cost regardless of length.

On completion (`update_content(final=True)`): `stream-view` is hidden, `md-view` (Markdown widget) is shown and rendered once. This prevents the slowdown where long responses got progressively slower to render.

### Clickable non-Button widgets

Avoid using `Button` for single-line or inline clickable labels. Textual's `Button` has a `border: tall` default that causes initial layout miscalculation (compositor renders 3-line height before CSS `border: none` takes effect), producing display corruption on first render that only resolves on focus.

Use `Static` with `@on(events.Click, "#widget-id")` instead. Always call `event.stop()` in the handler to prevent the click from bubbling to `ChatApp.on_click` (which calls `screen.get_widget_at` — if a modal was just pushed in the same handler, that screen's compositor is not yet initialized and crashes).

`ChatApp.on_click` wraps `screen.get_widget_at` in `try/except` as a defensive guard.

### Markdown rendering fixes

- `MarkdownTableCellContents.with_tooltip` is patched to no-op in `ChatApp.__init__` to prevent Tooltip widgets accumulating on Screen's `_tooltips` layer.
- `_wrap_box_drawing()` in `widgets/utils.py` auto-detects lines with ≥3 box-drawing characters (U+2500–U+257F range) and wraps them in a code fence before Markdown render, preventing word-wrap from destroying ASCII diagrams.

### Directory switching (`_do_cd`)

When changing directory (via `/cd` or `!cd`):
1. Save current `history.json` to old cwd
2. `os.chdir(target)`
3. `agent.reset_history()` — clears and reloads from new cwd's `history.json`
4. Clears chat UI
5. Refreshes file tree and sidebar

### Session history (`/cls` and `/resume`)

All archive lifecycle lives in `core/session_store.py` (`SessionStore(history_file)`); mixins and `ResumeScreen` only notify and re-render. Terms (Session, Archive, Resumed session) are defined in `GLOSSARY.md`; UI text uses the noun "archive" and the verb「封存」, not「歸檔」. State is disk-only, next to each history file (main and every agent tab are independent): `.history/history_<ts>[_N].json`, `.names.json`, `.current_name`, `.resumed_from`. Don't mirror it in UI state.

`/cls` → `close()`: archives `history.json` to `.history/history_YYYYMMDD_HHMMSS[_N].json`; a resumed session with no new messages is discarded instead of re-archived. A resumed session whose source archive no longer exists is treated as a normal session (archived, never silently deleted).

`/resume` → `resume(archive)`: closes the current session first (auto-archive if it has new messages), then copies the archive in and marks it resumed. Any non-ephemeral new message — typed by the user (`_send`), saved via `/save`, or dispatched from another agent (`_dispatch_to_agent`) — must call `mark_active()`, which ends the resumed state and carries the archive's name over.

`ResumeScreen` lists archives (timestamp, name, message count, last user message) with inline rename / delete via the same module.

### Multiline paste (`HistoryInput`)

When pasting multiline text into the prompt:
- Intercepts in `on_paste`; shows compact `[N 行] preview…` marker in input
- If pasted from `FileViewer` copy, auto-wraps with ` ```lang filename:lines` context
- On submit, replaces marker with full content while preserving any surrounding typed text

### Clipboard (WSL)

`_copy_to_clipboard()` in `widgets/utils.py` tries `xclip` first, then `clip.exe` with UTF-16-LE + BOM (`\xff\xfe`). BOM is required for `clip.exe` to treat input as Unicode; without it CJK characters become mojibake. All `subprocess.run` clipboard calls have `timeout=3` to prevent blocking app shutdown.

### Key bindings

| Key | Action |
|-----|--------|
| `Ctrl+Q` | Quit |
| `Ctrl+P` | Command palette |
| `Ctrl+T` | Toggle file tree |
| `Ctrl+B` | Toggle sidebar |
| `Ctrl+W` | Close active tab (priority binding) |
| `Escape` | Cancel streaming response / clear tree multi-select |
| `↑ / ↓` | Navigate input history |
| `Ctrl+Z` | Undo in input |
| `Space` (in tree) | Toggle current node in multi-select / range-select from anchor |
| `Ctrl+A` (in tree) | Select all / deselect all |
| `Ctrl+S` (FileViewer) | Save file |
| `F2` (FileViewer) | Save As (Ctrl+Shift+S is indistinguishable from Ctrl+S in most terminals) |

## Agent (`core/agent.py`)

### Tool calling loop (`Agent.chat`)

1. Stream Ollama/Gemini response; collect `msg.thinking`, `msg.content`, `msg.tool_calls`
2. If `tool_calls` present → `asyncio.gather` all tools concurrently → append `tool` messages → repeat
3. Loop exits when model returns no tool calls or `MAX_TOOL_CALLS` is reached
4. `save_history()` is called after every turn
5. `reset_history()` clears and reloads `history.json` from current cwd (used on directory change)

Actual streaming is delegated to `core/backends.py`: `stream_ollama` for Ollama models, `stream_openai_compat` for OpenAI-compatible endpoints (Gemini, etc.).

### History

`HISTORY_FILE = Path(".kai/history.json")` — relative to cwd at runtime. Each project directory has its own history. Archives go to `.kai/.history/` (gitignored).

### history vs messages distinction

| | `self.history` | `messages` (current turn) |
|---|---|---|
| Content | user + assistant final replies | includes tool_calls + tool_results |
| Persisted | written to history.json | exists only during chat() call |
| Next turn | rebuilt via `_trim_history()` | discarded |

### Adding a new model

Add one entry to `REGISTRY` in `core/models.py`:

| 模型 | thinking | vision | tools | backend |
|------|----------|--------|-------|---------|
| `deepseek-r1:1.5b` | ✓ | - | ✗ | ollama |
| `qwen2.5-coder:3b` | - | - | ✗ | ollama |
| `qwen3.5:2b` | - | ✓ | ✓ | ollama |
| `qwen3-vl:4b` | ✓ | ✓ | ✓ | ollama |
| `qwen3.5:4b` | ✓ | ✓ | ✓ | ollama |
| `gemma4:e2b` | ✓ | ✓ | ✓ | ollama |
| `gemma4:e4b` | ✓ | ✓ | ✓ | ollama |
| `deepseek-v4-flash` | ✓ | - | ✓ | deepseek |
| `deepseek-v4-pro` | ✓ | - | ✓ | deepseek |
| `gemini-2.0-flash` | - | ✓ | ✓ | gemini |
| `gemini-2.5-flash` | ✓ | ✓ | ✓ | gemini |
| `gemini-2.5-pro` | ✓ | ✓ | ✓ | gemini |

`get_profile()` returns a `dataclasses.replace()` copy to avoid mutating REGISTRY. `NUM_CTX` in `.env` overrides all profiles at runtime.

### Adding a new tool

1. Define the Ollama tool schema (like `SEARCH_TOOL` in `core/tools/web.py`)
2. Implement `async def my_tool(...) -> str`
3. Register in `Agent.tools_map` in `core/agent.py`
4. Pass the schema in `active_tools` list inside `chat()`

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

Disabled servers use a `_` prefix in `mcp_servers.json` (e.g. `_playwright`, `_twstock-db`). The `_` prefix convention prevents accidental activation — remove it to enable.

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

## Conventions

See `CONVENTIONS.md` for the full style guide. Key points:
- All code comments, log messages, UI text, and error messages use **Traditional Chinese** (繁體中文). Avoid PRC variants: 「專案」not「項目」, 「程式碼」not「代碼」, 「函式」not「函數」.
- All public functions and methods require type hints including return type.
- I/O operations are `async def`; blocking calls go through `asyncio.to_thread()`.
- Pure data structures use `@dataclass`; cross-boundary inputs use `pydantic.BaseModel`.

## Key dependencies

- `textual` — TUI framework (with `[syntax]` extra for tree-sitter highlighting)
- `textual-autocomplete` — slash command / path autocomplete in prompt
- `ollama` — native Python client; used directly to access `msg.thinking` and `tool_calls`
- `openai` — used by `stream_openai_compat` in `core/backends.py` for Gemini / compatible endpoints
- `rich` — text rendering in agent internals and widgets
- `prompt_toolkit` — input history file format (FileHistory)
- `tenacity` — retry logic in `web_search`
- `loguru` — structured logging to `agent_tui.log`

## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues (`duncan-wen/kai`), managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the repo root (created lazily). See `docs/agents/domain.md`.
