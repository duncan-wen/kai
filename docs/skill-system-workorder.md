# Skill 支援 — 實作工單

> 決策與設計見 `docs/skill-system.md`。本文件是可直接照做的 task checklist。

## 分支與範圍

- 分支：`feature/skill-support`（從 main 切出）
- 範圍：階段 0–3，每階段可獨立提交

---

## 階段 0：掃描器（單一真相來源）

- [x] 新增 `core/skills.py`
  - `Skill` dataclass：`name` / `description` / `when_to_use` / `path` / `body`
  - `discover_skills()`：掃 `~/.kai/skills` + `<cwd>/.kai/skills`，專案層覆蓋全域同名
  - 解析 **Claude Skills 格式** `SKILL.md`（YAML frontmatter + Markdown body）
  - `skills_index_text()`：產「名稱 + 描述」索引（供 system prompt 與側邊欄用）
  - `load_skill(name)`：回傳完整 body + 資源清單
  - 無目錄 / 解析失敗時安全 fallback（空 list，不拋錯）

**驗收**：能在 REPL 列舉 skill、正確讀取內容、缺檔不 crash。

---

## 階段 1：顯示層（system prompt 索引 + 側邊欄）

- [x] `core/agent.py::_rebuild_system_prompt` 注入「可用技能」索引（只放名稱+描述）
- [x] `widgets/sidebar.py`
  - `compose()` 加 `🧩 Skills` 標題 + `#sb-skills` 內容區
  - 新增 `set_skills(names: list[str])`，顯示「個數 + 名稱」
- [x] `app.py`：在更新 MCP 的同一批位置呼叫 `set_skills`（連線完成 / 切 tab / cd）

**驗收**：側邊欄顯示 `🧩 Skills  N 個（name1、name2）`；新對話 system prompt 含技能索引。

---

## 階段 2：互動層（工具 + /skills 指令）

- [x] 新增 `core/tools/skills.py`
  - `use_skill(name)`：回傳完整 SKILL.md 內容
  - `list_skills()`：回傳索引清單
  - 各自對應 `TOOL` schema，註冊到 `tools_map`（走既有 `run_tool` 分發）
- [x] `widgets/commands.py`：加 `Hit(0.8, "/skills  列出可用 skills", app._do_skills_list, ...)`
- [x] 新增 `mixins/skills.py`：`_do_skills_list` → 開 `SkillsListScreen`（鏡像 `MCPListScreen`）
  - 每列顯示：名稱 + description + when_to_use

**驗收**：模型可呼叫 `use_skill("xxx")` 拿到內容；`/skills` 開 screen 列出全部技能。

---

## 階段 3：能力層（scripts 捆綁 + app tool）

- [x] scripts/ 捆綁支援：路徑解析 + 安全提示（模型經既有 `bash_exec` 執行，不新增權限）
- [x] app tool：`create_skill`（`list_skills` 已於階段 2 完成，走 `run_tool` 分發，不重複做）

**驗收**：skill 可附 `scripts/`，模型依 SKILL.md 指示透過 bash 執行。

---

## 收尾

- [x] 更新 `docs/README.md` 索引（已於階段 0 收錄 `skill-system.md`）
- [x] 自測 + 提交（每階段一 commit，訊息標 `feat(skills): ...`）
- [ ] 視需要合回 main（留待使用者決定）

---

## 完成度追蹤

| 階段 | 狀態 |
|------|------|
| 0 掃描器 | ✅ 完成 |
| 1 顯示層 | ✅ 完成 |
| 2 互動層 | ✅ 完成 |
| 3 能力層 | ✅ 完成 |
