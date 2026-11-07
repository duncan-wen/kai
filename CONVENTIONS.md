# CONVENTIONS.md

## 語言與文字

所有對話回覆、程式碼註解、log 訊息、UI 文字（Rich Panel/Rule/status）、錯誤訊息，一律使用**繁體中文**。

避免中國大陸用語：
- 「專案」而非「項目」
- 「程式碼」而非「代碼」
- 「資料庫」而非「數據庫」
- 「函式」而非「函數」
- 「字串」而非「字符串」

## 命名規則

| 對象 | 規則 | 範例 |
|------|------|------|
| 函式、變數、模組 | `snake_case` | `run_tool`, `tool_names` |
| 類別 | `PascalCase` | `MCPClient`, `ModelProfile` |
| 常數（模組層級） | `UPPER_SNAKE` | `MAX_TOOL_CALLS`, `SEARCH_TOOL` |
| 私有 / 內部方法 | `_` 前綴 | `_reset_date`, `_cancelled` |
| 停用的 MCP server | `_` 前綴（於 mcp_servers.json）| `_twstock-db` |

## 程式碼風格

- **型別標注**：所有公開函式和方法都加 type hints；回傳值標注不可省略。
- **async 優先**：I/O 操作（HTTP、subprocess、MCP）一律用 `async def`；blocking 呼叫透過 `asyncio.to_thread()` 隔離。
- **資料類別**：純資料結構用 `@dataclass`（如 `ModelProfile`）；跨系統邊界輸入用 `pydantic.BaseModel` 驗證（如 `WebSearchArgs`）。
- **不要寫多餘的 fallback**：只在系統邊界（使用者輸入、外部 API）做驗證，內部呼叫信任型別標注。
- **不寫明顯的 what 型註解**：只有非顯而易見的 why（特殊限制、繞過 bug）才加。

## 套件結構

```
app.py          → TUI 入口點（ChatApp 組合所有 mixin）
core/           → 業務邏輯（與 UI 無關）
  agent.py      → Agent, 對話歷史, 工具呼叫迴圈
  agent_factory.py → /create-agent 邏輯，agents/ 目錄管理
  app_state.py  → InputState, SessionState, UIState, TabState
  backends.py   → stream_ollama / stream_openai_compat
  config.py     → 所有 env-var 常數
  models.py     → ModelProfile dataclass + REGISTRY
  session_store.py → SessionStore：session 封存／resume／命名（狀態只在磁碟上）
  tools/        → web_search / fetch_urls, bash_exec, dify_retrieve, run_tool
  mcp_client.py → MCP server 生命週期 + 工具呼叫
  tts.py        → TTS 引擎抽象（edge-tts, azure-tts, melotts, kokoro）
mixins/         → ChatApp 行為拆分（全部繼承 _MixinBase = ChatApp）
  streaming.py  → _send worker、串流回呼、TTS 播放
  agents.py     → /create-agent, /load-agent, /list-agents, /reload-agent
  app_tools.py  → AI 可呼叫的 app tools
  bash.py       → ! 指令執行
  mcp.py        → /mcp-list, /mcp-add, /mcp-del, /mcp-reload
  session.py    → /cd, /compact, /clear, /resume, /model 等
widgets/        → Textual TUI 元件
  input.py      → HistoryInput, ChatAutoComplete
  tree.py       → RightClickTree, TreeContextMenu, FileTree
  sidebar.py    → ResizeHandle, Sidebar
  chat.py       → ImageBubble, FileViewer, CopyButton, AIMessage …
  commands.py   → ChatCommands（命令面板）
  utils.py      → 剪貼簿、檔案圖示、_line_height 等共用工具
```

Import 規則：
- `core/` 內部用 relative imports（`.config`、`.models`）
- `widgets/` 內部用 relative imports（`.utils`）
- `widgets/` 可以 import `core/`（`from core.config import MODEL`）
- `core/` 不得 import `widgets/`

## Log 層級慣例

| 層級 | 用途 |
|------|------|
| `DEBUG` | 效能計時、token 統計、工具呼叫細節 |
| `WARNING` | 可恢復的失敗（搜尋重試、schema 查詢失敗） |
| `ERROR` | 工具呼叫完全失敗、MCP 錯誤 |

TUI 模式：log 寫入 `agent_tui.log`（`loguru` 設定於 `app.py`）。
CLI 模式：log 輸出到 stderr，不干擾 Rich 的 stdout 輸出。

## 對話行為規範

- **避免重複回答**：如果發現自己連續兩次回答相同的內容，立即停止並向使用者承認遇到邏輯錯誤。
- **優先執行指令**：收到明確指令時直接執行，不重複確認已理解的問題。

## Rich 渲染慣例

- AI 回覆用裸 `Markdown`，不包 Panel（避免複製時混入框線字元）。
- MCP 工具結果用 `Panel(expand=True)`，避免長內容溢出邊框。
- 搜尋結果用 `Table`（`box.SIMPLE_HEAD`），不加橫線。
- 思考過程用黃色 Panel。
