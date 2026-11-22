# Skill 支援規劃

本文說明如何讓 kai 支援 **Agent Skills**——一套「按需載入」的可複用能力包（說明 + 捆綁資源），讓任何 agent tab 都能在需要時載入特定技能。內容涵蓋概念、目錄規劃、實作架構、關鍵設計決策與分階段計畫。

---

## 背景與動機

kai 目前的擴充機制有兩個層級：

| 機制 | 定位 | 載入時機 |
|------|------|----------|
| **agent** | 人格 / 工作流（system prompt + 專屬 tab） | 開啟 tab 時全時載入 |
| **notes.md**（專案記憶） | 專案知識 | 每次 `_rebuild_system_prompt` 全時注入 |

兩者都是「全時載入」，缺的是**「按需載入」的可複用能力**——例如某個 agent 偶爾需要「處理 PDF」「操作特定資料庫」「執行公司內部流程」時，不必把這些說明永遠塞進 system prompt 佔 context。

**Skill 補的洞**：跨 agent 共用、觸發才載入、可捆綁預寫腳本的能力包。

---

## Skill 核心概念：兩層「漸進揭露」

Skill 的精髓是**不把所有內容塞進 context**，分兩層：

1. **索引層**（永遠在 system prompt）：只有 `名稱 + 一句描述`，讓模型知道「有哪些技能可用」
2. **完整層**（按需載入）：觸發時才把完整 `SKILL.md` 內容 + 資源清單灌進 context

對比 notes.md：notes 是全時注入，skill 是**觸發才載入**。

---

## 目錄規劃

對齊 agents 的 local/global 慣例：

```
<cwd>/.kai/skills/<skill-name>/SKILL.md      # 專案層
~/.kai/skills/<skill-name>/SKILL.md          # 全域層
```

`SKILL.md` 使用 YAML frontmatter：

```yaml
---
name: pdf-summarizer
description: 從 PDF 抽取文字並產生摘要
when_to_use: 使用者需要處理或分析 PDF 檔案時
---
# 實際執行步驟
1. 先檢查是否安裝 pdftotext...
```

每個 skill 一個資料夾，可附 `scripts/`、`references/` 子資料夾（見附錄）。

---

## 實作架構

**推薦路徑：把 skill 做成「工具」，直接吃現成的 `run_tool` 分發。**

| 步驟 | 位置 | 做什麼 |
|------|------|--------|
| 1. 新增 `core/skills.py` | 新檔 | `discover_skills()` 掃兩個目錄 → `Skill` dataclass；`skills_index_text()` 產索引；`load_skill(name)` 回完整內容 |
| 2. 注入索引 | `core/agent.py::_rebuild_system_prompt` | 在 system prompt 加「可用技能」清單（只放名稱+描述，與 MCP server 清單並排） |
| 3. 註冊工具 | `core/tools/`（新增 `skills.py` + 登錄 tools_map） | `use_skill(name)` 回傳完整 SKILL.md + 資源路徑；可選 `list_skills()` |
| 4. 觸發流程 | 模型自己決定 | 看到索引 → 需要時呼叫 `use_skill(...)` → 完整說明進 context → 照做 |

好處：**完全重用現有 `run_tool` 的 MCP/本地工具分發、錯誤處理、以及 langfuse 埋點**（tool span 自動產生）。

### 載入流程

```
system prompt（含 skill 索引）
        │ 模型判斷需要 skill X
        ▼
run_tool("use_skill", {name: "X"})
        │
        ▼
core/skills.py::load_skill("X")
        │ 回傳完整 SKILL.md + 資源路徑
        ▼
注入 context → 模型照說明執行
```

---

---

## 使用者介面（側邊欄 + 指令）

除 system prompt 索引與 `use_skill` 工具外，另需兩塊 UI，三者都讀同一個 `discover_skills()`：

### 側邊欄 `🧩 Skills` 區塊

鏡像現有 `🔧 MCP 工具` 區塊（`widgets/sidebar.py`），顯示**個數 + 名稱**：

```
🧩 Skills
  3 個（pdf-summarizer、code-reviewer、git-commit）
```

- `Sidebar.compose()` 加 `🧩 Skills` 標題 + `#sb-skills` 內容區
- 新增 `set_skills(names: list[str])`，在 `app.py` 更新 MCP 的同一批位置呼叫（skill 掃描是同步 fs 讀取，比 MCP async 連線更簡單）
- 不顯示描述：描述太長會爆版，完整資訊放到 `/skills` screen

### `/skills` 指令

新增斜線指令（`widgets/commands.py`），開**完整 list screen**：

- `Hit(0.8, "/skills  列出可用 skills", app._do_skills_list, ...)`
- 新增 `_do_skills_list`（`mixins/skills.py`），開 `SkillsListScreen`（鏡像 `MCPListScreen`）
- 每列顯示：**名稱 + description + when_to_use**

> 三處（system prompt 索引、側邊欄、`/skills`）全部讀 `core/skills.py::discover_skills()`，單一真相來源。

---

## 關鍵設計決策

1. **載入方式**：`use_skill(name)` 回傳「說明文字」而非執行程式——模型讀完後用**現有的 `bash_exec` 工具**去跑 skill 捆綁的 script。**執行權限不變，安全邊界不變**。
2. **索引 vs 全載**：skill 少（< 5 個）可退化成「全塞進 system prompt」省掉工具往返；數量多才值得做兩層。
3. **與 agent 的關係**：skill 是「能力/知識」，agent 是「人格/工作流」。skill **跨 agent 共用**，任何 tab 都能用。

---

## 分階段計畫

| 階段 | 內容 | 新增 |
|------|------|------|
| 階段 0 | `core/skills.py` 掃描器（單一真相來源） | 掃描器 |
| 階段 1 | system prompt 注入索引 **＋ 側邊欄 `🧩 Skills` 區塊** | 顯示層（讀掃描結果） |
| 階段 2 | `use_skill` / `list_skills` 工具 **＋ `/skills` 指令 + `SkillsListScreen`** | 互動層 |
| 階段 3（選配） | scripts/ 捆綁 + app tool（`create_skill`） | 能力層 |

---

## 決策紀錄（已定案，2026-11-23）

| 事項 | 決策 |
|------|------|
| 存放層級 | **全域 `~/.kai/skills` + 專案 `.kai/skills` 雙層**（與 agents 慣例一致，專案層優先） |
| SKILL.md 格式 | **完全相容 Claude Skills 格式**（YAML frontmatter + Markdown 本體，可直接搬現成 skill） |
| scripts/ 捆綁 | **分階段**：階段 0–2 先做「純說明」，階段 3 再加入 scripts/ 捆綁支援 |
| 側邊欄顯示 | **個數 + 名稱**（鏡像 MCP 區塊，不顯示長描述） |
| `/skills` 指令 | **完整 list screen**（名稱 + description + when_to_use，鏡像 `MCPListScreen`） |

> 執行策略：先打穩載入/觸發機制，scripts/ 捆綁（安全提示 + 路徑管理）留到第二波。

---

## 附錄：skill 捆綁 scripts/ 說明

skill 資料夾可附帶**可執行的腳本**：

```
skills/pdf-summarizer/
├── SKILL.md          ← 說明文字（模型讀這個）
└── scripts/
    ├── extract.py    ← 可執行的腳本
    └── cleanup.sh
```

`SKILL.md` 的步驟叫模型**執行捆綁好的腳本**，而非憑空現寫。

| 類型 | skill 內容 | 模型做的事 |
|------|-----------|-----------|
| **純說明** | 只有 SKILL.md 文字 | 模型自己發想步驟、自己寫指令 |
| **捆綁 scripts** | SKILL.md + 現成腳本 | 模型照說明**跑現成腳本**，結果更穩定可靠 |

**關鍵**：腳本不是 skill 系統自己偷偷執行，而是模型讀了 SKILL.md 後透過**現有的 `bash_exec` 工具**去跑。執行權限沒變，只是腳本內容由 skill 作者預先寫好、測試過。

**建議**：第一版先做「純說明」，把載入/觸發機制打穩；scripts 捆綁留到第二版，因為它牽涉安全提示（引導模型優先跑附帶腳本）與路徑管理，複雜度明顯較高。
