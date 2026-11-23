import time

import httpx
from loguru import logger
from pydantic import BaseModel, field_validator
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .. import config as _cfg

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web and return titles, URLs, and short snippets. "
            "Results are summaries only — use fetch_urls to read the full content of specific pages."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    },
}

FETCH_URLS_TOOL = {
    "type": "function",
    "function": {
        "name": "fetch_urls",
        "description": (
            "Fetch the full Markdown content of 1–5 web pages concurrently via Jina Reader. "
            "Use after web_search when snippets are not enough and you need the complete article or documentation. "
            "Pass a single-element list for one URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of full URLs to fetch (max 5)",
                }
            },
            "required": ["urls"],
        },
    },
}


class WebSearchArgs(BaseModel):
    """web_search 入參格式，對 AI 傳入的參數做強型別校驗。"""

    query: str
    num_results: int = _cfg.SEARCH_NUM_RESULTS

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query 不能為空字串")
        return v.strip()

    @field_validator("num_results")
    @classmethod
    def num_results_range(cls, v: int) -> int:
        if not 1 <= v <= 10:
            raise ValueError("num_results 必須介於 1-10")
        return v


def estimate_tokens(text: str) -> int:
    """粗估 token 數：中文字元 ÷ 1.5，其餘字元 ÷ 4。"""
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    other = len(text) - cjk
    return int(cjk / 1.5 + other / 4)


def budget_results(results: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """控制搜尋結果的總字元數在 MAX_SEARCH_CHARS 以內。"""
    total = sum(len(r) for _, r in results)
    if total <= _cfg.MAX_SEARCH_CHARS:
        return results
    approx_tokens = estimate_tokens("".join(r for _, r in results))
    logger.warning(
        f"搜尋結果共 {total} 字元（約 {approx_tokens} tokens），超出限制 {_cfg.MAX_SEARCH_CHARS}，壓縮中"
    )
    compressed, budget = [], _cfg.MAX_SEARCH_CHARS
    for fn_name, content in results:
        if budget <= 0:
            break
        compressed.append((fn_name, content[:budget]))
        budget -= len(content)
    return compressed


def _jina_headers() -> dict:
    h = {"Accept": "application/json"}
    if _cfg.JINA_API_KEY:
        h["Authorization"] = f"Bearer {_cfg.JINA_API_KEY}"
    return h


async def _fetch_one(url: str, client: httpx.AsyncClient, max_chars: int) -> str:
    try:
        resp = await client.get(
            f"https://r.jina.ai/{url}", headers=_jina_headers(), follow_redirects=True
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        title = data.get("title", "")
        page_url = data.get("url", url)
        content = data.get("content", "").strip()
        if len(content) > max_chars:
            cut = content.rfind("\n\n", 0, max_chars)
            content = content[: cut if cut > 0 else max_chars]
            content += f"\n\n…（共 {len(content)} 字元，已截斷）"
        logger.debug(f"fetch_url {url} → {len(content)} 字元")
        return f"# {title}\nURL: {page_url}\n\n{content}"
    except Exception as e:
        logger.warning(f"fetch_url 失敗：{url}：{e}")
        return f"[Error] fetch_url 失敗：{e}"


async def web_search(query: str, num_results: int = _cfg.SEARCH_NUM_RESULTS) -> str:
    """非同步打 SearXNG API，使用 tenacity 做 exponential backoff 重試。"""
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=_cfg.SEARCH_TIMEOUT) as client:

        @retry(
            stop=stop_after_attempt(_cfg.SEARCH_RETRIES + 1),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
            retry=retry_if_exception_type(
                (httpx.TimeoutException, httpx.HTTPStatusError)
            ),
            before_sleep=lambda state: logger.warning(
                f"搜尋 '{query}' 第 {state.attempt_number} 次失敗，重試中..."
            ),
            reraise=True,
        )
        async def _fetch() -> list:
            resp = await client.get(
                _cfg.SEARXNG_URL, params={"q": query, "format": "json"}
            )
            resp.raise_for_status()
            return resp.json().get("results", [])

        try:
            items = (await _fetch())[:num_results]
        except Exception as e:
            logger.error(f"搜尋 '{query}' 全部重試失敗：{e}")
            return f"[Search failed: {e}]"

    elapsed = time.perf_counter() - t0
    logger.debug(f"搜尋 '{query}' 完成，{len(items)} 筆，{elapsed:.2f}s")
    if not items:
        return "No results found."
    pairs = [
        (r["title"], f"[{r['title']}]({r['url']})\n{r.get('content', '')}")
        for r in items
    ]
    budgeted = budget_results(pairs)
    result = "\n\n".join(text for _, text in budgeted)
    logger.debug(f"傳給模型的內容（{len(result)} 字元）:\n{result}")
    return result


async def fetch_url(url: str) -> str:
    """用 Jina Reader 抓取單一網頁完整內容，回傳結構化 Markdown。"""
    async with httpx.AsyncClient(timeout=20.0) as client:
        return await _fetch_one(url, client, _cfg.FETCH_MAX_CHARS)


async def fetch_urls(urls: list) -> str:
    """並行抓取多個網頁（最多 5 個），共用同一個 httpx 連線池。"""
    import asyncio

    targets = urls[:5]
    per_limit = max(_cfg.FETCH_MAX_CHARS // len(targets), 2000)
    async with httpx.AsyncClient(timeout=20.0) as client:
        results = await asyncio.gather(
            *[_fetch_one(u, client, per_limit) for u in targets]
        )
    return "\n\n---\n\n".join(results)
