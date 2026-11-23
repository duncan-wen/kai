import asyncio

import httpx
from loguru import logger

from .. import config as _cfg

DIFY_RETRIEVE_TOOL = {
    "type": "function",
    "function": {
        "name": "dify_retrieve",
        "description": (
            "查詢知識庫，回傳最相關的文字段落。"
            "當問題需要查閱專業知識、文件內容或內部資料時使用。"
            "必須指定 dataset_ids 選擇相關的 KB，不要一次查全部。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "查詢問題"},
                "dataset_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要查詢的知識庫 ID 列表（從 system prompt 的 KB 清單中選擇）",
                },
            },
            "required": ["query", "dataset_ids"],
        },
    },
}


async def fetch_dify_datasets(dataset_ids: list[str]) -> str:
    """從 Dify API 取得指定 KB 的名稱與說明，回傳 Markdown 表格（供注入 system prompt）。"""
    if not _cfg.DIFY_API_KEY:
        return "（未設定 DIFY_API_KEY）"
    headers = {"Authorization": f"Bearer {_cfg.DIFY_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_cfg.DIFY_API_URL}/v1/datasets",
                headers=headers,
                params={"page": 1, "limit": 100},
            )
            resp.raise_for_status()
            all_ds = {d["id"]: d for d in resp.json().get("data", [])}
    except Exception as e:
        logger.error(f"fetch_dify_datasets 失敗：{e}")
        return f"（取得 KB 清單失敗：{e}）"

    rows = ["| ID | 名稱 | 說明 |", "|-----|------|------|"]
    for did in dataset_ids:
        d = all_ds.get(did, {})
        name = d.get("name", "（找不到）")
        desc = d.get("description") or "（無說明）"
        rows.append(f"| `{did}` | {name} | {desc} |")
    return "\n".join(rows)


async def _dify_retrieve_one(query: str, dataset_id: str) -> str:
    """查詢單一 Dify 知識庫。"""
    url = f"{_cfg.DIFY_API_URL}/v1/datasets/{dataset_id}/retrieve"
    headers = {
        "Authorization": f"Bearer {_cfg.DIFY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "retrieval_model": {
            "search_method": "hybrid_search",
            "reranking_enable": bool(_cfg.DIFY_RERANK_MODEL),
            "reranking_model": {
                "reranking_provider_name": _cfg.DIFY_RERANK_PROVIDER,
                "reranking_model_name": _cfg.DIFY_RERANK_MODEL,
            }
            if _cfg.DIFY_RERANK_MODEL
            else None,
            "top_k": _cfg.DIFY_TOP_K,
            "score_threshold_enabled": False,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"dify_retrieve {dataset_id} 失敗：{e}")
        return f"[Error] 查詢 KB {dataset_id} 失敗：{e}"

    records = data.get("records", [])
    if not records:
        return f"[KB {dataset_id}] 未找到相關內容"

    parts = [f"【KB：{dataset_id}】"]
    for i, rec in enumerate(records, 1):
        seg = rec.get("segment", {})
        score = rec.get("score", 0)
        content = seg.get("content", "").strip()
        doc_name = seg.get("document", {}).get("name", "")
        parts.append(f"[{i}] 來源：{doc_name}（score: {score:.3f}）\n{content}")
    return "\n\n".join(parts)


async def dify_retrieve(query: str, dataset_ids: list) -> str:
    """並行查詢多個 Dify 知識庫，回傳已整理的段落內容。"""
    results = await asyncio.gather(
        *[_dify_retrieve_one(query, did) for did in dataset_ids]
    )
    return "\n\n---\n\n".join(results)
