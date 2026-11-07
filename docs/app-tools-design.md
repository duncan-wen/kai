# App-Level Tools

## 背景

kai 有許多 `/指令`（create-agent、load-agent、cd、model…），目前只能由使用者手動輸入。
目標：讓 AI 能以自然語言理解使用者意圖，自動呼叫對應的 app 操作。

---

## 設計

### 採用方案：App-level tools（callback 注入）

在 `Agent` 上加三個 optional 欄位：

```python
self.extra_tools: list[dict] = []       # JSON schema（description 給 AI 讀）
self.extra_tools_map: dict = {}         # 實作（callback 到 ChatApp 方法）
self.extra_tools_note: str = ""         # 注入至 system prompt 的使用規則
```

`ChatApp.__init__` 呼叫 `_inject_app_tools(self.agent)` 注入 main agent。
`Agent.chat()` 把 `extra_tools` 合進 `active_tools`、`extra_tools_map` 合進 tool dispatch。

**只有 main agent 獲得這些工具**，sub-agent tab 不注入。
例外：切換模型（`action_switch_model`）會建立新 Agent 物件，`AppToolsMixin` override 後重注入。

### 為何不做成按需載入（skill）

- 跟現有工具一致：`SEARCH_TOOL`、`BASH_TOOL`、MCP tools 都是每次全部帶上
- 現代 LLM 可處理 20+ 工具，重點是 description 寫清楚
- 按需載入需要「何時載入哪組工具」的額外邏輯，複雜度高收益低
- 若未來 token 成本成問題，可加 `enabled_extra_tools: set[str]` 篩選

---

## LLM Tool-Calling 機制

### 每次 API call 送出的內容

```
messages = [
    {"role": "system", "content": "...system prompt + extra_tools_note..."},
    {"role": "user",   "content": "..."},   # history (trimmed to HISTORY_KEEP)
    ...
    {"role": "user",   "content": "目前這則輸入"},
]
tools = built-in tools + MCP tools + extra_tools   # 全部合併
```

### Tool-calling 來回（不寫入 history）

```
用戶：「幫我建一個寫 SQL 的助手，然後開啟它」
  ↓
LLM → tool_call: create_agent(name="SQL助手", model="...", system_prompt="...")
  ↓  "✅ 已建立 agent：SQL助手"
LLM → tool_call: load_agent(name="SQL助手")
  ↓  "✅ 已開啟 agent：SQL助手"
LLM 最終回覆：「已幫你建立並開啟 SQL 助手，可以直接開始對話了」
```

Tool 來回只活在 `Agent.chat()` 的局部 `messages` 裡，**不寫入 history**。

---

## 已實作的 Tools（17 個）

| tool 名稱 | 對應 slash 指令 | 說明 |
|----------|---------------|------|
| `list_agents` | `/list-agents` | 回傳文字清單，AI 可讀取後決策 |
| `create_agent` | `/create-agent` | AI 自行填 schema，直接呼叫 `save_agent()` |
| `load_agent` | `/load-agent` | 開啟 agent tab |
| `close_agent` | `/close-agent` | 關閉 agent tab；無 name 時若只有一個 tab 直接關 |
| `delete_agent` | `/delete-agent` | 直接刪除，跳過 UI 確認對話框 |
| `reload_agent` | `/reload-agent` | 熱更新執行中 agent 的 config.json |
| `change_dir` | `/cd` | 切換工作目錄 |
| `switch_model` | `/model` | 切換 AI 模型 |
| `remember` | `/remember` | 寫入 `.kai/notes.md` |
| `rename_session` | `/rename` | 命名目前 session |
| `reload_config` | `/reload-config` | 重載 `.kai/config.json` |
| `compact_history` | `/compact` | 壓縮對話歷史，釋放 context |
| `list_mcp` | `/mcp-list` | 回傳已連線 MCP server 及工具清單 |
| `add_mcp` | `/mcp-add` | 新增 MCP server 到 `~/.kai/mcp_servers.json` |
| `remove_mcp` | `/mcp-del` | 移除 MCP server |
| `reload_mcp` | `/mcp-reload` | 重新連線所有 MCP server |
| `set_tts` | `/tts` | 切換語音引擎或關閉語音 |

### 不開放的指令

| 指令 | 原因 |
|------|------|
| `/clear` | 純清畫面，AI 呼叫無意義 |
| `/cls` | 破壞性（歸檔清空），使用者主導 |
| `/ask` / `/save` | meta 指令 |
| `/resume` | 開 modal picker，純 UI |
| `/debug` / `/think` | 使用者偏好 toggle |
| `/image` | 使用者選檔案的動作 |
| `/nickname` | 使用者偏好 |
| `/init-config` | 開 modal |

---

## 架構細節

### 檔案

| 檔案 | 變更內容 |
|------|---------|
| `core/agent.py` | 新增三個 extra_tools 欄位；`chat()` 合入 dispatch；`_rebuild_system_prompt()` 附加 note |
| `mixins/app_tools.py` | 新增 `AppToolsMixin`；17 個 tool schemas + callbacks；`_inject_app_tools()`；override `action_switch_model` 重注入 |
| `app.py` | `ChatApp` 繼承加入 `AppToolsMixin`（`SessionMixin` 之前）；`__init__` 呼叫 `_inject_app_tools`；新增 `/close-agent` |

### MRO

```
ChatApp → MCPMixin → AgentsMixin → AppToolsMixin → SessionMixin → BashMixin → StreamingMixin → App
```

`AppToolsMixin` 在 `SessionMixin` 之前，確保 `action_switch_model` override 的 `super()` 正確鏈到 `SessionMixin`。

### `create_agent` 的特殊設計

原本 `/create-agent` 會發第二次 AI call 設計 agent schema。改為 tool 後，**呼叫的 AI 自己就是設計者**，直接在 tool_call arguments 填入所有欄位，callback 只需呼叫 `save_agent()`。省去 nested AI call。

### `close_agent` 的安全設計

- 主 chat tab 不在 `_agent_tabs` 裡，永遠不會被關閉
- 不帶 name：只有一個 agent tab → 直接關；多個 → 列出讓使用者指定
- 帶 name：模糊比對 `display_name` / `nickname`

### 防 hallucination（AI 假裝執行）

部分 thinking 模型在取得明確意圖後，可能直接用文字假裝已操作而不發出 tool_call。雙層防線：

1. **每個 action tool 的 `description`** 加 `**必須呼叫此工具...嚴禁只用文字假裝**`
2. **`extra_tools_note`** 注入 system prompt，統一聲明所有 app tools 必須透過 tool call 執行
