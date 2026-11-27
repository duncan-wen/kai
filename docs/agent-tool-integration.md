# Agent 工具整合模式

本文說明如何讓 kai agent 呼叫外部工具，並比較三種設計模式的取捨。

---

## 背景

kai agent 預設擁有以下內建工具：`web_search`、`bash_exec`、`fetch_urls`，以及透過 `mcp_servers.json` 掛載的 MCP 工具。若 agent 需要操作特定外部服務（例如 Ghost CMS、資料庫、第三方 API），有三種方式可以接入。

---

## 模式一：bash_exec 呼叫 CLI 腳本

AI 透過內建的 `bash_exec` 工具執行一支獨立的 Python（或任何語言）腳本。

```
AI
└─▶ bash_exec("python3 /path/to/tool.py create_draft --title '...' --html '...'")
      └─▶ 腳本直接呼叫外部 API
```

### 實作方式

1. 撰寫獨立 CLI 腳本（`argparse` 定義子指令）
2. 在 `agents/<name>/config.json` 的 `system_prompt` 中告知 AI 腳本路徑與可用指令
3. AI 自行組合 shell 指令呼叫

### HTML / 大量文字的引號問題

當參數包含 HTML 或多行文字時，直接用 `--html "..."` 容易因引號跳脫出錯。  
標準做法是先寫暫存檔再讀取：

```bash
cat > /tmp/article.html << 'HTMLEOF'
<p>文章內容，不用擔心引號</p>
HTMLEOF
python3 /path/to/tool.py create_draft --title "標題" --slug "slug" --tags "tech" --html "$(cat /tmp/article.html)"
```

### 適用情境

- 工具只有單一 agent 使用
- 快速迭代、不想動 kai core
- 腳本需要獨立版本控管或複用於其他系統

### 優缺點

| 優點 | 缺點 |
|------|------|
| 與 kai 完全解耦 | AI 要自己組 shell 指令，容易有引號問題 |
| 腳本可獨立測試 | 無法並行執行多個工具呼叫 |
| 不需修改 kai core | AI 看不到結構化 schema，靠 system prompt 說明 |
| 任何語言都能寫 | stderr/stdout 混合，錯誤訊息格式不一 |

---

## 模式二：專用 tool（寫進 kai core）

在 `core/tools/` 定義工具 schema 與實作，在 `core/agent.py` 登記，讓所有 agent 都能以結構化方式呼叫。

```
AI
└─▶ create_ghost_draft(title=..., html=..., tags=[...])   ← 結構化 schema
      └─▶ core/tools/ 的 async 函式直接呼叫外部 API
```

### 實作方式

1. 在 `core/tools/` 定義 Ollama tool schema：

```python
CREATE_GHOST_DRAFT_TOOL = {
    "type": "function",
    "function": {
        "name": "create_ghost_draft",
        "description": "建立 Ghost CMS 草稿文章，回傳 post_id 與 URL",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "文章標題"},
                "html": {"type": "string", "description": "文章 HTML 內容"},
                "slug": {"type": "string", "description": "URL slug（英文連字號）"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "tag slug 清單",
                },
                "excerpt": {"type": "string", "description": "文章摘要（可選）"},
            },
            "required": ["title", "html", "slug", "tags"],
        },
    },
}


async def create_ghost_draft(
    title: str, html: str, slug: str, tags: list[str], excerpt: str = ""
) -> str:
    # 直接呼叫 Ghost Admin API
    ...
```

2. 在 `core/agent.py` 的 `tools_map` 登記：

```python
self.tools_map = {
    ...
    "create_ghost_draft": create_ghost_draft,
}
```

3. 將 schema 加入 `chat()` 內的 `active_tools` 清單。

### 適用情境

- 工具要給多個 agent 使用
- 工具有複雜輸入結構，需要明確 schema 約束 AI 行為
- 需要在 `on_tool_call` callback 做即時 UI 回饋

### 優缺點

| 優點 | 缺點 |
|------|------|
| AI 看到結構化 schema，呼叫更精準 | 需修改 kai core（core/tools/ + agent.py） |
| 無 shell 引號問題 | 工具與 kai 耦合，換系統要重寫 |
| 支援 `asyncio.gather` 並行呼叫 | 所有 agent 都會看到此工具（需條件載入才能限制） |
| tool debug 卡片顯示乾淨的 JSON args | 需維護 schema 與實作同步 |

---

## 模式三：MCP server

將工具包裝成符合 MCP（Model Context Protocol）規範的獨立 server，透過 `mcp_servers.json` 掛載（分身共用主 agent 的 MCP client）。

```
AI
└─▶ MCP call → ghost_mcp.py（長駐 process）
                  └─▶ Ghost Admin API
```

> **注意**：kai 目前的 `_do_load_agent` 尚未實作讀取 agent 層 `mcp_servers` 欄位的功能；  
> 現階段 agent-specific MCP servers 需手動加入 `mcp_servers.json` 並以 `_` 前綴管理。

### 實作方式

使用 `mcp` Python 套件的 `FastMCP`：

```python
# ghost_mcp.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ghost")


@mcp.tool()
async def create_draft(title: str, html: str, slug: str, tags: list[str]) -> str:
    """建立 Ghost 草稿，回傳 post_id 與 URL。"""
    ...


if __name__ == "__main__":
    mcp.run()
```

在 `mcp_servers.json` 掛載（啟用時移除 `_` 前綴）：

```json
{
  "mcpServers": {
    "_ghost": {
      "command": "uv",
      "args": ["run", "python", "/path/to/ghost_mcp.py"],
      "env": { "GHOST_ADMIN_API_KEY": "..." }
    }
  }
}
```

### 適用情境

- 工具複雜、有多個子操作需要清晰 schema
- 同一工具要給多個 agent 或多個系統（如 Claude Desktop）使用
- 需要嚴格隔離：工具 crash 不影響 kai 本體

### 優缺點

| 優點 | 缺點 |
|------|------|
| 工具與 kai 解耦，可跨系統複用 | 需維護額外長駐 process |
| AI 看到結構化 schema | 啟動延遲、需處理 process lifecycle |
| 支援 kai 的 debug 卡片與並行呼叫 | MCP 協定有學習成本 |
| 環境變數在 server 層管理，不外洩 | 目前 agent-level 掛載尚未完整支援 |

---

## 決策矩陣

| 條件 | 建議模式 |
|------|----------|
| 工具只有一個 agent 用、快速驗證 | **bash_exec** |
| 工具邏輯簡單（CRUD）、有 shell 跳脫問題 | **bash_exec + heredoc** |
| 多個 agent 共用、要進 kai 主線 | **專用 tool** |
| 跨系統複用（kai + Claude Desktop + 其他）| **MCP server** |
| 工具很複雜、需要強型別 schema | **MCP server 或專用 tool** |
| 先快速驗證、之後再升級 | **bash_exec → MCP server** |

---

## 實例：幽靈寫手（Ghost CMS 文章代筆 agent）

採用 **bash_exec 模式**。

```
agents/幽靈寫手/
  config.json     ← system prompt 說明 ghost_api.py 的 CLI 介面
  ghost_api.py    ← Ghost Admin API 封裝（argparse CLI）
  history.json
```

AI 呼叫範例：

```bash
# 寫稿完成後，用 heredoc 避免 HTML 引號問題
cat > /tmp/article.html << 'HTMLEOF'
<h2>前言</h2><p>...</p>
HTMLEOF

python3 /path/to/ghost_api.py create_draft \
  --title "企業導入 RAG 的五個關鍵決策" \
  --slug "enterprise-rag-five-decisions-2025" \
  --tags "tech,rag" \
  --excerpt "90% 的企業 RAG 導入卡在資料治理，而非技術本身。" \
  --html "$(cat /tmp/article.html)"
```

`ghost_api.py` 支援的子指令：`list_drafts`、`list_posts`、`get_post`、`create_draft`、`publish`、`add_tags`、`delete_post`。樂觀鎖（`updated_at`）由腳本內部處理，AI 不需感知。

選擇 bash_exec 而非 MCP 的原因：工具目前只有一個 agent 使用，CLI 腳本可獨立於 kai 之外測試與部署，Ghost 操作也不需要並行執行。若未來需要第二個 agent 操作 Ghost，再升級為 MCP server。
