# kai

kai 是在終端機裡和本地／雲端模型對話、並能開出多個 agent 的 TUI。這份詞彙表定義專案內 domain 概念的唯一叫法。

## 對話紀錄

**Session**：
一段正在進行的對話，屬於某一個 Agent，各 Agent 的 session 互相獨立；可以帶一個名稱。
_Avoid_：history（指對話時）、conversation、chat log

**Archive**：
一段已結束、被封存起來的 session，之後可以被 resume。產生 archive 的動作稱為「封存」。
_Avoid_：歸檔、歸檔 session、snapshot、backup

**Resumed session**：
從某個 archive 複製回來、尚未新增任何對話的 session；一送出新訊息就變回一般 session。
_Avoid_：restored session

## Agent

**Agent**：
一個執行中、可以對話的個體，擁有自己的 session。main chat 也是一個 Agent。
_Avoid_：bot、assistant、subagent（指 Agent 本身時）

**Main agent**：
main chat 那個 Agent；沒有 agent definition，隨 app 啟動而存在、不能關閉。
_Avoid_：主 chat（指 Agent 時）、default agent

**Agent definition**：
磁碟上描述一個 Agent 該長什麼樣的設定（模型、system prompt、暱稱、語音等）；開啟或 reload 時套用到 Agent 上。
_Avoid_：agent config、agent（指磁碟上的設定時）

**Agent tab**：
顯示某個非 main 的 Agent 的 tab；一個 Agent definition 同時最多開一個 Agent tab。
_Avoid_：subagent tab、agent（指 tab 時）
