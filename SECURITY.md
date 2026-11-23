# 安全政策

## 回報漏洞

**請勿在公開 issue 回報安全問題。**

請到 GitHub 的 [Security Advisories](https://github.com/duncan-wen/kai/security/advisories/new)
（repo 頁面 → Security → Report a vulnerability）私下回報，內容請包含：

- 問題描述與影響範圍
- 重現步驟（使用的模型、輸入、設定）
- kai 的 commit 或版本

本專案由個人維護，會盡快回覆並在修正後公開說明。

## 支援版本

目前只維護 `main` 分支的最新版本。

## 威脅模型：使用前請先了解

kai 是一個**能在你的電腦上執行動作的 AI agent**。以下是設計上的行為，不是漏洞，但你需要知道：

### 1. AI 可以執行任意 shell 指令

內建工具 `bash_exec` 讓模型在**不經你確認**的情況下執行 shell 指令：

- 以啟動 kai 的使用者權限、在目前工作目錄執行（`/bin/bash -c`）
- 沒有沙箱；檔案刪改、網路存取、安裝套件都做得到
- `sudo` 會以 `sudo -n`（非互動）執行：若你的帳號設了免密碼 sudo，指令就會以 root 權限成功
- 單次指令逾時由 `BASH_TIMEOUT` 控制（預設 30 秒）

### 2. 提示注入（prompt injection）

`web_search`、`fetch_urls` 取回的網頁，以及模型讀到的檔案內容，都可能藏有惡意指示，
誘導模型呼叫 `bash_exec`。本地小模型（2–4B）特別容易被誘導。

### 3. 專案目錄裡的設定會自動生效

在某個目錄啟動 kai 時，會自動載入該目錄的：

- `.kai/mcp_servers.json`：其中的 stdio MCP server **會在啟動時被執行**（等同執行任意指令）
- `.kai/skills/`：skill 內容會進入模型的 context

**不要在不信任的 repo 裡啟動 kai**，啟動前先檢查這兩個位置。

### 4. 資料會送到第三方服務

- 使用雲端模型（Gemini、DeepSeek）時，對話與工具結果會送到該供應商
- 設定 Langfuse 後，對話、工具輸入輸出與 system prompt 會上傳到 Langfuse。
  上傳前會遮罩常見格式的密鑰（`sk-…`、`*_API_KEY=…` 等）與環境變數中的金鑰值，
  但遮罩是**盡力而為**，無法保證攔下所有敏感資料

## 建議的使用方式

- 在容器、VM 或專用的低權限帳號中執行，尤其是搭配本地小模型時
- 不要對執行 kai 的帳號設定免密碼 sudo
- 不要在存放機密（金鑰、憑證、個資）的目錄中使用
- API key 只放在 `.env`（已被 `.gitignore` 排除），不要寫進 `.kai/config.json` 或對話中

## 回報範圍

以下屬於我們想知道的安全問題：

- 不經模型決策、由外部輸入直接觸發的指令執行途徑
- 金鑰或 `.env` 內容被寫入 log、history 或送往第三方（遮罩失效的具體案例）
- 繞過本文件描述之行為邊界的情況

上述「威脅模型」所列的設計行為本身不在回報範圍內，但歡迎提出降低風險的改進建議。
