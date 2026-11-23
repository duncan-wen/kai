# 貢獻指南

謝謝你願意幫忙改進 Kai！無論是回報 bug、提出想法、改文件或送程式碼都很歡迎。

參與本專案即表示你同意遵守 [行為準則](CODE_OF_CONDUCT.md)。
**安全問題請勿開公開 issue**，改依 [SECURITY.md](SECURITY.md) 私下回報。

## 回報問題與提出想法

- **Bug**：用 [Bug 回報範本](https://github.com/duncan-wen/kai/issues/new?template=bug_report.yml)，附上模型、後端、重現步驟。
  開啟 `DEBUG_TOOLS=1` 後 `.kai/tool_calls.log` 會記錄工具呼叫，很有助於排查（貼上前請先刪掉敏感內容）。
- **功能建議**：用 [功能建議範本](https://github.com/duncan-wen/kai/issues/new?template=feature_request.yml)，說明想解決的問題比提出解法更重要。
- 新 issue 會先標上 `needs-triage`，維護者評估後改為 `ready-for-human`（歡迎認領）、`needs-info`（等你補資料）或 `wontfix`。

## 開發環境

需求：Python 3.13+、[uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/duncan-wen/kai.git && cd kai
uv sync --all-extras          # 含本地 TTS 引擎等選配依賴
cp .env.example .env          # 填入要用的 API key
uv run python app.py
```

沒有雲端金鑰也能開發：裝 [Ollama](https://ollama.com/) 後用預設的 `qwen3-vl:4b`。
架構、設定系統與已知限制見 [DEVELOPMENT.md](DEVELOPMENT.md)；術語（Session、Archive…）見 [GLOSSARY.md](GLOSSARY.md)。

## 送出 PR 前

```bash
uv run ruff check .           # lint
uv run ruff format .          # 格式（CI 會跑 --check）
uv run pytest tests/          # 測試
```

CI 會在每個 PR 跑這三項，全部通過才會合併。

- 從 `main` 開分支，一個 PR 只做一件事；改動較大時建議先開 issue 討論方向
- 修 bug 請附上能重現問題的測試；新功能請附測試
- 改到使用者可見的行為（指令、設定、畫面），請一併更新 README 或 `docs/`
- 依賴變更請用 `uv add` / `uv remove`，並提交更新後的 `uv.lock`

## 程式風格

完整規範見 [CONVENTIONS.md](CONVENTIONS.md)，重點：

- 程式碼註解、log、UI 文字、錯誤訊息一律用**繁體中文**，避免中國大陸用語（「專案」非「項目」、「程式碼」非「代碼」、「函式」非「函數」）
- 公開函式與方法都要有型別註記（含回傳型別）
- I/O 用 `async def`；阻塞呼叫透過 `asyncio.to_thread()`
- `core/` 不可 import `widgets/`（核心與 UI 分層）

## Commit 訊息

採用 [Conventional Commits](https://www.conventionalcommits.org/zh-hant/)，說明用繁體中文：

```
<type>(<scope>): <摘要>

<為什麼要改、改了什麼>
```

常用 type：`feat`、`fix`、`docs`、`refactor`、`test`、`ci`、`build`、`chore`、`style`。
例：`fix(session): session_id 改為持久 ID，修正每輪被拆成不同 Langfuse session`

## 授權

你提交的貢獻將以本專案的 [MIT License](LICENSE) 授權。
