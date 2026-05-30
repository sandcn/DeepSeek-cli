"""配置包 — 常量定义

所有纯常量，不触发任何文件 IO。
"""

from pathlib import Path

CONFIG_DIR  = Path.home() / ".chat_config"
LOG_FILE    = CONFIG_DIR / "audit.log"
RC_FILE     = CONFIG_DIR / "chatrc.json"
INPUT_HISTORY_FILE = CONFIG_DIR / "input_history"
INPUT_DRAFT_FILE = CONFIG_DIR / "input_draft"

PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-v4-pro",
        "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
        "token_prices": {
            "deepseek-v4-pro": {"input": 0.55, "output": 2.19},
            "deepseek-v4-flash": {"input": 0.55, "output": 2.19},
        }
    },
    "custom": {
        "base_url": "",
        "default_model": "",
        "models": [],
        "token_prices": {}
    }
}

DEFAULTS = {
    "provider": "deepseek",
    "base_url": "",
    "api_key": "",
    "model": "deepseek-v4-flash",
    "max_context_chars": 60000,
    "max_output_chars": 3000,
    "max_retries": 3,
    "retry_base_sec": 1,
    "max_session_messages": 0,
    "keep_recent_messages": 0,
    "max_context_tokens": 60000,
    "summary_token_budget": 2000,
    "auto_force_compress_threshold": 60000,
    "enable_notifications": True,
    "notify_on_chat_completion": True,
    "models": [],
    "token_prices": {},
    "tool_output_truncate": 500,
    "theme": "dark",
}
