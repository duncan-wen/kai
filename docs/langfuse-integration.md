# Langfuse 可觀測性

kai 可選配串接 [Langfuse](https://langfuse.com)，追蹤每一輪對話的模型呼叫、工具呼叫、
token 用量與成本，並在 Langfuse 的 Sessions 頁面重播整段對話。

**未安裝 SDK 或未設定金鑰時完全不啟用**，不影響一般使用。

## 啟用

```bash
uv sync --extra observability     # 安裝 langfuse SDK
```

在 `.env` 填入金鑰（Langfuse 專案 → Settings → API Keys）：

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com   # US：https://us.cloud.langfuse.com，或自架網址
```

調校參數建議放 `.kai/config.json`：

| 設定 | 預設 | 說明 |
|------|------|------|
| `langfuse_tracing_environment` | `development` | Langfuse 的 environment，用來區隔開發與正式資料 |
| `langfuse_trace_system_prompt` | `true` | 是否記錄 system prompt（每輪只記第一次模型呼叫）；設 `false` 完全不上傳 |

改完需**完全重啟 kai**（`/reload-config` 不會重新初始化 Langfuse client）。

## 會記錄什麼

每一輪對話是一個 trace，結構如下：

```
chat-turn                 (span)        ← 輸入：使用者訊息；輸出：assistant 回覆
├── generate-response     (generation)  ← 每次模型呼叫一個
├── bash_exec             (tool)        ← 每次工具呼叫一個，與觸發它的 generation 同層
├── generate-response     (generation)
└── agent:<名稱>          (agent)       ← send_to_agent 委派給分身時
```

| 項目 | 內容 |
|------|------|
| Session | 每段對話一個持久 ID（`kai-YYYYMMDD-HHMMSS-xxxx`）；`/rename` 不會切開，`/resume` 會接回原本的 session |
| User | `USERNAME` 設定值 |
| `generate-response` | 模型、模型參數（temperature、top_p…）、輸入訊息、輸出（含 tool_calls）、token 用量、首 token 時間；metadata 有 `backend` 與 `thinking` |
| 成本 | 依 Langfuse 模型定價自動計算；DeepSeek 等的 cache 命中會拆成 `input_cached_tokens`，以 cache 價計費 |
| 工具 | 參數（敏感欄位遮罩）與結果；失敗時標 `level=ERROR` |
| `chat-turn` metadata | `model`、`backend`、`agent`、`session_name`、`system_sha`（system prompt 指紋，可追蹤版本變化）、`generations`（本輪模型呼叫次數） |

## 在 Langfuse 怎麼看

- **Sessions**：整段對話依序顯示使用者訊息、工具卡片與 assistant 回覆，失敗的工具旁有紅色標記
- **Tracing**：點進單一 `chat-turn` 看每一步；每個 `generate-response` 的 input 都包含完整對話脈絡
  （Langfuse 靠這點串接 Sessions），看單一步驟的決定請看 output 或樹狀結構裡的 tool 節點
- **篩選**：Level = `ERROR` 找失敗的工具呼叫；metadata `session_name` 找命名過的對話

## 隱私與遮罩

上傳前會經過兩層遮罩，但都是**盡力而為**，無法保證攔下所有敏感資料（見 [SECURITY.md](../SECURITY.md)）：

1. **工具參數**：欄位名含 key / secret / token / password / api 的值改成 `***`
2. **所有上傳內容**（含工具輸出、模型 thinking）：
   - 環境變數中金鑰的原值
   - 常見憑證格式：`sk-…`、`pk-lf-…`、`AIza…`、`ghp_…`、`Bearer …` 等
   - 賦值形式 `XXX_API_KEY=…`、`"api_key": "…"`；認出的值會被記住，之後單獨出現也會遮

system prompt 可能含專案記憶，每輪只在第一次模型呼叫記錄（截斷 6000 字）；不想上傳請設
`langfuse_trace_system_prompt: false`。

## 實作與設計決策（給貢獻者）

| 位置 | 職責 |
|------|------|
| `core/langfuse_client.py` | lazy client、`trace` / `generation` / `tool` / `agent` helper、訊息格式轉換、遮罩 |
| `core/agent.py::chat` | 建立 `chat-turn` trace |
| `core/backends.py` | 兩個串流後端各自建立 `generate-response` |
| `core/tools/dispatch.py` | tool / agent observation 與錯誤標記 |
| `core/session_store.py` | 持久 session ID |

這些做法都是踩過問題後定下的，修改前請先確認不會退化：

- **根節點 I/O 必須是 ChatML 訊息陣列**：`[{"role": "user", "content": …}]`。單一 dict 不會被渲染成對話
- **`chat-turn` 強制成為新的 trace 根**：進入前切斷既有 OpenTelemetry context，否則在分身委派等情境下會被掛成子節點，Sessions 讀不到
- **generation input 要完整重複前面的對話**：Sessions 靠「後一次呼叫的 input 包含前面的訊息」串接；不能只留尾部幾則，
  也要保留 assistant 的 `tool_calls` 與 tool 結果的 `tool_call_id`（Ollama 的 tool call 沒有 id，以內容雜湊出固定 id）
- **同一則訊息在 output 與後續 input 必須截成一樣**：統一 `MESSAGE_MAX_CHARS`（2000 字）
- **同輪第 2 次以後的模型呼叫不放 system 訊息**：放佔位文字會被 Sessions 當成對話中途插入的 system prompt
- **usage 是互斥的 bucket**：供應商回報的 prompt tokens 含 cache 命中，要拆成 `input` + `input_cached_tokens`，否則成本高估
- **session ID 要持久化**：不能用 history 檔的時間戳（Linux 的 `st_ctime` 每次寫檔都變，會讓每輪都變成新 session），
  也不能用 session 名稱（改名會切段、同名會合併）
- **本輪回覆用 user 訊息物件定位**：不能用 history 長度當索引，history 滿了會輪替刪舊訊息，索引會落空
- **generation 以動作命名**（`generate-response`），不含模型或後端名稱，換模型時篩選條件與 evaluator 才不會失效

## 注意事項

- **flush**：kai 結束時會呼叫 `flush()`；異常終止時最後幾筆可能遺失
- **讀取 API**：Langfuse Cloud 的舊版讀取 API（`GET /api/public/traces`）已停用，請改用 v2 observations API 或
  [langfuse-cli](https://langfuse.com/docs/api-and-data-platform/features/cli)；SDK 寫入不受影響
- **刪除 trace**：Langfuse 的刪除是非同步的，刪除後可能仍會顯示一段時間（通常 15 分鐘內，最長 24 小時）
