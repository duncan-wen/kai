# kai

kai 是在終端機裡和本地／雲端模型對話、並能開出多個 agent 的 TUI。這份詞彙表定義專案內 domain 概念的唯一叫法。

## 對話紀錄

**Session**：
一段正在進行的對話，屬於 main chat 或某一個 agent，各自獨立；可以帶一個名稱。
_Avoid_：history（指對話時）、conversation、chat log

**Archive**：
一段已結束、被封存起來的 session，之後可以被 resume。產生 archive 的動作稱為「封存」。
_Avoid_：歸檔、歸檔 session、snapshot、backup

**Resumed session**：
從某個 archive 複製回來、尚未新增任何對話的 session；一送出新訊息就變回一般 session。
_Avoid_：restored session
