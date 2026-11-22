# Issue: tool call debug display 用字串關鍵字比對判斷錯誤，存在誤判缺陷

> 狀態：✅ 已修復（2026-11-23，分支 `fix/tool-result-structured`）　|　優先序：中　|　建立：2026-11-23

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

## ✅ 修復紀錄（2026-11-23）

改為**結構化旗標**回報，完全移除顯示層的字串關鍵字掃描。

### 新增
- `core/tools/result.py`：`ToolResult(fn_name, output, is_error, exit_code)` dataclass，
  `is_warn` 為 property（`not is_error and exit_code == 1`），`__str__` 回傳 output 保持字串相容。

### 改動
| 檔案 | 改動 |
|---|---|
| `core/tools/bash.py` | `bash_exec` 回傳 `ToolResult`，帶 `exit_code`；`>= 2` → `is_error`，`== 1` → `is_warn` |
| `core/tools/dispatch.py` | `_dispatch` / `run_tool` 回傳 `ToolResult`；工具自行回傳 `ToolResult` 則透傳；**所有工具例外統一包成 `is_error=True`**（修掉原本漏判） |
| `core/agent.py` | 呼叫點改吃 `ToolResult`，`on_tool_call(name, args, tr)` 傳物件；tool message 用 `tr.output` |
| `mixins/streaming.py` | 移除 `_ERR_PREFIXES` 與 exit code regex，改讀 `tr.is_error` / `tr.is_warn` |
| `mixins/agents.py` | 移除 `_ERR` 字串掃描，改讀 `tr.is_error` / `tr.is_warn`，**補上 ⚠️ 警告級**（兩路徑語意統一） |
| `core/tools/__init__.py` | 匯出 `ToolResult` |

### 驗證
- `tests/test_tool_result.py` 新增 15 個測試，含三個關鍵回歸：
  1. 成功輸出含 `Traceback (most recent call last)` → **不再**判為錯誤
  2. 成功輸出含 `[Error]` / `[stderr]` / `[MCP Error]` 字樣 → **不再**判為錯誤
  3. `exit 3`（無任何關鍵字）→ 正確判為錯誤
- 全套 `uv run pytest`：131 passed；`ruff check`：All checks passed

## 備註

- 不影響功能正確性，屬「顯示誤判」層級，優先序中。
- 與 skill 支援（階段 3）無關，建議獨立分支處理，勿混在同一個 PR。
