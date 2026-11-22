# Langfuse 可觀測性串接規劃

本文說明如何將 Langfuse 導入 kai，補上目前缺少的 latency、token 成本、agent 層級 trace 可視化。內容涵蓋現況分析、整合路徑比較、建議架構與分階段實作計畫。

---

## 背景與動機

kai 目前的 LLM 呼叫鏈如下：

```
mixins/streaming.py ─▶ Agent.chat() ─▶ _run_turn() ─▶ core/backends.py
                                              ├─ stream_ollama()        (ollama 套件)
                                              └─ stream_openai_compat() (openai AsyncOpenAI)
                                              └─ run_tool() → core/tools/*
```

現況：

- **兩條自寫串流 backend**，token 用量已存在 `agent._last_usage`（`eval_count` / `prompt_eval_count`）
- **已有除錯機制** `core/tool_log.py`：`log_turn_start()` + `log_tool_call()`（`DEBUG_TOOLS=1` 才寫純文字檔）
- 目前**沒有任何** latency、token 成本、agent 層級 trace 的可視化

Langfuse 能補的洞：

| 需求 | 現況 | Langfuse 提供 |
|------|------|--------------|
| latency 分析 | 無 | 每個 span 的耗時 |
| token 成本 | 僅存在 `_last_usage`，無匯總 | 自動彙整 per-model 成本 |
| agent 層級 trace | 僅 `tool_log` 純文字檔 | trace → span 樹狀可視化 |
| 品質評估 | 無 | dataset / scoring（選配） |

---

## Langfuse 現況（2026-11）

- 最新 **Python SDK v5**（基於 OpenTelemetry）
- **需要 Langfuse v4 server**，或使用 Langfuse Cloud（含免費 tier）
- 非同步背景上傳、幾乎零延遲；SDK 錯誤會被 catch，不會弄掛 TUI

三種整合路徑：

| 路徑 | 適合度 | 說明 |
|---|---|---|
| **A. 手動 SDK 埋點**（`@observe` / trace span） | ✅ 推薦 | 兩條 backend 皆自寫串流，ollama 那條 OpenAI 自動整合蓋不到 |
| B. OpenTelemetry | 備選 | 若想跨專案統一 telemetry 才值得 |
| C. OpenAI SDK 自動整合 | ❌ 只蓋一半 | 僅對 `stream_openai_compat` 有效，ollama 沒用 |

---

## 建議架構

新增一個 **lazy singleton** `core/langfuse_client.py`，核心原則「未設憑證 = no-op」，不影響沒使用 langfuse 的使用者：

```python
# 憑證來源：LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST
# 未設定 → 回傳 None，全專案自動跳過追蹤
```

埋點掛在**既有的天然掛鉤點**（與 `tool_log.py` 並排，互不衝突）：

| 位置 | 層級 | 記錄內容 |
|------|------|----------|
| `core/agent.py::_run_turn` | trace | session id（用 `SessionStore` 名字）、model、cwd、agent dir |
| `core/backends.py::stream_ollama` / `stream_openai_compat` | generation span | model、input/output、token（取自 `_last_usage`）、latency |
| `core/tools/dispatch.py::run_tool` | tool span | tool name、args、result |
| `core/agent.py::compact_history` | generation | 摘要該次的用量 |

---

## 分階段實作計畫

### 階段 0：安裝與設定

- `pip install langfuse`（專案用 uv 管理，請用 `uv add langfuse`）
- 將 `LANGFUSE_*` 加進 `core/config.py`（讀取環境變數）

### 階段 1：lazy client

撰寫 `core/langfuse_client.py`：

- lazy init + no-op fallback（未設憑證時回傳 None）
- 提供 `flush()` 供 app 關閉時呼叫

### 階段 2：埋點

- 在 `_run_turn` 包 trace
- 接 generation + tool spans，填入 token / 成本欄位
- 與現有 `tool_log.py` 的 `log_turn_start` / `log_tool_call` 並排呼叫

### 階段 3：收尾

- app 關閉時 `await langfuse.flush()`，避免 TUI 退出掉資料

### 階段 4（選配）：評估

- 接 evaluation（dataset / scoring），觀察 agent 回覆品質

---

## 風險與注意事項

- **隱私**：tool result 可能含原始碼 / 檔案內容，建議沿用 `DEBUG_TOOLS` 的「預設關閉」開關，或對 result 做遮罩
- **版本對齊**：自架 langfuse 需 v4 server 才能用 SDK v5；否則降用 SDK v4
- **串流不卡 UI**：langfuse 為背景上傳，但務必於退出時 flush，否則最後幾個 span 會掉
- **工作量**：階段 0–3 約 **1 個檔案新增 + 4 個檔案各加幾行**，改動集中於 `core/agent.py`、`core/backends.py`、`core/tools/dispatch.py`

---

## 附錄：設定範例

```bash
# .env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://localhost:3000   # self-host；Cloud 可省略
```
