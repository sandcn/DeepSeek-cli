"""配置包 — 常量定义

所有纯常量，不触发任何文件 IO。
"""

from pathlib import Path

CONFIG_DIR  = Path.home() / ".chat_config"
LOG_FILE    = CONFIG_DIR / "audit.log"
RC_FILE     = CONFIG_DIR / "chatrc.json"
INPUT_HISTORY_FILE = CONFIG_DIR / "input_history"

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
    "theme": "dark",
}

# ============================================================
# CONFIG_KEYS — 配置键统一元数据（单一定义源）
# ------------------------------------------------------------
# 新增配置键只需在此处添加一个条目，无需同步修改 schema.py 的
# int_fields/float_fields/bool_fields 和 __init__.py 的
# _RC_KEY_MAP——两者均从此元数据自动派生。
#
# 每条目字段：
#   rc_path  : RC JSON 文件中的嵌套路径（空元组=特殊键）
#   type     : Python 类型（用于 _validate_rc 类型校验）
#   default  : 默认值
#   cacheable: 是否可在 _value_cache 中缓存
# ============================================================
CONFIG_KEYS = {
    # ---- 核心配置 ----
    "MODEL": {
        "rc_path": ("model",),
        "type": str,
        "default": "deepseek-v4-flash",
        "cacheable": True,
    },
    "MODELS": {
        "rc_path": ("models",),
        "type": list,
        "default": [],
        "cacheable": True,
    },
    # ---- 数值配置 ----
    "MAX_CONTEXT_CHARS": {
        "rc_path": ("max_context_chars",),
        "type": int,
        "default": 60000,
        "cacheable": True,
    },
    "MAX_OUTPUT_CHARS": {
        "rc_path": ("max_output_chars",),
        "type": int,
        "default": 3000,
        "cacheable": True,
    },
    "MAX_RETRIES": {
        "rc_path": ("max_retries",),
        "type": int,
        "default": 3,
        "cacheable": True,
    },
    "RETRY_BASE_SEC": {
        "rc_path": ("retry_base_sec",),
        "type": float,
        "default": 1,
        "cacheable": True,
    },
    "MAX_SESSION_MESSAGES": {
        "rc_path": ("max_session_messages",),
        "type": int,
        "default": 0,
        "cacheable": True,
    },
    "KEEP_RECENT_MESSAGES": {
        "rc_path": ("keep_recent_messages",),
        "type": int,
        "default": 0,
        "cacheable": True,
    },
    "MAX_CONTEXT_TOKENS": {
        "rc_path": ("max_context_tokens",),
        "type": int,
        "default": 60000,
        "cacheable": True,
    },
    "SUMMARY_TOKEN_BUDGET": {
        "rc_path": ("summary_token_budget",),
        "type": int,
        "default": 2000,
        "cacheable": True,
    },
    "AUTO_FORCE_COMPRESS_THRESHOLD": {
        "rc_path": ("auto_force_compress_threshold",),
        "type": int,
        "default": 60000,
        "cacheable": True,
    },
    # ---- 布尔配置 ----
    "ENABLE_NOTIFICATIONS": {
        "rc_path": ("enable_notifications",),
        "type": bool,
        "default": True,
        "cacheable": True,
    },
    "NOTIFY_ON_CHAT_COMPLETION": {
        "rc_path": ("notify_on_chat_completion",),
        "type": bool,
        "default": True,
        "cacheable": True,
    },
    # ---- 复合配置 ----
    "TOKEN_PRICES": {
        "rc_path": ("token_prices",),
        "type": dict,
        "default": {},
        "cacheable": True,
    },
    # ---- HTTP 性能配置（嵌套路径） ----
    "HTTP_CONNECT_TIMEOUT": {
        "rc_path": ("performance", "http_client", "connect_timeout"),
        "type": int,
        "default": 30,
        "cacheable": True,
    },
    "HTTP_READ_TIMEOUT": {
        "rc_path": ("performance", "http_client", "read_timeout"),
        "type": int,
        "default": 120,
        "cacheable": True,
    },
    "HTTP_WRITE_TIMEOUT": {
        "rc_path": ("performance", "http_client", "write_timeout"),
        "type": int,
        "default": 120,
        "cacheable": True,
    },
    "HTTP_MAX_CONNECTIONS": {
        "rc_path": ("performance", "http_client", "max_connections"),
        "type": int,
        "default": 100,
        "cacheable": True,
    },
    "HTTP_MAX_CONNECTIONS_PER_HOST": {
        "rc_path": ("performance", "http_client", "max_connections_per_host"),
        "type": int,
        "default": 20,
        "cacheable": True,
    },
    "HTTP_KEEP_ALIVE_TIMEOUT": {
        "rc_path": ("performance", "http_client", "keep_alive_timeout"),
        "type": int,
        "default": 15,
        "cacheable": True,
    },
    "HTTP_ENABLE_POOL": {
        "rc_path": ("performance", "http_client", "enable_pool"),
        "type": bool,
        "default": True,
        "cacheable": True,
    },
    "HTTP_ENABLE_HTTP2": {
        "rc_path": ("performance", "http_client", "enable_http2"),
        "type": bool,
        "default": True,
        "cacheable": True,
    },
}
