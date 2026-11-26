# Harness Architecture：kai 的 Agent 基礎設施實作

> **Agent = Model + Harness** — LangChain, 2025

## 目錄

1. [什麼是 Harness](#什麼是-harness)
2. [kai 的 Harness 架構總覽](#kai-的-harness-架構總覽)
3. [Builder Harness：基礎設施](#builder-harness基礎設施)
   - [3.1 工具抽象層（Tool Layer）](#31-工具抽象層tool-layer)
   - [3.2 MCP 橋接層（MCP Bridge）](#32-mcp-橋接層mcp-bridge)
   - [3.3 模型路由器（Model Router）](#33-模型路由器model-router)
   - [3.4 Orchestration Loop](#34-orchestration-loop)
   - [3.5 Context 管理](#35-context-管理)
   - [3.6 System Prompt Factory](#36-system-prompt-factory)
   - [3.7 Sandbox（執行環境）](#37-sandbox執行環境)
4. [User Harness：使用者層疊規則](#38-user-harness使用者層疊規則)
5. [設計決策記錄](#4-設計決策記錄)
6. [Harness 地圖 vs 市面框架](#5-harness-地圖-vs-市面框架)

---

## 什麼是 Harness

Harness（馬具 / 固定裝置）在 Agent 架構中指**模型之外的一切**。

```
LLM（模型）          Harness（基礎設施）
                    ┌──────────────────────┐
  原始參數           │  工具呼叫層            │
  文字生成           │  System Prompt 建構    │
  機率分佈           │  Context 管理          │
                    │  Tool orchestration    │
                    │  Sandbox / 隔離        │
                    │  Error handling        │
                    └──────────────────────┘
```

Martin Fowler 進一步把 Harness 分成兩層：

- **Builder Harness** — 開發者寫的程式碼，是 Agent 能運作的基礎設施
- **User Harness** — 使用者在 builder harness 之上疊加的 rules、conventions、prompt patterns

**kai 的程式碼絕大部分是 Builder Harness**：它幫模型搭建了工具層、orchestration、context pipeline，讓模型能真正「做事」。

---

## kai 的 Harness 架構總覽

```
┌──────────────────────────────────────────────────────────────┐
│  kai：Builder Harness                                         │
│                                                               │
│  ┌──────────┐   ┌──────────────┐   ┌────────────────────┐   │
│  │ CLI/main │   │ TUI/app.py   │   │  notes.md    │   │
│  │ 入口     │   │ Textual TUI  │   │ (User Harness)     │   │
│  └────┬─────┘   └──────┬───────┘   └────────────────────┘   │
│       │                │              │                       │
│       ▼                ▼              ▼                       │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  Agent (core/agent.py)                                 │ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │  Orchestration Loop (chat method)                │  │ │
│  │  │  ┌──────────┐  ┌───────────┐  ┌────────────┐   │  │ │
│  │  │  │ Streaming│→│ Tool Call  │→│ Result     │   │  │ │
│  │  │  │ Backend  │  │ Detection  │  │ Injection  │   │  │ │
│  │  │  └──────────┘  └───────────┘  └────────────┘   │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  ├─ System Prompt Factory (_rebuild_system_prompt)        │ │
│  ├─ Context Manager (_trim_history / compact_history)     │ │
│  ├─ Tool Dispatcher (run_tool)                            │ │
│  └─ 後端 Adapter (Ollama / OpenAI compat)                │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐ │
│  │ MCP      │   │ SearXNG  │   │ Jina     │   │ Bash     │ │
│  │ Bridge   │   │ 搜尋引擎  │   │ Reader   │   │ Sandbox  │ │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘ │
└──────────────────────────────────────────────────────────────┘
```

---

## Builder Harness：基礎設施

### 3.1 工具抽象層（Tool Layer）

**Location**：`core/tools/`

kai 的工具層解決一個核心工程問題：**如何讓 LLM 產生的 JSON tool call 安全地對應到真正的系統操作**。

#### 架構

```python
# core/tools/dispatch.py：三級分發

SEARCH_TOOL     # → web_search(query)      → SearXNG API
FETCH_URLS_TOOL # → fetch_urls(urls)       → Jina Reader
BASH_TOOL       # → bash_exec(command)     → subprocess

# 分發邏輯（在 agent.py 的 chat() 內）
results = await asyncio.gather(
    *[run_tool(tc, self.tools_map, self.mcp_client) for tc in tool_calls]
)
```

其中 `run_tool` 做兩層 dispatch：

1. 若工具名稱在 MCP 命名空間 → 走 `MCPClient.call()`
2. 否則 → 走 `self.tools_map[fn_name](**raw)`

#### Harness 層面的意義

工具層是 harness 的核心價值——沒有工具層，模型只能生成文字；

有工具層，模型才能**影響外部世界**。

#### 設計決策

| 決策 | 理由 |
|------|------|
| 工具 schema 硬寫在 `tools/`，不從 OpenAPI 自動產生 | 保持 schema 描述精準可控，避免模型混淆 |
| 用 `Pydantic BaseModel` 做入參型別檢查 | 防止模型傳入畸形參數造成 crash |
| `tenacity` exponential backoff 搜尋重試 | 增加魯棒性，搜尋服務不穩時自動重試 |
| 結果長度控制（`budget_results`, `trim_tool_result`） | 避免工具結果撐爆 context window |

---

### 3.2 MCP 橋接層（MCP Bridge）

**Location**：`core/mcp_client.py`

MCP（Model Context Protocol）是 kai 的**工具擴展機制**。kai 不是硬編碼所有工具，而是透過 MCP 協定**動態掛載**外部 server 提供的工具。

#### 架構

```python
class MCPClient:
    async def start(self):
        # 對每個 server 建立 ClientSession
        # 呼叫 list_tools() 取得工具清單
        # 轉換為 function calling schema

    async def call(self, tool_name, arguments):
        # 查 _tool_to_server 找到對應 server
        # 呼叫 session.call_tool(tool_name, arguments)
```

支援兩種 transport：

- **stdio**：MCP server 是子程序，透過 stdin/stdout JSON-RPC 通訊
- **http**：MCP server 是 HTTP 服務，透過 SSE 串流

#### Harness 層面的意義

MCP 橋接層讓 kai 的工具集**不受核心程式碼限制**。使用者在 `mcp_servers.json` 加一行就能掛載 Playwright（瀏覽器自動化）、Filesystem（檔案操作）、SQL 查詢……等工具，不需要改 kai 的程式碼。

這是 harness 的可擴展性關鍵。

#### 設計決策

| 決策 | 理由 |
|------|------|
| `force_kill()` 用 `pkill -P <ppid>` 砍子程序 | `async stop()` 在 anyio cancel scope 下會 deadlock，同步殺子程序是逃生門 |
| `AsyncExitStack` 管理 session 生命週期 | 保證 startup 失敗時已開啟的資源正確關閉 |
| MCP TOOL TIMEOUT 預設 60 秒 | 防止工具 hang 住卡死整個 agent loop |
| schema 統一轉為 OpenAI function calling 格式 | 三種後端（Ollama/Gemini/DeepSeek）共用同一套描述 |

---

### 3.3 模型路由器（Model Router）

**Location**：`core/models.py`, `core/agent.py`

Harness 需要知道「模型能做什麼」才能正確驅動它。

#### ModelProfile

每個模型有一個 profile 描述其能力邊界：

```python
@dataclass
class ModelProfile:
    name: str
    supports_thinking: bool = False   # 有 <think> block
    supports_vision: bool = False     # 能吃圖片
    supports_tools: bool = True       # 能輸出 tool_calls
    num_ctx: int = 4096               # context window
    backend: str = "ollama"           # ollama / gemini / deepseek
```

#### 後端 Adapter 策略模式

```python
# agent.py __init__：client 在啟動時建立，跨請求重用
self._gemini = AsyncOpenAI(
    api_key=GEMINI_API_KEY,
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
) if GEMINI_API_KEY else None

self._deepseek = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
) if DEEPSEEK_API_KEY else None

# agent.py chat()：根據 profile 選 client
if self.profile.backend == "gemini":
    stream = await self._gemini.chat.completions.create(...)
elif self.profile.backend == "deepseek":
    stream = await self._deepseek.chat.completions.create(..., extra_body={"thinking": ...})
else:  # ollama
    stream = await self._ollama.chat(...)
```

#### Harness 層面的意義

模型路由器是 harness 的「相容層」。它讓同一個 orchestration loop 可以跑在本地模型（Ollama）、雲端模型（Gemini、DeepSeek）上。切換模型只需要改 `MODEL` 環境變數，不用改程式碼。

---

### 3.4 Orchestration Loop

**Location**：`core/agent.py` — `Agent.chat()`

這是 harness 的「大腦」——決定什麼時候呼叫 LLM、什麼時候執行工具、什麼時候結束。

```
使用者訊息
  │
  ▼
Rebuild system prompt（注入日期、cwd、MCP context）
  │
  ▼
┌─────────────────────────────────────────────────┐
│ Loop（最多 MAX_TOOL_CALLS = 15 次）：            │
│                                                  │
│  1. 呼叫 LLM（streaming）                        │
│  2. 收集 reply + tool_calls                     │
│  3. 有 tool_calls？                              │
│     ├─ 是 → asyncio.gather 並行執行所有工具      │
│     │      結果 append 回 messages               │
│     │      tool_call_count += 1                  │
│     │      └─ 回到步驟 1                          │
│     └─ 否 → 輸出最終 reply，結束                 │
└─────────────────────────────────────────────────┘
```

#### 關鍵實作細節

**並行工具執行**：

```python
results = await asyncio.gather(
    *[run_tool(tc, self.tools_map, self.mcp_client)
      for tc in tool_calls]
)
```

當 LLM 一次要求多個工具（如同時搜尋多個關鍵字），kai 會平行執行而非序列執行。這是 agentic loop 效能的關鍵優化。

**回拋工具結果後消息格式**（Ollama）：

```python
# Assistant 宣告要呼叫工具
{"role": "assistant", "content": "我要搜尋...", "tool_calls": [...]}

# 工具執行完
{"role": "tool", "name": "web_search", "content": "結果..."}
```

**回拋工具結果後消息格式**（Gemini / DeepSeek）：

```python
# 使用 tool_call_id 對應
{"role": "tool", "tool_call_id": "call_xxx", "content": "結果..."}
```

**取消支援**：

```python
# 使用者按 Esc 或 TUI 點 cancel
agent._cancelled = True
# agent.py 的 stream loop 會在下次 chunk 到來時 break
# 同時移除最後一則 user message，不汙染 history
```

---

### 3.5 Context 管理

**Location**：`core/agent.py` — `_trim_history()`, `compact_history()`, `_rotate_history()`, `save_history()`

Harness 必須解決 LLM 的固有缺陷：**無狀態**、**context window 有限**、**舊對話會污染新對話**。

kai 的 context 管理有三層：

#### 層級 1：每次請求修剪（`_trim_history`）

```python
def _trim_history(self) -> list[dict]:
    """保留 system message + 最近 HISTORY_KEEP 則訊息。"""
    system = [m for m in self.history if m["role"] == "system"]
    rest = [m for m in self.history if m["role"] != "system"]
    return system + rest[-HISTORY_KEEP:]
```

預設只送最近 10 則對話給模型。

#### 層級 2：記憶輪換（`_rotate_history`）

```python
def _rotate_history(self):
    """記憶條數超過 HISTORY_KEEP * 2 則時自動移除最舊的非-system 訊息。"""
```

啟動每次對話後修剪歷史總量。

#### 層級 3：摘要壓縮（`compact_history`）

```python
async def compact_history(self) -> str:
    # COMPACT_MODEL 優先；未設時有 Gemini key 用 gemini-2.5-flash，否則 deepseek-flash
    _compact_client = self._gemini or self._deepseek
    _compact_model = _cfg.COMPACT_MODEL or (COMPACT_FALLBACK_DEEPSEEK if not self._gemini and self._deepseek else COMPACT_FALLBACK_GEMINI)
    # 把整段 history 送給摘要模型
    # 回傳摘要文字，取代原有 history（只保留 system + 摘要）
```

當對話累積太長時，用另一個模型把整段對話壓成摘要，大幅節省 context。

#### Harness 層面的意義

沒有 context 管理，LLM 會：
- 忘記前面說過的話
- 超出 context window 報錯
- 被舊對話的錯誤資訊污染

這三層機制確保 kai 能在有限 context 下維持**長期一致性**。

---

### 3.6 System Prompt Factory

**Location**：`core/agent.py` — `_rebuild_system_prompt()`

Harness 要在每次對話前重建 system prompt，因為**環境資訊會變動**：

```python
def _rebuild_system_prompt(self):
    now = datetime.now().strftime("%Y-%m-%d")   # 日期會變
    cwd = os.getcwd()                            # 目錄會變
    mcp_server_desc = ...                        # MCP 連線狀態會變

    # 檢查 notes.md（使用者撰寫的 User Harness）
    notes_path = Path(cwd) / ".kai" / "notes.md"
    notes = notes_path.read_text().strip() if notes_path.exists() else ""

    system_msg = {
        "role": "system",
        "content": (
            f"目前時間是 {now}（台灣時區 UTC+8）。"
            f"使用者的名字是 {USERNAME}。"
            + (f"\n\n## 📝 專案記憶\n{notes}" if notes else "")
            + (
                f"\n\n你已連接以下 MCP server：{mcp_server_desc}。"
                f"\n\n## 📁 工作目錄\n- 目前工作目錄：{cwd}"
                + "\n\n## 🛠️ 執行與排錯 SOP"
                + ("\n- 瀏覽器操作：僅限使用 Playwright MCP ..." if has_playwright else "")
                # ... 動態根據 MCP 工具組合產生的 SOP
                if has_mcp else ""
            )
        ),
    }
```

#### 設計重點

- **All-or-nothing 重建**：整個 system message 每次都重新產生，不 patch
- **狀態感知**：根據 MCP 工具組合動態調整 system prompt 內容（有 Playwright 就加 browser SOP、有 filesystem 就加檔案操作限制）
- **User Harness 注入**：讀取 `notes.md` 讓使用者能疊加自己的規則
- **時間資訊**：防止模型用舊的訓練資料回答時間敏感問題

---

### 3.7 Sandbox（執行環境）

**Location**：`core/tools/` — `bash_exec()`

bash 執行是 harness 風險最高的部分——它讓模型能執行任意 shell 指令。

#### 實作

```python
async def bash_exec(command: str, timeout: int = 0) -> str:
    t = timeout or BASH_TIMEOUT
    proc = await asyncio.create_subprocess_shell(command, ...)

    # 採用 proc.wait() + 獨立讀取 task
    # 而非 communicate()，因為 communicate() 會等所有輸出讀完

    try:
        await asyncio.wait_for(proc.wait(), timeout=t)
    except TimeoutError:
        proc.kill()
        return f"[Timeout] 超過 {t}s"
```

#### 設計決策

| 決策 | 理由 |
|------|------|
| 用 `create_subprocess_shell` 而非 `create_subprocess_exec` | 支援 pipe、chain command（`&&`、`|`） |
| 用 `proc.wait()` + 獨立 reader 而非 `communicate()` | 背景程序不會死鎖，支援即時輸出 |
| 超時強制 `proc.kill()` | 防止殭屍程序消耗資源 |
| timeout 可透過 `bash_timeout` 參數覆寫 | 長時間任務（如編譯）不會被預設 30s 卡住 |
| **無指令白名單** | 權衡：靈活性優先，安全性靠使用者環境隔離 |

---

## User Harness：使用者層疊規則

kai 提供兩個使用者可介入的 harness 層：

### .kai/notes.md

放在專案根目錄的 Markdown 檔案，kai 啟動時自動讀取並注入 system prompt：

```markdown
# .kai/notes.md
- 這個專案使用 uv 管理 Python 套件，不要用 pip install
- API endpoint 在 config/api.toml，不要寫死在程式碼
- 前端使用 React + Tailwind，icon 用 lucide-react
```

這是一種 **feedforward control**（先發控制）：使用者預先告訴模型專案的慣例，讓模型從第一次對話就不會犯錯。

### System Prompt 內的規則區塊

kai 自動在 system prompt 中插入的 SOP（如「嚴禁盲目重試」、「嚴禁捏造」）是 kai 本身對模型的「強制 harness 規則」，使用者也可以透過修改 `_rebuild_system_prompt()` 來客製化這些規則。

---

## 4. 設計決策記錄

以下是在 building kai harness 過程中做出的關鍵取捨：

### 4.1 為什麼不直接用 LangChain / 其他框架

| 因素 | 自幹 Harness | LangChain |
|------|-------------|-----------|
| 程式碼透明度 | 完整可控 | 多層抽象，debug 困難 |
| 依賴 | 只有 httpx + pydantic | 幾十個套件互相依賴 |
| 客製化 | 任意改 | 被框架設計限制 |
| MCP 支援 | 原生整合 | 需插件橋接 |
| 開發速度 | 初期慢 | 初期快 |

kai 選擇自幹 harness，因為它本身就是造輪子的專案——目的就是理解 harness 的每個環節。

### 4.2 為什麼 Ollama 走 streaming + 獨立讀取

kai 的 bash_exec 選用 `proc.wait()` + `asyncio.StreamReader` 而非 `proc.communicate()`：

- `communicate()` 等所有 stdout/stderr 讀完才回傳，無法實現**即時輸出**
- 即時輸出在 TUI 模式是必要功能（使用者要看 command 執行進度）
- **權衡**：程式碼較複雜，但功能完整

### 4.3 為什麼 MCP 用 AsyncExitStack

MCP server 有多層資源需要管理（stdio process、ClientSession、httpx client），`AsyncExitStack` 保證：

1. startup 失敗 → 已開啟的資源自動關閉
2. shutdown 正確 → reverse order cleanup
3. 不用手動追蹤哪些 server 已啟動、哪些還沒

### 4.4 為什麼用 `pkill -P` 做 force_kill

`MCPClient.stop()` 是 async 方法，正常 shutdown 會走 `await self._stack.aclose()`。但在某些 anyio cancel scope 情境下，async close 會 deadlock（因為 anyio 在 cancel 時不允許新的 coroutine 進入 event loop）。

`force_kill()` 是逃生門——同步殺掉所有子程序，不依賴 event loop。

---

## 5. Harness 地圖 vs 市面框架

| Harness 元件            | kai                          | Claude Code          | OpenAI Codex CLI      |
|-------------------------|------------------------------|----------------------|-----------------------|
| **Tool Layer**          | 三級分發（本地 / MCP / 外部）| 插件系統             | 插件系統              |
| **MCP Bridge**          | 原生支援 stdio + http        | 原生支援             | 有限支援              |
| **Model Router**        | Ollama / Gemini / DeepSeek   | 封閉（Claude only）  | 封閉（GPT only）      |
| **Orchestration**       | 自幹 loop                    | 黑箱                 | 黑箱                  |
| **Context Mgmt**        | trim + rotate + compact      | 黑箱                 | 黑箱                  |
| **Sandbox**             | subprocess shell             | Docker container     | 受限 sandbox          |
| **System Prompt Factory** | 公開原始碼                 | 不可見               | 不可見                |
| **User Harness**        | notes.md               | CLAUDE.md / rules    | Custom instructions   |
| **TUI**                 | Textual                      | terminal-only        | web UI                |

kai 的獨特定位在於：
- **開源**：整個 harness 的每一行都公開
- **多後端**：不綁定單一模型
- **MCP 原生**：工具擴展機制與 VSCode / Cursor 相容
- **輕量**：無大型框架依賴，核心邏輯不到 2000 行

---

## 結語

kai 的 harness 實作說明了 Agent 的基礎設施層需要解決哪些工程問題：

1. 工具如何被模型安全地呼叫
2. 多種後端如何統一接入
3. Context 如何在有限視窗下管理
4. 環境資訊如何動態傳遞給模型
5. 使用者如何疊加自己的規則
6. 高風險操作（bash）如何在安全與彈性間取捨

每一項都不是模型本身的能力，但每一項都決定了模型能否「真正有用」。
