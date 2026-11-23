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
- `_mask_args()`：依欄位名遮罩工具參數中的 key/secret/token/api
- `mask_secrets()`：Langfuse client 的 `mask` hook，所有上傳資料（含工具輸出）依「值」遮罩：環境變數中的密鑰原值、常見憑證格式（`sk-`、`pk-lf-`、`AIza`、`ghp_`、Bearer…）、`*_API_KEY=` / `"api_key":` 賦值

### 2. 埋點位置

| 位置 | 層級 | 名稱 / 記錄內容 |
|------|------|----------------|
| `core/agent.py::chat` | trace span | `chat-turn`：session_id、model、input（user 文字）、output（reply） |
| `core/backends.py::stream_ollama` | generation | `generate-response`：model、model_parameters、input / output（ChatML 訊息陣列，含 tool_calls）、metadata（backend、thinking）、usage、completion_start_time |
| `core/backends.py::stream_openai_compat` | generation | `generate-response`：同上，usage 另拆出 `input_cached_tokens` |
| `core/tools/dispatch.py::run_tool` | tool | tool name、args（遮罩後）、result |
| `core/tools/dispatch.py`（subagent dispatch） | **agent** | `send_to_agent` / `load_agent` 等 → type 為 `agent`，Agent Graph 顯示子 agent 節點 |
| `core/agent.py::compact_history` | generation | 摘要該次用量 |
| `app.py`（退出） | flush | 避免 TUI 退出掉資料 |

### 3. session 分組 — `core/session_store.py`

`SessionStore.session_id` property（session 概念的家）：

- session 第一次取用時產生 `kai-YYYYMMDD-HHMMSS-xxxx`，存進 `.history/.session_id`，整個 session 不變
- `/cls` 封存時把 ID 記到 `.history/.session_ids.json`（archive 檔名 → ID）；`/resume` 時還原，叫回的對話接續原本的 Langfuse session
- session 名稱**不**當 ID（中途改名會切段、同名會合併），改放 trace metadata `session_name`
- 只在 Langfuse 啟用時才取用（不啟用就不會建立 `.session_id`）

> 歷史教訓：曾用 history 檔的 `st_ctime` 當未命名 session 的 ID。但 Linux 的 ctime 是「最後變更時間」，
> 每次 `save_history` 都會變 → 幾乎每一輪都是不同 session（實測 37 筆 trace 分散在 33 個 session）。

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
| 描述性 trace name | ✅ `chat-turn` / `generate-response`（動作命名，不含模型或後端） |
| Span hierarchy（單一 parent 嵌套） | ✅ trace → generation → tool/agent |
| Correct observation types | ✅ generation / tool / **agent** 四型態正確 |
| Sensitive data masked | ✅ `_mask_args` 遮罩 tool 參數、`mask_secrets` 遮罩所有上傳值；system prompt 每 turn 僅首輪記錄（可用 `LANGFUSE_TRACE_SYSTEM_PROMPT=false` 完全排除） |
| Capture thinking | ✅ generation metadata 含 thinking |
| session_id 分組 | ✅ 每個 session 一個持久 ID，`/rename` 不影響、`/resume` 接續 |

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
| 本 turn **第 1 次** `generation` | 完整 system prompt（截斷 6000 字）+ 本次送給模型的全部非 system 訊息（每則截斷 2000 字） |
| 本 turn **第 2～N 次** `generation` | 不放 system，只放非 system 訊息（佔位符會被 Session 視圖當成中途插入的 system prompt） |

`system_sha` 讓你能在 dashboard 上直接看出 system prompt 有沒有變（專案記憶、skills 索引
會隨時間變動），而不必比對全文。實作：`core/langfuse_client.py` 的 `trim_messages` /
`system_fingerprint`，搭配 `core/backends.py` 的 `_generation_messages`（per-turn 計數
`agent._lf_gen_idx`，於 `chat()` 開頭重置）。

## Sessions（Messages 視圖）修復 — 2026-11-23

**症狀**：Sessions 頁面看不到任何對話，trace 樹長成「1 個 root 底下掛 1.1～1.6 六個
chat-turn」。

**兩個根因**：

1. **訊息形狀不合**：Langfuse Sessions 只讀 trace **根節點**的 `input` / `output`，且必須是
   **`{"role", "content"}` 的陣列**（`[{"role": "user", "content": ...}]`）才渲染成對話泡泡。
   原本寫的是 `{"user": ...}` / `{"reply": ...}`，所以有資料但渲染不出來。
2. **巢狀而非獨立 trace**：`chat-turn` 若在已有外層 span 的 context 中被建立，會變成子節點，
   而 Sessions 只看根節點 → 讀不到。

**修法**：

| 位置 | 改動 |
|------|------|
| `core/langfuse_client.py` | 新增 `user_message()` / `assistant_message()` 產生標準訊息**陣列** |
| `core/langfuse_client.py` `trace()` | 進入前 `attach(Context())` 脫離既有 OTel context，**強制**成為新 trace 根節點，離開時 `detach` |
| `core/agent.py` `chat()` | `input` 改 `user_message(...)`、`output` 改 `assistant_message(...)`；新增 `_turn_reply_text()` 只取**本輪**最後一則 assistant 訊息 |

**設計要點**：`_turn_reply_text(turn_start)` 只看本輪新增訊息，避免工具迴圈被中斷時把
上一輪的舊回覆誤當成本輪 output（原本 `history[-1]` 在結尾是 tool 訊息時會得到 `None`）。
session 分組沿用 `SessionStore.session_id`（2026-11-23 後改為持久 ID，見上方「session id」），
因此多個獨立 trace 在 Sessions 頁會串成同一段對話。

**驗證**：`tests/test_langfuse_client.py` 新增 4 個測試（訊息形狀、input 傳遞、只看本輪回覆）；
並以 OTel InMemorySpanExporter 實證外層有 span 時 `chat-turn` 仍為 root（`parent=None`、
trace_id 不同）。

### 補充：ChatML 解析器只吃陣列（同日修正）

首版 `user_message()` / `assistant_message()` 回傳單一 dict（`{"role": "user", "content": "好"}`），
Sessions 頁面仍空白 —— 因為 Langfuse 的 ChatML 解析器偵測的是**訊息陣列**，單一 dict 會被視為
一般 JSON payload 而不做對話渲染。

改成回傳 `list[dict]` 後才正確：

```python
user_message("好")      # -> [{"role": "user", "content": "好"}]
assistant_message("嗨")  # -> [{"role": "assistant", "content": "嗨"}]
```

呼叫端（`agent.py` 的 `input=` / `output=`）不需改動，helpers 已封裝形狀。

### 補充：含 tool call 的 turn 顯示「Thread 1/2/3 — No messages」（同日修正）

**症狀**：沒用工具的對話正常；用了工具的 turn 在 Sessions 頁被拆成多個 thread，每個都是
「No messages」。

**根因**：Session 視圖是用各 `generation` 的 input / output 重建對話 —— 後一個 generation 的
input 要**完整重複**前面的訊息，才會被串成同一段（官方 best practice「Repeat the conversation
history」「Match tool calls to results」）。原本的 generation I/O 三處不符：

1. input 只留**尾部 6 則**：tool loop 越跑越長，開頭（含本輪 user 訊息）被擠掉 → 每輪都像新對話。
2. input 丟掉 assistant 的 `tool_calls`（content 為空被略過），tool 結果也沒有 `tool_call_id`。
3. output 是 `{"content": "", "thinking": ...}` 物件而非 ChatML 陣列；呼叫工具那幾輪 content 為空 → No messages。

**修法**：

| 位置 | 改動 |
|------|------|
| `trim_messages()` | 預設不砍尾部（messages 已由 history_keep 限縮）；保留 assistant `tool_calls` 與 tool `tool_call_id`；每則截斷 `MESSAGE_MAX_CHARS`=2000 |
| `tool_call_dict()` | 各後端 tool call 正規化成 OpenAI 形狀；Ollama 無 id → 以「位置 + 名稱 + 參數」雜湊出決定性 id，tool 結果依序配對 |
| `generation_output()` | output 改為 `[{"role": "assistant", "content", "tool_calls"}]`，與下一輪 input 裡的同一則訊息完全一致 |
| `core/backends.py` | input 直接傳訊息陣列（不再包 `{"messages": ...}`）；thinking 移到 generation metadata |

## 稽核修正 — 2026-11-23（`fix/langfuse-audit`）

對照官方「What does a good trace look like?」與 Token & Cost Tracking 文件稽核後的修正：

| # | 問題 | 修法 |
|---|------|------|
| 1 | **成本高估**：DeepSeek 的 `prompt_cache_hit_tokens` 只寫 log，usage 全以一般輸入價計 | `_usage_details(usage, cache_hit)` 拆成互斥 bucket：`input`（未命中）+ `input_cached_tokens`（命中）；每次呼叫重置 `_last_cache_hit`；也支援 OpenAI / Gemini 的 `prompt_tokens_details.cached_tokens` |
| 2 | **工具輸出未遮罩**：`cat .env` 之類的輸出會原文進 tool span 與下一輪 generation input | `Langfuse(mask=mask_secrets)`，對所有上傳值做值層級遮罩 |
| 3 | **工具失敗未標記** | `_record_result()`：`ToolResult.is_error` → `level="ERROR"` + `status_message` |
| 4 | **generation 以後端命名**（`llm-ollama` / `llm-openai-compat`），換模型就斷篩選 | 統一 `generate-response`，後端記在 `metadata.backend` |
| 5 | 缺屬性 | `completion_start_time`（首個實質輸出時間）、`model_parameters`（temperature / top_p / top_k / num_ctx / thinking）、`environment`（`LANGFUSE_TRACING_ENVIRONMENT`，預設 `development`）、`user_id`（`USERNAME`，非 ASCII 轉 sha1） |

**端到端驗證**（送出真實 trace 後以 v2 observations API 取回）：

- `DEMO_API_KEY=supersecretvalue99` 在 tool output 顯示為 `DEMO_API_KEY=***`，整份 payload 搜不到原值
- usage 例：`input 141 / input_cached_tokens 3456 / output 48`，cache 以 `deepseek-flash` 定價表的 `input_cached_tokens`（$0.006/M）計價
- `exit 3` 的 tool span 為 `level=ERROR`、`statusMessage="[exit code 3]…"`
- `environment=development`、`userId` 已帶上，`completionStartTime` 有值
  （v2 API 的 `timeToFirstToken` 衍生欄位仍為 null，原始時間要用 `fields=time` 取）

> ⚠️ generation 名稱變更：既有以 `llm-ollama` / `llm-openai-compat` 篩選的 view / evaluator 要改成 `generate-response`。

## 已知注意事項

- **Langfuse Cloud v3 read API 已棄用**（2026-11-16，`GET /api/public/traces` 回 410）。**不影響 SDK 寫入**，只影響讀取；讀取請用 v2（`iter_observation_pages`）或 langfuse-cli。
- **重啟生效**：埋點在程式碼（`agent.py` / `backends.py` / `dispatch.py`），改動後需完全重啟 kai，`/reload-config` 不重載程式碼。
- **flush**：app 退出時呼叫 `flush()`，否則最後幾個 span 會掉。
- **隱私**：tool 參數經 `_mask_args`、所有上傳值經 `mask_secrets` 遮罩；system prompt 每 turn 僅首輪記錄全文（截斷 6000 字），後續輪次不放 system。要完全排除請設 `LANGFUSE_TRACE_SYSTEM_PROMPT=false`。

## 安裝

```bash
uv add langfuse          # SDK v5（實測 5.2.0）
```
