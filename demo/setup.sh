#!/bin/bash
# 建立乾淨的 demo 工作目錄（每次錄影前重建）
set -e

DEMO_DIR="${DEMO_DIR:-/tmp/kai-demo}"
rm -rf "$DEMO_DIR"
mkdir -p "$DEMO_DIR/.kai" "$DEMO_DIR/notes" "$DEMO_DIR/scripts"
cd "$DEMO_DIR"

cat > README.md <<'MD'
# 台股觀察筆記

每週整理關注個股的新聞與技術面。
MD

cat > notes/watchlist.md <<'MD'
# 觀察清單

- 2330 台積電
- 2317 鴻海
- 2454 聯發科
MD

cat > scripts/fetch_price.py <<'PY'
"""抓取收盤價（示範用）。"""


def fetch_close(symbol: str) -> float:
    raise NotImplementedError
PY

# 專案層級設定：用回應快的雲端模型、開啟工具卡片
cat > .kai/config.json <<'JSON'
{
  "model": "deepseek-flash",
  "create_agent_model": "deepseek-flash",
  "debug_tools": true
}
JSON

printf ".kai/\n" > .gitignore
git init -q
git -c user.name=demo -c user.email=demo@example.com add .gitignore README.md notes scripts
git -c user.name=demo -c user.email=demo@example.com commit -qm "init"

echo "demo 目錄已建立：$DEMO_DIR"
