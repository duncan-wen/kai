"""AppToolsMixin 的靜態 tool schema 定義。
修改工具誌明或參數只需動這個檔案。
動態 schema（create_agent_tool、switch_model_tool）依賴模型清單，保留在 app_tools.py。
"""

LIST_AGENTS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_agents",
        "description": "列出所有可用的 AI agent（本地和全域）。當用戶問「有哪些 agent」、「列出所有 agent」時使用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

LIST_OPEN_AGENTS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_open_agents",
        "description": (
            "查詢目前已開啟的 agent tab 清單。"
            "收到「開啟 XXX」請求時，**先呼叫此工具**確認是否已開啟："
            "已開啟 → 呼叫 load_agent 切換過去；未開啟 → 呼叫 load_agent 載入。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

DESIGN_SYSTEM_PROMPT_TOOL = {
    "type": "function",
    "function": {
        "name": "design_system_prompt",
        "description": (
            "呼叫專用設計模型（CREATE_AGENT_MODEL）生成高品質的 agent system_prompt 和 greeting。"
            "建立 agent 的標準流程：先呼叫此工具取得 system_prompt，再呼叫 create_agent。"
            "例外：用戶已明確提供完整 system_prompt 時可直接呼叫 create_agent。"
            "**必須呼叫此工具取得 system_prompt，嚴禁自行撰寫後跳過。**"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "agent 名稱"},
                "description": {
                    "type": "string",
                    "description": "agent 的詳細用途描述",
                },
            },
            "required": ["name", "description"],
        },
    },
}

LOAD_AGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "load_agent",
        "description": "載入或切換到一個 agent tab。請先呼叫 list_open_agents 確認狀態，再呼叫此工具。**必須呼叫此工具才能實際開啟或切換，嚴禁只用文字假裝已載入。**",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "agent 名稱、暱稱或目錄名"},
            },
            "required": ["name"],
        },
    },
}

DELETE_AGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "delete_agent",
        "description": "刪除一個 agent（不可恢復）。只有用戶明確要求刪除時才呼叫。**必須呼叫此工具才能實際刪除，嚴禁只用文字假裝已刪除。**",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "agent 名稱或暱稱"},
            },
            "required": ["name"],
        },
    },
}

CHANGE_DIR_TOOL = {
    "type": "function",
    "function": {
        "name": "change_dir",
        "description": "切換工作目錄。當用戶說「切換到 ~/projects/xxx」、「幫我看某個目錄的專案」時使用。**必須呼叫此工具，嚴禁只用文字假裝已切換。**",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目標目錄路徑，支援 ~ 展開"},
            },
            "required": ["path"],
        },
    },
}

REMEMBER_TOOL = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": "將重要資訊永久記錄到 .kai/notes.md，下次對話也能看到。當用戶說「記住 XXX」、「把這個記下來」時使用。**必須呼叫此工具才能實際寫入，嚴禁只用文字假裝已記住。**",
        "parameters": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "要記住的內容"},
            },
            "required": ["note"],
        },
    },
}

CREATE_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "create_skill",
        "description": (
            "建立一個新的 skill（能力包）。當用戶說「幫我做一個 XX 的 skill」時使用。"
            "會建立 SKILL.md（YAML frontmatter + 操作步驟本體）。"
            "**必須呼叫此工具才能實際建立，嚴禁只用文字假裝已建立。**"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "skill 名稱（同時用作目錄名，建議 kebab-case 英文）",
                },
                "description": {
                    "type": "string",
                    "description": "一行描述這個 skill 的用途",
                },
                "body": {
                    "type": "string",
                    "description": "SKILL.md 本體：給模型的詳細操作步驟（Markdown）",
                },
                "when_to_use": {
                    "type": "string",
                    "description": "觸發時機說明，可省略",
                },
                "global_scope": {
                    "type": "boolean",
                    "description": "預設 false。true = 存到 ~/.kai/skills/（跨專案）；false = 存到 .kai/skills/（專案）",
                },
            },
            "required": ["name", "description", "body"],
        },
    },
}

RENAME_SESSION_TOOL = {
    "type": "function",
    "function": {
        "name": "rename_session",
        "description": "幫目前這段對話命名，方便之後用 /resume 找到它。當用戶說「幫這次對話取名」或完成明確任務後可主動提議命名。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "簡短描述本次對話主題的名稱"},
            },
            "required": ["name"],
        },
    },
}

COMPACT_HISTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "compact_history",
        "description": (
            "用 AI 壓縮對話歷史，釋放 context window。"
            "當對話很長、用戶說「壓縮歷史」或「清理 context」時使用。"
            "**必須呼叫此工具，嚴禁只用文字假裝已壓縮。**"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

LIST_MCP_TOOL = {
    "type": "function",
    "function": {
        "name": "list_mcp",
        "description": "列出目前已連線的 MCP server 及工具清單。查詢可用 MCP 工具時使用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

ADD_MCP_TOOL = {
    "type": "function",
    "function": {
        "name": "add_mcp",
        "description": (
            "新增 MCP server 到 ~/.kai/mcp_servers.json。"
            "當用戶說「幫我加 playwright MCP」、「新增 XXX server」時使用。"
            "新增後需呼叫 reload_mcp 生效。"
            "**必須呼叫此工具，嚴禁只用文字假裝已新增。**"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "server 名稱"},
                "command": {
                    "type": "string",
                    "description": "執行指令，例如 'npx'（與 url 二選一）",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "指令參數列表",
                },
                "url": {
                    "type": "string",
                    "description": "HTTP/SSE URL（與 command 二選一）",
                },
            },
            "required": ["name"],
        },
    },
}

REMOVE_MCP_TOOL = {
    "type": "function",
    "function": {
        "name": "remove_mcp",
        "description": (
            "從 ~/.kai/mcp_servers.json 移除 MCP server。"
            "移除後需呼叫 reload_mcp 生效。"
            "**必須呼叫此工具，嚴禁只用文字假裝已移除。**"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "server 名稱"},
            },
            "required": ["name"],
        },
    },
}

RELOAD_MCP_TOOL = {
    "type": "function",
    "function": {
        "name": "reload_mcp",
        "description": (
            "重新連線所有 MCP server（新增或移除 server 後需呼叫）。"
            "**必須呼叫此工具，嚴禁只用文字假裝已重載。**"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

TOGGLE_GREETING_TOOL = {
    "type": "function",
    "function": {
        "name": "toggle_greeting",
        "description": (
            "開關 agent 開場白（greeting）功能。"
            "當用戶說「不要 greeting」、「關閉開場白」、「開啟 greeting」時使用。"
            "這層控制所有 agent 載入時是否自動觸發開場白。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "true = 開啟 greeting，false = 關閉 greeting",
                },
            },
            "required": ["enabled"],
        },
    },
}

SET_TTS_TOOL = {
    "type": "function",
    "function": {
        "name": "set_tts",
        "description": (
            "切換 TTS 語音引擎或關閉語音。"
            "當用戶說「開啟語音」、「關閉語音」、「換成 kokoro」時使用。"
            "可用引擎：edge-tts、azure-tts、melotts、kokoro-api、kokoro、off。"
            "**必須呼叫此工具，嚴禁只用文字假裝已切換。**"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "engine": {
                    "type": "string",
                    "enum": [
                        "edge-tts",
                        "azure-tts",
                        "melotts",
                        "kokoro-api",
                        "kokoro",
                        "off",
                    ],
                    "description": "語音引擎",
                },
            },
            "required": ["engine"],
        },
    },
}

RELOAD_AGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "reload_agent",
        "description": (
            "重新從 config.json 載入目前 agent 的 system_prompt、model、nickname。"
            "當用戶說「重新載入 agent」、「套用我剚才的修改」時使用。"
            "**必須呼叫此工具才能實際套用，嚴禁只用文字假裝已重載。**"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

RELOAD_CONFIG_TOOL = {
    "type": "function",
    "function": {
        "name": "reload_config",
        "description": (
            "重新載入 .kai/config.json 並套用所有設定（history_keep、model、num_ctx 等）。"
            "當用戶說「重新載入設定」、「套用 config 變更」時使用。"
            "**必須呼叫此工具才能實際套用，嚴禁只用文字假裝已重載。**"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

CLOSE_AGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "close_agent",
        "description": "關閉 agent tab。當用戶說「關閉這個 agent」、「不用了」、「關掉它」時使用。**必須呼叫此工具才能實際關閉，嚴禁只用文字假裝已關閉。**省略 name 則關閉唯一開啟中的 agent tab。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "agent 名稱或暱稱，省略則關閉目前 agent tab",
                },
            },
            "required": [],
        },
    },
}

SEND_TO_AGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "send_to_agent",
        "description": (
            "傳送訊息給已開啟的 agent（非阻塞，立即返回）。"
            "對方會在自己的 tab 中自動處理。"
            "如需對方回報結果，在訊息中告知對方完成後用 send_to_agent 回傳。"
            "對方必須已用 load_agent 開啟。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "目標 agent 的名稱或昵稱"},
                "message": {"type": "string", "description": "訊息內容"},
            },
            "required": ["name", "message"],
        },
    },
}
