# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (all extras = local TTS engines kokoro / melotts + Langfuse observability)
uv sync --all-extras

# Run the Textual TUI
uv run python app.py

# Lint
uv run ruff check .
uv run ruff format .

# Run tests (tests/test_tui_smoke.py drives the real ChatApp headless via run_test() + Pilot)
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
  agent.py          → Agent class: history, system prompt, model switch, tool schemas, commit
  turn.py           → run_turn: the tool-call loop as typed events (ThinkingDone / Token / ToolEvent / Done …)
  model_stream.py   → ModelStream seam: CancelToken, Delta, ToolCall, Usage, AssistantStep, make_model_stream
  agent_factory.py  → /create-agent, /load-agent, /list-agents logic (AI-based agent design)
  agent_registry.py → AgentRegistry: sole owner of main agent + agent tabs (tab ids, active agent, name lookup, MCP distribution); ChatApp.agent = agents.active
  backends.py       → OllamaStream / OpenAICompatStream adapters (wire format, lazy SDK clients, Langfuse generation)
  app_state.py      → ChatApp state dataclasses (InputState, SessionState, UIState, TabState) + AgentView display snapshot; agent tabs live in AgentRegistry, not here
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
  app_tools.py      → AppToolsMixin: 22 AI-callable app tools (load/create/delete agent, model switch, TTS, MCP…)
  bash.py           → BashMixin: ! command execution
  commands.py       → CommandsMixin: every slash-command handler (`@slash`) + `run_slash` dispatch
  mcp.py            → MCPMixin: /mcp-list, /mcp-reload, /mcp-add, /mcp-del
  session.py        → SessionMixin: /cd, /compact, /cls, /resume, /model, /rename, /nickname, /reload-config, /init-config
  streaming.py      → StreamingMixin: _send worker, _turn_sink (shared by all turn consumers), TTS playback (run_in_executor thread)
widgets/
  input.py          → HistoryInput, ChatAutoComplete, PromptSpinner (border_title spinner)
  center_tabs.py    → CenterTabs(TabbedContent): file tabs, close (unsaved confirm, “×” click), move, messages_for(tab_id) → ChatLog; posts TabClosed / ActiveChanged
  chat_log.py       → ChatLog(VerticalScroll): one tab's message stream (add_user / add_inbox / add_notice / add_error / add_output / render_history); start_reply → ReplySink renders turn events; format_stats, error_text
  prompt_bar.py     → PromptBar: input row (📎 + HistoryInput + autocomplete); owns prompt history, posts PromptBar.Submitted
  slash.py          → slash command table (SlashCommand, @slash); single source for dispatch / autocomplete / palette
  tree.py           → RightClickTree, TreeContextMenu, FileTree
  sidebar.py        → ResizeHandle, Sidebar
  chat.py           → ImageBubble, FileViewer, CopyButton, CapBar, AIMessage
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
┌─ FileTree ─┬─── CenterTabs ───────────────────┬─ Sidebar ─┐
│ RightClick │  Chat tab  │  file tabs (×)       │ cwd       │
│ Tree       │  messages  │  FileViewer          │ model     │
│            │            │  (syntax highlight)  │ context   │
│            ├────────────┴──────────────────────┤ git       │
│            │  cap-bar (Think / MCP badges)     │ MCP tools │
│            │  PromptBar: 📎 #prompt (History…) │           │
└────────────┴───────────────────────────────────┴───────────┘
```

### Key widgets

| Widget | File | Purpose |
|--------|------|---------|
| `ChatApp` | app.py | root App, composes all mixins, event handlers |
| `PromptBar` | widgets/prompt_bar.py | input row: 📎 attach button + `HistoryInput`; owns Up/Down history (`.kai/.prompt_history`), marker expansion, mouse-debris filter; mounts `ChatAutoComplete` on the screen; posts `PromptBar.Submitted(text)` — `ChatApp.on_prompt_bar_submitted` only dispatches (`!` / `/` / chat) |
| `CenterTabs` | widgets/center_tabs.py | `TabbedContent` subclass: opens / dedups file tabs (`open_file`), adds agent tabs (`add_agent_tab`, id from `AgentRegistry`), closes tabs (`request_close`: `ConfirmCloseScreen` for unsaved files, “×” click), moves tabs (`move_active`), finds a tab's `ChatLog` (`messages_for`); posts `ActiveChanged(tab_id)` (App sets `active_tab` + active agent) and `TabClosed(tab_id)` (App drops agent tabs from the registry) |
| `HistoryInput` | widgets/input.py | single-line input: long-text / multiline paste → marker, Ctrl+Z undo, ghost-text prediction; Up/Down post `HistoryInput.Navigate` to `PromptBar` |
| `ChatAutoComplete` | widgets/input.py | slash command / path autocomplete |
| `FileTree` | widgets/tree.py | lazy-loading dir tree; double-click opens file tab; Ctrl+click / Space multi-select for batch delete; 2s deep fs watcher |
| `RightClickTree` | widgets/tree.py | Tree subclass; intercepts right-click + Ctrl+click; Space/Ctrl+A/Escape key bindings |
| `TreeContextMenu` | widgets/tree.py | ModalScreen right-click menu; shows "刪除選取 (N 項)" when multi-selected |
| `FileViewer` | widgets/chat.py | TextArea with syntax highlighting + WSL-aware Ctrl+C multi-line copy; mtime polling for external change detection; Ctrl+S save, F2 save-as |
| `ChatLog` | widgets/chat_log.py | one tab's message stream (`#messages` for Chat, `msgs-{tab_id}` for agent tabs). Everything shown in a chat goes through its `add_*` / `render_history` methods; a turn draws through `start_reply(agent, debug=, on_phase=)` → `ReplySink` (thinking, 0.1s-throttled bubble, tool cards, notices, stats). Owns the "auto-scroll only when already at the bottom" rule. Don't `mount` into it from mixins |
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

Adding a slash command: write `async def _cmd_xxx(self, arg: str) -> None` in `mixins/commands.py` with `@slash("/xxx", "說明", needs_arg=..., presets=...)`. Prompt submit, autocomplete and the Ctrl+P palette all read the same table; `needs_arg=True` makes the palette fill the prompt instead of running. Don't add `if cmd == ...` branches to `on_input_submitted`.

Prompt input reaches the App only as `PromptBar.Submitted(text)` (marker already expanded, history already recorded). Don't handle `Input.Submitted` on the App or read `HistoryInput`'s `_multiline_*` fields from outside; to put text into the prompt use `PromptBar.attach(path)` or set `prompt.value`.

Prompt spinner: use `PromptSpinner(prompt, label, visible=...).start()` and `stop()` in `finally`; change `spinner.label` to update the phase text. `stop()` restores the idle title itself. Don't hand-roll `set_interval` tick closures.

### Reactive UI state

State that is drawn on screen lives in `ChatApp` reactives; widgets bind to it in `compose()` via `data_bind` and redraw in their own `watch_*`:

| Reactive | Bound by | Drives |
|----------|----------|--------|
| `debug_tools` | `PromptBar.debug_mark` → `HistoryInput.debug_mark` | prompt idle title / spinner debug mark |
| `think_override` | (`watch_think_override` → `refresh_agent_view`) | thinking on/off |
| `agent_view: AgentView` | `Sidebar.view`, `CapBar.view`; `ChatApp.watch_agent_view` | sidebar model + MCP, cap-bar think/vision, subtitle, prompt placeholder |
| `active_tab` / `busy_jobs` → computed `prompt_locked` | `PromptBar.locked` → `HistoryInput.locked` | prompt `disabled` + `.streaming` class; unlock restores idle title + focus |

`AgentView` (`core/app_state.py`) is a frozen snapshot of the active agent. Agents are mutated in place by core code, so after changing anything it shows (model switch, nickname, tab switch, reload-agent, rename/resume/cd, MCP ready) call `self.refresh_agent_view()` — equal snapshots don't trigger redraws. Don't write to `#sb-model` / `#cap-think` / `sub_title` / prompt `border_title` directly.

Long-running work in a tab (streaming, agent dispatch, greeting, `/create-agent`, `!bash`) wraps itself in `self.mark_busy(tab_id)` … `finally: self.unmark_busy(tab_id)` and gives its `PromptSpinner` `visible=lambda: self.active_tab == tab_id`. Never set `prompt.disabled` or the `streaming` class by hand; tab switches lock/unlock automatically. `busy_jobs` holds one `(tab_id, cancel)` entry per job (a multiset), so overlapping jobs on one tab unlock only when all finish.

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

Use `Static` with `@on(events.Click, "#widget-id")` instead. Always call `event.stop()` in the handler so the click doesn't bubble on to ancestor click handlers (e.g. `CenterTabs.on_click`, which closes a tab on its “×”). Use `event.widget` to find what was clicked; don't call `screen.get_widget_at` — if a modal was just pushed in the same handler, that screen's compositor is not yet initialized and crashes.

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

All archive lifecycle lives in `core/session_store.py` (`SessionStore(history_file)`); mixins and `ResumeScreen` only notify and re-render. Terms (Session, Archive, Resumed session) are defined in `GLOSSARY.md`; UI text uses the noun "archive" and the verb「封存」, not「歸檔」. State is disk-only, next to each history file (main and every agent tab are independent): `.history/history_<ts>[_N].json`, `.names.json`, `.current_name`, `.resumed_from`, `.session_id` (persistent observability ID of the current session), `.session_ids.json` (archive → session ID, restored on `/resume`). Don't mirror it in UI state.

`/cls` → `close()`: archives `history.json` to `.history/history_YYYYMMDD_HHMMSS[_N].json`; a resumed session with no new messages is discarded instead of re-archived. A resumed session whose source archive no longer exists is treated as a normal session (archived, never silently deleted).

`/resume` → `resume(archive)`: closes the current session first (auto-archive if it has new messages), then copies the archive in and marks it resumed. Any non-ephemeral new message — typed by the user (`_send`), saved via `/save`, or dispatched from another agent (`_dispatch_to_agent`) — must call `mark_active()`, which ends the resumed state and carries the archive's name over.

`ResumeScreen` lists archives (timestamp, name, message count, last user message) with inline rename / delete via the same module.

### Multiline paste (`HistoryInput`)

When pasting multiline text into the prompt:
- Intercepts in `on_paste`; shows compact `[N 行] preview…` marker in input
- If pasted from `FileViewer` copy, auto-wraps with ` ```lang filename:lines` context
- On submit, `PromptBar` calls `HistoryInput.take_submission()`, which replaces the marker with the full content while preserving any surrounding typed text

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

### Turn (`core/turn.py`)

`run_turn(agent, TurnRequest(user_msg, persist, think), sink, cancel) -> Done` runs one turn:

1. Takes `agent.turn_lock` and `agent.turn_scope()` (a mid-turn `switch_model` applies after the turn); `agent.prepare_turn()` rebuilds the system prompt
2. Each step calls `agent.model_stream.step(...)`; tool calls run concurrently, results go back as `tool` messages
3. Events go to `sink`: `ThinkingStarted` / `ThinkingDone`, `Token` (text already stripped of `<next>`), `StepReset`, `ToolEvent`, `Compacting` / `Compacted`, then exactly one `Done(reason, reply, stats, prediction, error, new_messages)`; a reply that is empty after stripping `<next>` ends as `empty` and is not committed
4. History is committed once at the end (`persist`: `all` / `reply_only` / `none`); errors and cancels leave it untouched
5. Model/backend errors never raise — they become `Done(reason="error", error=TurnError(kind))`, kind `overflow` / `timeout` / `quota` / `other`; overflow triggers one compact + retry of the same message, continuing from the tool steps already done (tools are not re-run). An empty reply whose prompt hit `num_ctx` (Ollama truncates silently instead of erroring) also ends as `overflow`, without compacting
6. Bad tool-argument JSON is returned to the model as an error `ToolResult`; `MAX_TOOL_CALLS` ends with `Done(tool_limit)`
7. Cancel during tool execution waits for the batch, still emits its `ToolEvent`s, then ends with `Done(cancelled)`

Backend differences live behind the `ModelStream` seam (`core/model_stream.py`); the turn uses a neutral message format and adapters in `core/backends.py` convert it to wire format. Tests swap in `tests/fake_model.py::FakeModel` via `agent.model_stream = FakeModel([...])`.

UI consumers (`_send`, `_dispatch_to_agent`, `_trigger_greeting`) call `run_turn` with the same sink, `StreamingMixin._turn_sink(log, agent, spinner)`: `ChatLog.start_reply` draws it, the wrapper refreshes file tree / git. Consumers only add what is theirs (TTS and prediction in `_send`, inbox bubble and error toast in dispatch). Each turn's `CancelToken` is registered with `mark_busy(tab_id, cancel)` / `unmark_busy(tab_id, cancel)`; Esc calls `cancel_turns(active_tab)`. `/ask` uses `persist="none"` and `/save` calls `await agent.commit(done.new_messages)`. Call `mark_busy` immediately before the `try` whose `finally` unmarks it.

### History

`HISTORY_FILE = Path(".kai/history.json")` — relative to cwd at runtime. Each project directory has its own history. Archives go to `.kai/.history/` (gitignored).

### history vs messages distinction

| | `self.history` | `messages` (current turn) |
|---|---|---|
| Content | user + assistant final replies | includes tool_calls + tool_results |
| Persisted | written to history.json | exists only during run_turn() |
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
| `deepseek-flash` | ✓ | - | ✓ | deepseek |
| `deepseek-v4-pro` | ✓ | - | ✓ | deepseek |
| `gemini-2.0-flash` | - | ✓ | ✓ | gemini |
| `gemini-2.5-flash` | ✓ | ✓ | ✓ | gemini |
| `gemini-2.5-pro` | ✓ | ✓ | ✓ | gemini |

`get_profile()` returns a `dataclasses.replace()` copy to avoid mutating REGISTRY. `NUM_CTX` in `.env` overrides all profiles at runtime.

### Adding a new tool

1. Define the Ollama tool schema (like `SEARCH_TOOL` in `core/tools/web.py`)
2. Implement `async def my_tool(...) -> str`
3. Register in `Agent.tools_map` in `core/agent.py`
4. Add the schema in `Agent.tool_schemas()`

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
- `openai` — used by `OpenAICompatStream` in `core/backends.py` for Gemini / compatible endpoints
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
