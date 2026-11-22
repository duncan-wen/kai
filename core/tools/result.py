"""core.tools.result — 工具執行結果的結構化表示。

過去 UI 層（streaming / agents 兩條路徑）用字串關鍵字（`Traceback`、
`[Error]`、`[stderr]` …）猜測工具是否出錯，造成：

1. 誤塗紅：工具成功，但回傳的程式碼片段剛好含 `Traceback` 字樣
   （codegraph、context7 這類回傳原始碼的工具最常中招）。
2. 漏判：工具失敗，但訊息不含上述關鍵字。
3. 不一致：streaming 路徑有 exit code 判斷，agents 路徑沒有。

改由分發層與工具本身直接標記 `is_error` / `exit_code`，顯示層只讀 flag。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolResult:
    """單次工具呼叫的結果。

    Attributes:
        fn_name: 工具名稱（如 `bash_exec`）。
        output: 給模型看的原始輸出文字。
        is_error: 是否為錯誤（致命失敗）。
        exit_code: 子程序 exit code（僅 bash 類工具有值）。
    """

    fn_name: str
    output: str
    is_error: bool = False
    exit_code: int | None = None

    @property
    def is_warn(self) -> bool:
        """exit code 1：有問題但非致命，UI 顯示 ⚠️。"""
        return not self.is_error and self.exit_code == 1

    def __str__(self) -> str:
        return self.output
