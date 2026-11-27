# Agentic Loop 與 Code Agent

## 什麼是 Agentic Loop

Agentic Loop 是讓 LLM 能夠「自主執行多步驟任務」的核心程式邏輯。
普通 LLM 只是輸入 → 輸出一次，而 agent 會不斷循環，直到任務完成。

```
使用者輸入
    ↓
  LLM 推理
    ↓
需要工具？
  ├─ 是 → 執行工具 → 把結果塞回 history → 再讓 LLM 推理
  └─ 否 → 輸出最終回答，結束
```

這個「推理 → 工具 → 推理」的循環就叫 **agentic loop**。

---

## 本專案的實作對照

### 1. 入口：`agent.py: Agent.chat()`

```python
while True:
    # 1. 呼叫 LLM（streaming）
    # 2. 收集 tool_calls
    if not tool_calls:
        break  # 沒有工具要叫 → 結束
    # 3. asyncio.gather 並行執行所有工具
    # 4. 把結果 append 進 messages
    # 5. 繼續下一輪
```

### 2. 工具分發：`core/tools/dispatch.py: run_tool()`

優先序：MCP tools → 本地 tools_map

```python
if mcp_client and fn_name in mcp_client.tool_names:
    return await mcp_client.call(fn_name, raw)
return await tools_map[fn_name](**raw)
```

### 3. 記憶：`self.history`

LLM 本身無狀態，記憶靠每次把完整 `history`（含工具結果）傳入。
`_trim_history()` 控制傳入長度，避免超出 context window。

### 4. 工具結果傳回格式（Ollama）

```python
# assistant 宣告要呼叫工具
{"role": "assistant", "tool_calls": [...]}

# 工具執行完，把結果放進 messages
{"role": "tool", "name": "web_search", "content": "結果字串"}
```

---

## 核心概念整理

### ReAct 模式（Reason + Act）

目前最主流的 agent 設計模式：
1. **Reason**：LLM 先「思考」要做什麼（`<think>` 或直接在 content）
2. **Act**：呼叫工具執行動作
3. **Observe**：把工具結果觀察後，進入下一輪推理

本專案的 `supports_thinking` 對應 Reason 步驟（qwen3 的 `<think>` block）。

### Tool Schema

LLM 要能呼叫工具，必須在 prompt 裡告訴它「有哪些工具、參數格式是什麼」。
Ollama 使用 OpenAI 相容格式：

```python
{
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "描述越清楚，LLM 越知道什麼時候該呼叫",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}
```

### 平行工具執行

當 LLM 在一次回應中要求呼叫多個工具時，可以平行執行：

```python
results = await asyncio.gather(*[run_tool(tc, ...) for tc in tool_calls])
```

本專案已實作，這是 agentic loop 效能的關鍵優化。

### 工具結果的 token 控制

工具結果會放回 context，過長會撐爆 context window。
本專案用 `budget_results()` 限制搜尋結果總字元數（`MAX_SEARCH_CHARS`）。

---

## 與市面框架的對比

| | 本專案 | LangChain | AutoGen | CrewAI |
|---|---|---|---|---|
| 底層呼叫 | 直接 Ollama API | 抽象 LLM wrapper | 抽象 LLM wrapper | 多 agent 協作 |
| 透明度 | 高（自己寫迴圈） | 低（框架包裝） | 中 | 低 |
| MCP 支援 | 自行橋接 | 插件 | 插件 | 插件 |
| 適合情境 | 學習底層 / 客製化 | 快速原型 | 多 agent 對話 | 角色分工任務 |

---

## 未來可以深入的方向

### 短期（可直接在本專案實驗）

- [ ] **Structured Output**：用 Pydantic schema 約束 LLM 輸出格式（Ollama `format=`），讓工具參數更可靠
- [ ] **Tool call 重試**：工具失敗時自動告知 LLM 並重試，而非直接中斷
- [ ] **Memory 分層**：短期記憶（history）+ 長期記憶（向量資料庫檢索），目前只有短期
- [ ] **Streaming tool result**：工具執行中即時回傳進度，而非等全部完成

### 中期（需要較大改動）

- [ ] **Multi-agent**：多個 agent 協作，例如一個負責規劃、一個負責執行
- [ ] **Planning agent**：先讓 LLM 產生執行計畫（task list），再逐步執行，而非每步都讓 LLM 自由決策
- [ ] **RAG 整合**：把文件向量化，工具呼叫時檢索相關段落放進 context
- [ ] **評估框架**：記錄每次 tool call 的決策，分析 LLM 的工具選用準確率

### 長期（架構層級）

- [ ] **Human-in-the-loop**：高風險操作（刪除檔案、送出 API 請求）前暫停等待使用者確認
- [ ] **Agent 持久化**：任務可以中斷後繼續，而非每次對話都從頭開始
- [ ] **多模型路由**：根據任務類型自動選模型（思考型 vs 速度型 vs 視覺型）
