# MCP 整合指南

kai 透過 [Model Context Protocol (MCP)](https://modelcontextprotocol.io) 讓 AI 模型呼叫外部工具，例如資料庫查詢、瀏覽器操作、檔案系統存取等。

## 設定方式

在專案根目錄建立（或編輯）`mcp_servers.json`，格式與 Claude Desktop 完全相容：

```json
{
  "mcpServers": {
    "server-name": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/your/path"]
    }
  }
}
```

重啟 kai 後，sidebar 的「MCP 工具」欄位會顯示已連線的 server 與工具數量。

> 以 `_` 開頭的 server 名稱（如 `_playwright`）會被略過，可用來暫時停用而不刪除設定。

---

## Transport 類型

### stdio（預設）

啟動本地子行程，透過 stdin/stdout 通訊。適合本地工具。

```json
{
  "mcpServers": {
    "my-server": {
      "command": "python",
      "args": ["my_server.py"],
      "env": {
        "MY_ENV_VAR": "value"
      }
    }
  }
}
```

### HTTP（Streamable HTTP）

連線到遠端 MCP server，支援自訂 header（可讀取環境變數）。

```json
{
  "mcpServers": {
    "remote-server": {
      "type": "http",
      "url": "https://api.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${env:MY_API_KEY}"
      }
    }
  }
}
```

`${env:VAR_NAME}` 語法會在啟動時從環境變數展開。

---

## 常用現成 Server

以下 server 可直接加進 `mcp_servers.json` 使用，無需另外安裝（`npx` 會自動下載）：

| Server | 功能 | 範例 args |
|--------|------|-----------|
| `@modelcontextprotocol/server-filesystem` | 讀寫本地檔案系統 | `["-y", "...", "/your/path"]` |
| `@modelcontextprotocol/server-postgres` | PostgreSQL 查詢 | `["-y", "...", "postgresql://user:pass@host/db"]` |
| `@modelcontextprotocol/server-sqlite` | SQLite 查詢 | `["-y", "...", "/path/to/db.sqlite"]` |
| `@modelcontextprotocol/server-git` | Git 操作（log、diff、commit） | `["-y", "...", "--repository", "/repo/path"]` |
| `@modelcontextprotocol/server-github` | GitHub API（PR、issue、search） | 需設定 `GITHUB_TOKEN` env |
| `@playwright/mcp` | 瀏覽器自動化 | `["-y", "@playwright/mcp@latest", "--browser", "chromium"]` |

完整清單：[github.com/modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers)

---

## 本專案的設定範例

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/projects"]
    },
    "RepoMapper": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/repomap_server.py"]
    },
    "context7": {
      "type": "http",
      "url": "https://mcp.context7.com/mcp",
      "headers": {
        "CONTEXT7_API_KEY": "${env:CONTEXT7_API_KEY}"
      }
    },
    "_postgres": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://user:pass@localhost/mydb"]
    }
  }
}
```

---

## MCP vs bash_exec：如何取捨

kai 內建 `bash_exec` 工具，可直接執行任意 shell 指令。因此「要不要用 MCP」本質上是一個工程取捨，而非非此即彼。

### 決策樹

```
要加一個新工具？
│
├─ 系統上已有 CLI？
│   └─ 直接 bash_exec → git / grep / jq / ffmpeg / uv
│
├─ 需要跨呼叫保持狀態？
│   └─ MCP → playwright（瀏覽器 session）、DB 連線池
│
├─ 回傳結構化資料，模型需要解讀內容？
│   └─ MCP → RepoMapper（程式碼地圖）、postgres（查詢結果）
│
├─ 第三方 HTTP 服務？
│   └─ MCP http → context7、遠端 API
│
└─ 一次性輸出，純文字就夠？
    └─ bash_exec 或內建工具（web_search、fetch_urls）
```

### 核心原則

> **token 稅值不值得，看「模型需不需要理解回傳的結構」。**

MCP 工具的 schema（名稱、描述、參數定義）會佔用 context window。每個 server 平均數百至數千 tokens；若同時掛載 40 個工具，佔用量可達 55,000 tokens（實測數據）。

- `git log --oneline` → 純文字，bash 即可，不必 server-git
- SQL 查詢 → 有型別、有欄位名稱 → MCP 讓模型少猜一層
- 讀本地檔案 → `cat` bash 即可，server-filesystem **不必要**

### kai 目前的工具配置

| 工具 | 類型 | 理由 |
|------|------|------|
| `bash_exec` | 內建 CLI | 系統操作、git、uv、grep 等 |
| `web_search` | 內建 | SearXNG 封裝，schema 精準可控 |
| `fetch_urls` | 內建 | Jina Reader，回傳需截斷處理 |
| `context7` | MCP http | 無 CLI，需 API key，HTTP 服務 |
| `RepoMapper` | MCP stdio | 自訂工具，回傳結構化程式碼地圖 |
| `_playwright` | MCP（停用）| 有狀態；有需要時移除 `_` 前綴啟用 |
| `_filesystem` | MCP（停用）| bash 能做，多餘的 token 開銷 |

### MCP 的獨特優勢（bash 做不到的）

1. **有狀態會話**：playwright 保持同一瀏覽器視窗跨多次呼叫
2. **集中式憑證管理**：OAuth 2.1、API key 由 server 持有，agent 不需要直接接觸金鑰
3. **工具發現**：新增 server 後 `/mcp-reload` 即可，不需改程式碼
4. **跨 agent 共用**：同一 MCP server 可同時服務多個 agent 實例

### 現實限制

- MCP server 連線失敗率高於 bash（須處理 timeout、連線重試）
- Streaming HTTP 協議在某些環境下不穩定
- stdio server 是子行程，kai 關閉時需要 `force_kill()` 清理

---

## 寫自己的 MCP Server

最簡單的 Python MCP server：

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-tools")

@mcp.tool()
def my_tool(query: str) -> str:
    """做某件事並回傳結果。"""
    return f"結果：{query}"

if __name__ == "__main__":
    mcp.run()
```

加進 `mcp_servers.json`：

```json
{
  "mcpServers": {
    "my-tools": {
      "command": "python",
      "args": ["my_server.py"]
    }
  }
}
```

詳細文件：[modelcontextprotocol.io/docs/concepts/servers](https://modelcontextprotocol.io/docs/concepts/servers)
