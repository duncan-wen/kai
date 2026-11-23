# 使用手冊

本文說明 kai TUI 的完整操作方式。

---

## Slash 指令

在提示列輸入 `/` 開頭的指令可執行特殊動作。

### `/clear` — 清除畫面

```
/clear
```

只清除聊天畫面，對話記憶保留。

---

### `/cls` — 封存並清除對話

```
/cls
```

將目前對話封存至 `.history/history_YYYYMMDD_HHMMSS.json`，然後清空聊天畫面並重置歷史。若目前對話只有系統訊息（尚未進行任何對話），或剛透過 `/resume` 還原且未產生新訊息，則不會產生新的封存。

---

### `/resume` — 還原舊對話

```
/resume          # 開啟封存清單
/resume 2        # 直接還原第 2 份封存
```

清單會顯示封存名稱（有命名時）、封存時間、使用者訊息數，以及最後一則使用者訊息的預覽；可在清單中按 `r` 重命名、`d` 刪除。還原後會重新載入完整歷史記錄。

---

### `/model` — 切換模型

```
/model qwen3:8b
/model gemini-2.5-flash
```

熱切換模型，歷史記錄保留。目前支援的模型請參閱「[模型列表](#模型列表)」一節。

---

### `/cd` — 切換工作目錄

```
/cd ../other-project
/cd /home/user/my-repo
```

切換工作目錄，同時儲存目前目錄的歷史、切換後載入新目錄的 `.kai/history.json`，並重新整理檔案樹與 Sidebar。等同於 `!cd` 但更語意清晰。切換後也會自動套用新目錄的 `.kai/config.json`。

---

### `/think` — 切換思考模式

```
/think
```

切換模型的 thinking 模式（僅支援具備思考能力的模型，例如 Qwen3、Gemini 2.5）。啟用後模型會先輸出推理過程再給出答案，適合複雜問題。

---

### `/debug` — 切換工具 debug 卡片

```
/debug
```

開關工具呼叫的 debug 資訊卡片顯示，可即時檢視工具呼叫的輸入與輸出。

---

### `/image` — 傳送圖片

```
/image screenshot.png
/image diagram.jpg 這張圖在說什麼？
```

傳送圖片給視覺模型（需模型支援 vision）。第二個參數為可選提問；省略時僅傳送圖片並附上預設描述請求。

---

### `/ask` — 臨時提問

```
/ask 這段程式碼的時間複雜度是多少？
```

向模型提問，但本次問答**不會儲存到歷史記錄**，適合不想污染對話脈絡的一次性查詢。

---

### `/save` — 保存 `/ask` 回答

```
/save
```

將最後一次 `/ask` 的問答正式寫入歷史記錄，使其成為對話的一部分。

---

### `/remember` — 寫入持久筆記

```
/remember 這個專案的 DB 連線要用 port 5433
```

將筆記附加寫入目前目錄的 `.kai/notes.md`，跨 session 保留。適合記錄專案特有的重要資訊，讓模型在之後的對話中也能參考。

---

## `!` Bash 指令

任何以 `!` 開頭的輸入都會透過 `/bin/bash -c` 執行，支援花括號展開、算術運算 `$(())` 等 Bash 特性。

```bash
!ls -la
!grep -r "TODO" src/
!cat pyproject.toml
!echo $((1024 * 1024))
```

### `!cd` 特殊處理

```bash
!cd ../other-dir
```

由於 subprocess 的 `cd` 不影響父行程，`!cd <路徑>` 會被攔截，直接呼叫 Python 的 `os.chdir()`，確實切換行程工作目錄。執行後檔案樹與 git Sidebar 自動重新整理。

---

## 檔案樹操作

### 多選快捷鍵

| 操作 | 效果 |
|------|------|
| `Ctrl+Click` | 切換該項目的選取狀態（選取的項目顯示綠色 ✓） |
| `Space` | 切換游標項目；若已設定錨點，則從錨點到游標範圍選取 |
| `Ctrl+A` | 全選 / 取消全選所有可見節點 |
| `Escape` | 清除多選（若無選取，則將焦點移回提示列） |

### 右鍵選單

對已選取的項目按右鍵，選單會顯示「刪除選取 (N 項)」選項，可一次批次刪除所有選取的檔案或資料夾。刪除後多選狀態自動清除。

### 檔案系統 Watcher

FileTree 每 2 秒在背景執行緒掃描一次目錄結構（使用 `os.walk`，略過 `.git`、`__pycache__` 等隱藏目錄）。外部工具（例如 VSCode 刪檔、`touch` 指令）造成的異動都會觸發自動重新整理，且掃描不在事件迴圈上，不影響介面渲染。

---

## FileViewer

### 開啟方式

雙擊檔案樹中的任意檔案，即可在中央分頁區開啟 FileViewer，支援語法高亮（透過 tree-sitter）。

### 外部變更偵測

FileViewer 每秒輪詢一次檔案的 `mtime`。若偵測到外部程式修改了檔案（且 FileViewer 本身尚未修改），會自動重新載入並顯示 toast 通知。用 `Ctrl+S` 存檔後，`mtime` 會更新，不會誤觸發重載。

### 複製內容

FileViewer 中選取文字後按 `Ctrl+C`，會透過以下流程複製：
1. 嘗試 `xclip`
2. 若失敗，改用 `clip.exe`（WSL 環境），以 UTF-16-LE + BOM 輸出，確保中文字不會亂碼。

---

## Session 管理

### 封存（`/cls`）

執行 `/cls` 時，目前的 `.kai/history.json` 會被移到 `.kai/.history/` 目錄，檔名格式為 `history_YYYYMMDD_HHMMSS.json`（同一秒重複時加 `_N`）。之後清空聊天並重置歷史。

以下情況不會產生新的封存：
- 歷史中只有系統提示訊息（尚未進行實際對話）
- 剛透過 `/resume` 還原且未產生任何新訊息

### 還原（`/resume`）

```
/resume
```

開啟封存清單（↑↓ 選擇、Enter 載入、`r` 重命名、`d` 刪除）：

```
▶ [ 1]  Rust 重構  2025-05-19 14:23   12 則  幫我分析這段 Rust 程式碼…
  [ 2]  2025-05-18 09:11    5 則  請問 Textual 的 compose 方法…
```

```
/resume 1
```

還原第 1 份封存，將它複製回 `.kai/history.json` 並重新載入對話。還原前會先封存目前的對話（若有新訊息）。

---

## 模型切換

### 切換指令

```
/model <模型名稱>
```

### 思考模式

```
/think
```

適用於支援 thinking 的模型（見下表），可提升複雜推理問題的回答品質。

### 模型列表

| 模型名稱 | Thinking | Vision | Tools | Backend |
|---------|----------|--------|-------|---------|
| `qwen3-vl:4b` | ✓ | ✓ | ✓ | Ollama |
| `qwen3:4b` | ✓ | - | ✓ | Ollama |
| `qwen3:8b` | ✓ | - | ✓ | Ollama |
| `qwen3.5:2b` | - | ✓ | ✓ | Ollama |
| `qwen3.5:4b` | ✓ | ✓ | ✓ | Ollama |
| `gemma3:4b` | - | ✓ | ✓ | Ollama |
| `gemma4:e2b` | ✓ | ✓ | ✓ | Ollama |
| `gemma4:e4b` | ✓ | ✓ | ✓ | Ollama |
| `deepseek-r1:1.5b` | ✓ | - | ✗ | Ollama |
| `MFDoom/deepseek-r1-tool-calling:1.5b` | - | - | ✓ | Ollama |
| `qwen2.5-coder:7b` | - | - | ✗ | Ollama |
| `gemini-2.0-flash` | - | ✓ | ✓ | Gemini |
| `gemini-2.5-flash` | ✓ | ✓ | ✓ | Gemini |
| `gemini-2.5-pro` | ✓ | ✓ | ✓ | Gemini |

---

## MCP 工具整合

MCP（Model Context Protocol）工具透過 `mcp_servers.json` 設定。模型在對話中可直接呼叫已掛載的工具，右側 Sidebar 會列出目前載入的所有工具名稱。

### 設定範例（`mcp_servers.json`）

```json
{
  "servers": [
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/projects"]
    }
  ]
}
```

啟動應用程式時，`core/mcp_client.py` 會自動讀取設定並啟動各 MCP server 行程。

---

## 開發者指引

### 新增模型

在 `core/models.py` 的 `REGISTRY` 字典中加入一筆記錄，填入 `ModelProfile` 所需欄位（`name`、`backend`、`thinking`、`vision`、`tools` 等）。執行期間 `NUM_CTX` 環境變數可覆寫所有模型的 context window 大小。

詳見 [CLAUDE.md](../CLAUDE.md) 的「Adding a new model」章節。

### 新增工具

1. 在 `core/tools/` 下定義 Ollama 工具 schema（仿照 `SEARCH_TOOL`）。
2. 實作 `async def my_tool(...) -> str` 函式。
3. 在 `core/agent.py` 的 `Agent.tools_map` 中登記對應關係。
4. 將 schema 加入 `chat()` 內的 `active_tools` 清單。

詳見 [CLAUDE.md](../CLAUDE.md) 的「Adding a new tool」章節。
