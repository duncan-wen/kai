"""全域測試設定。

測試一律停用 Langfuse 上報：避免單元測試的假資料（如假對話、假回覆）
被送到真實的觀測專案，污染 production 的 trace 統計。
"""

import pytest

from core import langfuse_client as _lf


@pytest.fixture(autouse=True)
def _disable_langfuse(monkeypatch):
    """把 Langfuse 憑證清空並重置 lazy singleton，使 get_langfuse() 回 None。"""
    monkeypatch.setattr("core.config.LANGFUSE_PUBLIC_KEY", None)
    monkeypatch.setattr("core.config.LANGFUSE_SECRET_KEY", None)
    monkeypatch.setattr(_lf, "_client", None)
    monkeypatch.setattr(_lf, "_checked", False)
    yield
