"""core.tools — 工具目錄。外部 import 不變。"""
from .bash import BASH_TOOL, CLEAN_PATTERN, bash_exec, clean_thinking
from .dify import DIFY_RETRIEVE_TOOL, dify_retrieve, fetch_dify_datasets
from .dispatch import OPEN_AGENT_TOOL, run_tool
from .skills import LIST_SKILLS_TOOL, USE_SKILL_TOOL, list_skills, use_skill
from .web import (
    FETCH_URLS_TOOL,
    SEARCH_TOOL,
    WebSearchArgs,
    fetch_url,
    fetch_urls,
    web_search,
)

__all__ = [
    "BASH_TOOL",
    "CLEAN_PATTERN",
    "DIFY_RETRIEVE_TOOL",
    "FETCH_URLS_TOOL",
    "LIST_SKILLS_TOOL",
    "OPEN_AGENT_TOOL",
    "SEARCH_TOOL",
    "USE_SKILL_TOOL",
    "WebSearchArgs",
    "bash_exec",
    "clean_thinking",
    "dify_retrieve",
    "fetch_dify_datasets",
    "fetch_url",
    "fetch_urls",
    "list_skills",
    "run_tool",
    "use_skill",
    "web_search",
]
