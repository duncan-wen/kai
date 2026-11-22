# Issue: tool call debug display 用字串關鍵字比對判斷錯誤，存在誤判缺陷

> 狀態：待處理　|　優先序：中　|　建立：2026-11-23

## 問題描述

工具呼叫結果（tool call）在 UI 上的「紅色 ❌ / 正常 🔧」判定，目前是**用字串關鍵字比對**猜測是否為錯誤，而非結構化錯誤標記。只要回傳文字裡任何位置出現特定關鍵字，就被塗紅。

## 兩條路徑的實作（且兩者不一致）

| 路徑 | 位置 | 判斷邏輯 |
|---|---|---|
| 主對話（streaming） | `mixins/streaming.py:173-180` | `_ERR_PREFIXES = ("[MCP Error]", "[Error]", "[ValidationError]", "[stderr]", "Traceback (most recent call last)")`，**加上** exit code 判斷：`is_err = _exit_code >= 2 or any(kw in result ...)`；另有 `is_warn = not is_err and _exit_code == 1`（⚠️） |
| agent 間通訊 | `mixins/agents.py:220-221` | `_ERR = ("[MCP Error]", "[Error]", "[ValidationError]", "[stderr]", "Traceback")`，`is_err = any(kw in result for kw in _ERR)`，**無 exit code 判斷** |

→ 兩條路徑邏輯不一致：streaming 路徑有 exit code + 警告級別，agents 路徑只有純字串比對。

## 缺陷

1. **False positive（誤塗紅）**：工具**成功**、但回傳的程式碼／文件片段裡恰含 `Traceback`、`[Error]`、`[stderr]` 字樣 → 被誤判為錯誤。codegraph、context7 這類「回傳程式片段」的工具最容易中招。
2. **False negative（漏判）**：工具**失敗**、但錯誤訊息不含上述關鍵字 → 不塗紅。
3. **不一致**：同一個語意（工具錯誤），兩條路徑給出不同判定結果（agents 路徑無 exit code 判斷）。

## 建議方案

工具分發層回傳結構化結果 `(result, is_error)`（或 `ToolResult` dataclass），`on_tool_call` 直接吃 flag，取代字串掃描。需改動：

- `core/agent.py` 工具分發（`run_tool` 路徑，約 line 616-618 呼叫 `on_tool_call` 處）
- `mixins/streaming.py:173` 的 `_ERR_PREFIXES` 判斷
- `mixins/agents.py:220` 的 `_ERR` 判斷
- 所有工具 schema 需能回傳錯誤標記（或統一在分發層 wrap）

## 影響檔案

- `mixins/streaming.py`（`on_tool_call`，line 165 起；`_ERR_PREFIXES` line 173）
- `mixins/agents.py`（`on_tool_call`，line 212 起；`_ERR` line 220）
- `core/agent.py`（`on_tool_call` 呼叫點，line 616-618）

## 備註

- 不影響功能正確性，屬「顯示誤判」層級，優先序中。
- 與 skill 支援（階段 3）無關，建議獨立分支處理，勿混在同一個 PR。
