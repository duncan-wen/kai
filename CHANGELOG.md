# 變更紀錄

本檔依循 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/) 格式，
版本號採用 [語意化版本](https://semver.org/lang/zh-TW/)。

## [Unreleased]

### 變更

- `langfuse` 改為選配依賴：需 `uv sync --extra observability` 才會安裝，
  省下約 10 個只有追蹤功能用到的套件（OpenTelemetry、protobuf 等）。
  設了 `LANGFUSE_*` 金鑰但未安裝時，會在 log 提示並停用追蹤
- 移除未使用的 dev 依賴 `ipykernel`

## [0.1.0] - 2026-11-23

第一個正式版本。

### 新增

- **Textual TUI**：檔案樹（多選刪除、外部變更偵測）、可編輯的語法高亮檔案檢視器、多 tab、
  命令面板、輸入歷史、多行貼上、`!` 直接執行 shell、`@path` 注入檔案內容
- **多後端模型**：本地 Ollama（Qwen、Gemma 等）、Gemini、DeepSeek；`/model` 熱切換保留對話；
  Thinking 模式、Vision 圖片輸入、模型暱稱
- **工具呼叫迴圈**：多工具並行執行與呼叫次數上限；內建 `web_search`（SearXNG）、
  `fetch_urls`、`bash_exec`、`dify_retrieve`（Dify RAG）
- **Agent Factory**：`/create-agent` 由 LLM 設計整份分身設定；分身各自一個 tab、
  獨立歷史與記憶；兩層目錄（專案 `agents/` 與全域 `~/.kai/agents/`）；
  分身之間用 `send_to_agent` 傳訊；`/reload-agent` 熱更新 system prompt
- **App tools**：22 個 AI 可呼叫的 app 功能，用自然語言開關分身、切模型、管理 MCP、設定 TTS
- **MCP 整合**：stdio 與 HTTP transport、全域與專案兩層設定、`/mcp-add`、`/mcp-del`、
  `/mcp-reload` 熱重載；偵測 RepoMapper 自動調整程式碼探索規則
- **Skills**：支援 Claude Skills 格式（`SKILL.md`），漸進揭露載入、`/skills` 列表、
  AI 可建立新 skill；專案層附 Langfuse skill
- **Session 管理**：`/cls` 封存、`/resume` 叫回（含命名、改名、刪除）、`/compact` 摘要壓縮、
  `/ask` 臨時提問與 `/save`、`/remember` 長期記憶
- **TTS**：edge-tts、Azure、MeloTTS、Kokoro（API 與本地 ONNX）五種引擎，分身可各自設定語音
- **設定系統**：`<專案>/.kai/config.json` > `~/.kai/config.json` > `.env` > 預設值；
  `/init-config` 互動面板查看來源並匯出、`/reload-config` 免重啟套用
- **Langfuse 可觀測性**（選配）：每輪對話一個 trace、持久 session ID、ChatML 訊息格式
  讓 Sessions 頁面可重播對話；token 與 cache 命中成本、首 token 時間、模型參數；
  上傳前遮罩密鑰、工具失敗標記 `ERROR`

### 安全

- 新增 `SECURITY.md`：漏洞私下回報管道與威脅模型（`bash_exec` 無沙箱、提示注入、
  專案層 MCP 設定啟動即執行、資料外送）

[Unreleased]: https://github.com/duncan-wen/kai/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/duncan-wen/kai/releases/tag/v0.1.0
