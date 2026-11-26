# DEVELOPMENT.md

開發筆記：設計決策、踩過的坑、不明顯的規則。

---

## 設定系統

三層，優先度由高到低：

```
.env              ← 使用者 override（實際生效）
core/config.py    ← 應用程式預設值（.env 未設定時生效）
core/models.py    ← 各模型的 num_ctx（NUM_CTX=0 時生效）
```

**規則：只在 `.env` 放真正需要 override 的東西。** 改預設值改 `core/config.py`；改特定模型的 context window 改 `core/models.py`。

### `.env` 應該有什麼

| 設定 | 說明 |
|------|------|
| `MODEL` | 目前使用的 Ollama 模型 |
| `SEARXNG_URL` | SearXNG API 位址（換機器時改） |
| `USERNAME` | 顯示名稱 |
| `CONTEXT7_API_KEY` | Context7 API 金鑰 |
| `NUM_CTX=8192` | 只在需要限制 VRAM 時才取消註解；否則用各模型預設值 |

所有其他調校參數（`MAX_TOOL_CALLS`、`MAX_SEARCH_CHARS` 等）**不應出現在 `.env`**，由 `core/config.py` 預設值控制。

### `NUM_CTX` 的語意

- `.env` 未設定（或 `NUM_CTX=0`）→ 每個模型用 `core/models.py` REGISTRY 裡各自的 `num_ctx`
- `.env` 設定 `NUM_CTX=N` → **覆蓋所有模型**，全部用 N

覆蓋行為在 `core/agent.py:Agent.__init__`：
```python
if NUM_CTX:
    self.profile.num_ctx = NUM_CTX
```

### `core/config.py` 預設值（2025-05 版）

| 常數 | 預設 | 說明 |
|------|------|------|
| `MAX_TOOL_CALLS` | 15 | 單輪工具呼叫上限；Playwright 多步瀏覽需要 10+ |
| `MAX_SEARCH_CHARS` | 8000 | MCP/搜尋結果截斷；browser_snapshot 需要 ≥8K 才看得到搜尋框 |
| `BASH_TIMEOUT` | 30 | bash_exec 逾時秒數 |
| `HISTORY_KEEP` | 10 | 保留的對話輪數 |

---

## 模型設定（`core/models.py`）

各模型的 `num_ctx` 對應 Playwright agentic 任務的需求：

| 模型 | num_ctx | 原因 |
|------|---------|------|
| `qwen3.5:4b` | 16384 | 多步工具呼叫累積 prompt，8192 不夠 |
| `qwen3-vl:4b` | 16384 | 同上 |
| `qwen3:8b` | 16384 | 同上 |
| `qwen3:4b` | 8192 | 無 vision，任務較輕 |
| `gemma3/4` | 8192 | 同上 |

`get_profile()` 回傳 `dataclasses.replace()` 的 copy，避免 `Agent.__init__` 修改 `num_ctx` 時汙染 REGISTRY。

---

## MCP 工具呼叫完整流程

以「幫我開啟 momo 搜尋 PEGASUS 41」為例：

```
使用者輸入
│
▼
┌─────────────────────────────────────────────────────┐
│  TUI: on_input_submitted                            │
│  → 把訊息渲染到畫面                                 │
│  → _send() worker 啟動（@work exclusive）           │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  run_turn() 準備階段（core/turn.py）                │
│  1. agent.prepare_turn() — 重建 system message      │
│  2. messages = agent.messages_for(user_msg)         │
│     ← 裁切到最近 N 輪 + 新的 user 訊息              │
│  3. 取得 agent.turn_lock、進入 turn_scope()         │
│  4. active_tools = [SEARCH, BASH] + MCP 39 個工具   │
│     每個工具帶完整 schema（description + params）   │
│     schema 來自 MCP server list_tools()，非 prompt  │
└──────────────────────┬──────────────────────────────┘
                       │
              ┌────────┴────────┐
              │   while True    │  ← tool calling loop
              └────────┬────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  agent.model_stream.step(messages, tools, …)        │
│  adapter 轉成 wire 格式：Ollama / OpenAI-compat     │
│  選項：num_ctx、think 由 profile 決定               │
└──────────────────────┬──────────────────────────────┘
                       │  串流回應
                       ▼
┌─────────────────────────────────────────────────────┐
│  adapter 逐 chunk 送 Delta，turn 轉成事件給 sink    │
│  thinking  → ThinkingDone → TUI 顯示思考框          │
│  text      → Token        → TUI 逐字渲染            │
│  tool_calls→ AssistantStep.tool_calls               │
└──────────────────────┬──────────────────────────────┘
                       │
          ┌────────────┴────────────┐
        空 tool_calls            有 tool_calls
          │                         │
          ▼                         ▼
┌──────────────────┐  ┌─────────────────────────────────┐
│ 正常結束這輪      │  │ 並發執行所有工具                 │
│ agent.commit(    │  │ asyncio.gather(                 │
│   user+assistant)│  │   run_tool(tc) for tc in …)     │
│ （依 persist）   │  │                                 │
│ sink(Done)       │  │ run_tool 分兩條路：              │
│ → TUI 顯示統計  │  │  ├─ MCP tool  → mcp_client.call │
│                  │  │  └─ 本地 tool → tools_map       │
└──────────────────┘  └──────────────┬──────────────────┘
                                      │
                                      ▼
                       ┌─────────────────────────────────┐
                       │  MCP 呼叫路徑                   │
                       │  mcp_client.call(               │
                       │    "browser_navigate",          │
                       │    {"url": "momoshop.com.tw"}   │
                       │  )                              │
                       │  ↓ JSON-RPC over stdio          │
                       │  Playwright MCP process         │
                       │  ↓ 控制 Chromium                │
                       │  ↓ 執行操作（導航/點擊/截圖…）  │
                       │  ↓ 回傳文字結果                 │
                       └──────────────┬──────────────────┘
                                      │
                                      ▼
                       ┌─────────────────────────────────┐
                       │  結果處理                       │
                       │  sink(ToolEvent) → TUI 工具卡片 │
                       │    （需開啟 /debug 模式才顯示） │
                       │                                 │
                       │  messages.append(               │
                       │    assistant + tool_calls)      │
                       │  messages.append(               │
                       │    tool result, 截斷至 8000 ch) │
                       │                                 │
                       │  tool_rounds += 1               │
                       │  >= MAX_TOOL_CALLS(15)?         │
                       │    → Done(reason="tool_limit")  │
                       └──────────────┬──────────────────┘
                                      │
                                      └──→ 回到 while True
                                           帶完整工具結果
                                           再呼叫模型
```

### history vs messages 的差異

| | `self.history` | `messages`（當輪） |
|---|---|---|
| 內容 | user + assistant 最終回覆 | 包含 tool_calls + tool_results |
| 持久化 | 寫入 history.json | 僅存在當次 run_turn() 中 |
| 下輪使用 | `agent.messages_for()` 重建 | 丟棄 |

### 模型如何知道工具用法

模型**不**靠 system prompt 學習工具用法。工具說明透過 Ollama `tools` 參數傳入（function calling 標準機制）：

```
system prompt → 工具名稱清單 + 優先順序規則（語境）
Ollama tools  → 每個工具的 description + parameters schema（呼叫方式）
```

Schema 來源：MCP server 在 `list_tools()` 時回傳，`core/mcp_client.py` 轉換為 Ollama 格式後加入 `active_tools`。

### Ollama tools 機制（我們不需要自己處理的部分）

我們只負責傳入 schema、接出結構化結果。中間的工作全由 Ollama 代勞：

```
我們                         Ollama                        模型
─────                        ──────                        ────
tools=[                      依 chat template              <|im_start|>system
  {                    →     把 schema 注入 prompt    →    你有以下工具：
    "type": "function",                                    {"name": "browser_navigate"
    "function": {                                           "description": "...",
      "name": "...",                                        "parameters": {...}}
      "description": "...",                                ...
      "parameters": {...}                                  <|im_end|>
    }
  }, ...
]
                             解析模型輸出的 tool call      輸出 JSON / XML 格式
                    ←        轉成結構化物件          ←     {"name": "browser_navigate",
                                                           "arguments": {"url": "..."}}

msg.tool_calls = [
  ToolCall(function=Function(
    name="browser_navigate",
    arguments={"url": "https://..."}
  ))
]
```

**注意**：模型輸出 tool call 的格式（JSON 或 XML）由 Ollama 的 chat template 決定，不同模型不同。Qwen3 系列有時輸出格式不正確的 XML，導致 Ollama parser 報 `XML syntax error`——這個 bug 在 Ollama 層，不在我們的程式碼中（我們對此加了 auto retry）。

---

## TUI 架構（`app.py` + `mixins/` + `widgets/`）

`app.py` 是 Textual TUI 的入口點。ChatApp 的行為透過 mixin 拆分：

```
app.py          ChatApp（組合所有 mixin；事件處理、tab 管理、layout）
mixins/
  streaming.py  _send worker（@work exclusive）、串流回呼、TTS 播放
  agents.py     /create-agent, /load-agent, /list-agents, /reload-agent, /delete-agent
  app_tools.py  22 個 AI 可呼叫的 app tools（切換模型、TTS、MCP…）
  bash.py       ! 指令執行、!cd 攔截
  mcp.py        /mcp-list, /mcp-add, /mcp-del, /mcp-reload
  session.py    /cd, /compact, /cls, /resume, /model, /nickname, /reload-config…
widgets/
  input.py      HistoryInput（Up/Down 歷史、Ctrl+Z undo、多行貼上）
                ChatAutoComplete（斜線指令、路徑補全）
  tree.py       FileTree（懶載入目錄樹，雙擊開啟）
                RightClickTree（攔截右鍵事件）
                TreeContextMenu（右鍵選單 ModalScreen）
  sidebar.py    Sidebar（cwd、模型、git、MCP 工具）
                ResizeHandle（拖拉分隔條）
  chat.py       AIMessage（Markdown 渲染）
                FileViewer（TextArea，Ctrl+C WSL 多行複製）
                ImageBubble（圖片訊息卡片）
                CapabilityBadge / CapLabel（Think / vision 標籤）
  commands.py   ChatCommands（Ctrl+P 命令面板）
  utils.py      _copy_to_clipboard（xclip / clip.exe）
                _file_icon（extension → emoji）
                _line_height 等 TUI 共用工具
```

### Tab 輸入狀態隔離（`streaming_tabs`）

`#prompt` 是全域唯一的 widget，不屬於任何 tab。當 sub-agent tab 串流時，`_send` 會把它 `disabled = True`，若此時切換到 main tab，prompt 仍然鎖住。

解法：`TabState.streaming_tabs: set[str]` 記錄哪些 tab_id 正在串流。

- `_send` 開始時：`streaming_tabs.add(my_tab_id)`
- `_send` 結束時（finally）：`streaming_tabs.discard(my_tab_id)`；只有用戶還在同一個 tab 才恢復 prompt
- `on_tabbed_content_tab_activated`：切換後查 `streaming_tabs`，決定 `prompt.disabled`

**不要**在 `finally` 裡無條件恢復 prompt——此時用戶可能已在另一個 tab，應該由 tab 切換事件負責狀態還原。

### MCP 生命週期

MCP client 用獨立的 `asyncio.create_task()` 長駐執行：

```python
# on_mount
self._mcp_task = asyncio.get_event_loop().create_task(self._mcp_lifecycle())

async def _mcp_lifecycle(self):
    await mcp.start()
    await self._mcp_done.wait()   # 等待退出訊號
    await mcp.stop()              # start/stop 同一 task
```

**為什麼不用 `@work`**：anyio cancel scope 必須在同一個 asyncio task 中 enter/exit。若 `start()` 和 `stop()` 在不同 task，會 deadlock 或 `RuntimeError`。`asyncio.create_task()` 保證整個生命週期在同一 task。

**退出流程**（`action_quit`）：
1. `_mcp_done.set()` → 讓 `_mcp_lifecycle` 解除 wait，進入 finally 執行 `mcp.stop()`
2. `asyncio.wait_for(_mcp_task, timeout=2.0)` → 等 MCP 正常關閉
3. `agent.save_history()` → 確保歷史寫入（包含串流中途強制退出的情況）
4. `self.exit()`

### MCP stderr 處理

MCP stdio server 啟動時會印 startup log，會汙染 TUI 畫面。`MCPClient` 接受 `errlog: TextIO` 參數；TUI 傳 `open(os.devnull, 'w')` 靜音：

```python
mcp = MCPClient(servers, errlog=open(os.devnull, "w"))
```

注意：必須是有真實 file descriptor 的物件（`fileno()` 可用）。`io.TextIOBase` 子類沒有 `fileno()`，anyio 會拋 `UnsupportedOperation`。

### 滑鼠事件洩漏到 Input

Playwright 或 WSLg 切換視窗焦點時會重置 terminal mouse tracking，產生 SGR/X10 binary 序列洩入 Textual `Input`：

```
ESC[<35;80;25M  →  Textual 吃掉 ESC[，剩 <35;80;25M 逐字進 Input
```

處理方式（`on_input_changed`）：偵測到任何 mouse sequence pattern 就直接清空整個 input，因為 user 是在移動滑鼠，不是打字：

```python
_MOUSE_SEQ = re.compile(r"(?:\x1b\[?)?<?(?:\d+;)+\d+[Mm]")

def on_input_changed(self, event):
    if _MOUSE_SEQ.search(event.value):
        event.input.value = ""
        return
```

### 工具呼叫錯誤顯示

`run_turn`（`core/turn.py`）不會因為模型或後端錯誤 raise，錯誤一律變成 `Done`，由 UI 顯示：

1. **模型 / 後端錯誤**（context 溢出、逾時、配額等）：`Done(reason="error", error=TurnError(kind))`，kind 是 `overflow` / `timeout` / `quota` / `other`；overflow 會先壓縮一次再重試
2. **`MAX_TOOL_CALLS` 超限**：`Done(reason="tool_limit")`，進度摘要照樣寫進 history

只有程式 bug 才會 raise，UI 消費者在 `try/except` 接住以免 `@work` 關掉整個 app。

---

## 已知限制

- `browser_snapshot` 回傳 ~400KB accessibility tree；`MAX_SEARCH_CHARS=8000` 截斷後模型只看到前 8K chars。複雜頁面可能需要更大的值（留意 prompt token 數）。
- Playwright 在 WSL2 需要 `--no-sandbox`（Chromium sandbox 需要 kernel namespace，WSL2 不支援）。
- `browser_navigate` 預設用 playwright-managed `chrome-for-testing`；系統 Chrome 不適用，要另外安裝：`npx @playwright/mcp install-browser chrome-for-testing`。
