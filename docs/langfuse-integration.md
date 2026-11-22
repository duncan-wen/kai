# Langfuse 可觀測性串接 — 已實作

> 狀態：**已實作並驗證**（2026-11-23）。本文前半保留原始背景與整合路徑分析，後半為實際落地內容。

---

## 背景與動機

kai 的 LLM 呼叫鏈：

```
mixins/streaming.py ─▶ Agent.chat() ─▶ _run_turn() ─▶ core/backends.py
                                              ├─ stream_ollama()        (ollama 套件)
                                              └─ stream_openai_compat() (openai AsyncOpenAI)
                                              └─ run_tool() → core/tools/*
```

導入 Langfuse 補上 latency、token 成本、agent 層級 trace 的可視化。

## 整合路徑（定案：手動 SDK 埋點）

| 路徑 | 適合度 | 說明 |
|------|--------|------|
| **A. 手動 SDK 埋點** | ✅ 採用 | 兩條 backend 皆自寫串流，ollama 那條 OpenAI 自動整合蓋不到 |
| B. OpenTelemetry | 備選 | 未採用 |
| C. OpenAI SDK 自動整合 | ❌ 只蓋一半 | 僅對 stream_openai_compat 有效 |

---

## 實際實作

### 1. lazy singleton client — `core/langfuse_client.py`

核心原則「未設憑證 = no-op」，不影響沒使用 langfuse 的使用者：

- `get_langfuse()`：未設憑證回傳 `None`，全專案自動跳過
- helper：`trace()` / `generation()` / `tool()` / `agent()`（封裝 Langfuse SDK `start_as_current_observation`）
- `flush()`：app 關閉時呼叫
- `_ascii_session_id()`：session id 非 ASCII（如中文）轉 sha1，滿足 `propagate_attributes` 的 US-ASCII 限制
- `_mask_args()`：遮罩 key/secret/token/api 等敏感參數

### 2. 埋點位置

| 位置 | 層級 | 名稱 / 記錄內容 |
|------|------|----------------|
| `core/agent.py::chat` | trace span | `chat-turn`：session_id、model、input（user 文字）、output（reply） |
| `core/backends.py::stream_ollama` | generation | `llm-ollama`：model、input、output（含 **thinking capture**）、usage |
| `core/backends.py::stream_openai_compat` | generation | `llm-openai-compat`：同上 |
| `core/tools/dispatch.py::run_tool` | tool | tool name、args（遮罩後）、result |
| `core/tools/dispatch.py`（subagent dispatch） | **agent** | `send_to_agent` / `load_agent` 等 → type 為 `agent`，Agent Graph 顯示子 agent 節點 |
| `core/agent.py::compact_history` | generation | 摘要該次用量 |
| `app.py`（退出） | flush | 避免 TUI 退出掉資料 |

### 3. session 分組 — `core/session_store.py`

`SessionStore.session_id` property（session 概念的家，語意正確）：

- 已命名 session → 回 session 名（中文由 `_ascii_session_id` 轉 sha1）
- 未命名 → `history-<history檔 ctime>`，同 session 跨 turn 穩定、可讀且唯一

> 注意：`_history_file.stem` 對所有 agent 都固定是 `history`，不能用它當 session id（否則全混成一組）。已改用 `SessionStore.session_id`。

### 4. 設定 — `config_registry.py` + `.env`

```bash
# .env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://jp.cloud.langfuse.com   # Cloud region；自架改 LANGFUSE_HOST
```

registry 已註冊 `langfuse_public_key` / `langfuse_secret_key` / `langfuse_base_url` 三 entry。

---

## 對照官方 best practice（self-audit 已通過）

對照 `github.com/langfuse/skills` 的 `instrumentation.md` 與 `best-practices` 頁面：

| 要求 | 狀態 |
|------|------|
| Model name | ✅ `deepseek-v4-pro` |
| Token usage | ✅ `usage_details: {input, output, total}` |
| 描述性 trace name | ✅ `chat-turn` / `llm-ollama` / `llm-openai-compat` |
| Span hierarchy（單一 parent 嵌套） | ✅ trace → generation → tool/agent |
| Correct observation types | ✅ generation / tool / **agent** 四型態正確 |
| Sensitive data masked | ✅ `_mask_args` 遮罩 tool 參數；system prompt 每 turn 僅首輪記錄（可用 `LANGFUSE_TRACE_SYSTEM_PROMPT=false` 完全排除） |
| Capture thinking | ✅ generation output 含 thinking |
| session_id 分組 | ✅ 命名 / 未命名（時間戳 fallback）皆可區分 |

## 驗證

- ✅ pytest **116 全過**（含 session_id、ascii、遮罩、system prompt 策略等單元測試）
- ✅ ruff 全過
- ✅ self-audit：送出真實 trace → fetch 回來逐項對照（上表）

---

## system prompt 追蹤策略（避免 log 膨脹）

一輪對話若含 tool calling loop，會有多次 LLM 呼叫（`generation` span）。若每次都記完整
messages，log 會隨輪數線性膨脹；但完全不記又無法還原當下的動態上下文。折衷做法：

| 位置 | 內容 |
|------|------|
| `chat-turn` metadata | `system_sha`（sha256 前 12 碼）、`system_chars`、`generations`（本 turn 呼叫次數） |
| 本 turn **第 1 次** `generation` | 完整 system prompt（截斷 6000 字）+ 尾部 6 則非 system 訊息 |
| 本 turn **第 2～N 次** `generation` | `{"role":"system","content":"[same as turn-1 generation]"}` + 尾部 6 則 |

`system_sha` 讓你能在 dashboard 上直接看出 system prompt 有沒有變（專案記憶、skills 索引
會隨時間變動），而不必比對全文。實作：`core/langfuse_client.py` 的 `trim_messages` /
`system_fingerprint`，搭配 `core/backends.py` 的 `_generation_messages`（per-turn 計數
`agent._lf_gen_idx`，於 `chat()` 開頭重置）。

## Sessions（Messages 視圖）修復 — 2026-11-23

**症狀**：Sessions 頁面看不到任何對話，trace 樹長成「1 個 root 底下掛 1.1～1.6 六個
chat-turn」。

**兩個根因**：

1. **訊息形狀不合**：Langfuse Sessions 只讀 trace **根節點**的 `input` / `output`，且必須是
   `{"role", "content"}` 形狀才渲染成對話泡泡。原本寫的是 `{"user": ...}` / `{"reply": ...}`，
   所以有資料但渲染不出來。
2. **巢狀而非獨立 trace**：`chat-turn` 若在已有外層 span 的 context 中被建立，會變成子節點，
   而 Sessions 只看根節點 → 讀不到。

**修法**：

| 位置 | 改動 |
|------|------|
| `core/langfuse_client.py` | 新增 `user_message()` / `assistant_message()` 產生標準訊息形狀 |
| `core/langfuse_client.py` `trace()` | 進入前 `attach(Context())` 脫離既有 OTel context，**強制**成為新 trace 根節點，離開時 `detach` |
| `core/agent.py` `chat()` | `input` 改 `user_message(...)`、`output` 改 `assistant_message(...)`；新增 `_turn_reply_text()` 只取**本輪**最後一則 assistant 訊息 |

**設計要點**：`_turn_reply_text(turn_start)` 只看本輪新增訊息，避免工具迴圈被中斷時把
上一輪的舊回覆誤當成本輪 output（原本 `history[-1]` 在結尾是 tool 訊息時會得到 `None`）。
session 分組沿用 `SessionStore.session_id`（未命名為 `history-YYYYMMDD-HHMMSS`），
因此多個獨立 trace 在 Sessions 頁會串成同一段對話。

**驗證**：`tests/test_langfuse_client.py` 新增 4 個測試（訊息形狀、input 傳遞、只看本輪回覆）；
並以 OTel InMemorySpanExporter 實證外層有 span 時 `chat-turn` 仍為 root（`parent=None`、
trace_id 不同）。

## 已知注意事項

- **Langfuse Cloud v3 read API 已棄用**（2026-11-16，`GET /api/public/traces` 回 410）。**不影響 SDK 寫入**，只影響讀取；讀取請用 v2（`iter_observation_pages`）或 langfuse-cli。
- **重啟生效**：埋點在程式碼（`agent.py` / `backends.py` / `dispatch.py`），改動後需完全重啟 kai，`/reload-config` 不重載程式碼。
- **flush**：app 退出時呼叫 `flush()`，否則最後幾個 span 會掉。
- **隱私**：tool result 經 `_mask_args` 遮罩；system prompt 每 turn 僅首輪記錄全文（截斷 6000 字），後續輪次只放佔位符。要完全排除請設 `LANGFUSE_TRACE_SYSTEM_PROMPT=false`。

## 安裝

```bash
uv add langfuse          # SDK v5（實測 5.2.0）
```
