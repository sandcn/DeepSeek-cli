"""配置包 — 常量定义

所有纯常量，不触发任何文件 IO。
"""

from pathlib import Path

CONFIG_DIR  = Path.home() / ".chat_config"
LOG_FILE    = CONFIG_DIR / "audit.log"
RC_FILE     = CONFIG_DIR / "chatrc.json"
INPUT_HISTORY_FILE = CONFIG_DIR / "input_history"
OUTPUT_HISTORY_FILE = CONFIG_DIR / "output_history"

PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-v4-pro",
        # deepseek-flash 为 DeepSeek V4.1 Flash（最新一代，原生多模态视觉）；
        # deepseek-v4-flash / deepseek-v4-flash-vision-exp 为上一代旧名，
        # 官方已下线并把请求路由到 V4.1 Flash（保留以兼容旧配置）。
        "models": ["deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"],
        "token_prices": {
            # 价格单位：美元 / 百万 tokens（高峰价；闲时（非 peak 时段）为高峰价一半）。
            # input_cache_hit 为缓存命中输入价格（DeepSeek 上下文缓存：命中部分
            # 按未命中价 ~1/50 计费，缺失时 /cost 回退按 input 全价计费，保守不低估）。
            # V4.1 Flash（deepseek-flash）：原生多模态，图片按 token 计费；
            # 旧名 deepseek-v4-flash / deepseek-v4-flash-vision-exp 的请求由
            # V4.1 Flash 承接，按相同单价计费。
            "deepseek-flash": {"input": 0.3, "output": 1.2, "input_cache_hit": 0.006},
            "deepseek-v4-pro": {"input": 0.55, "output": 2.19, "input_cache_hit": 0.07},
            "deepseek-v4-flash": {"input": 0.3, "output": 1.2, "input_cache_hit": 0.006},
            "deepseek-v4-flash-vision-exp": {"input": 0.3, "output": 1.2, "input_cache_hit": 0.006},
        }
    },
    "custom": {
        "base_url": "",
        "default_model": "",
        "models": [],
        "token_prices": {}
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-4-6",
        "models": [
            "claude-sonnet-4-6",
            "claude-opus-4-6",
            "claude-haiku-4-5-20251001",
        ],
        "token_prices": {
            "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
            "claude-opus-4-6": {"input": 15.0, "output": 75.0},
            "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
        }
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "default_model": "glm-5.2",
        "models": [
            "glm-5.2",
        ],
        "token_prices": {}
    },
    "mimo": {
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1/chat/completions",
        "default_model": "mimo-v2.5",
        "models": [
            "mimo-v2.5",
        ],
        "token_prices": {}
    }
}

DEFAULTS = {
    "provider": "deepseek",
    "base_url": "",
    "api_key": "",
    # 全局默认模型（用户未显式选择 provider/模型时使用快速版）；
    # PROVIDERS[provider]["default_model"] 是**显式切换**到该 provider 时的
    # 模型（deepseek=pro 旗舰）——两者用途不同，非笔误。
    "model": "deepseek-v4-flash",
    "reasoning_effort": "max",
    "temperature": 0.2,
    "max_context_chars": 60000,
    "max_output_chars": 3000,
    "max_retries": 10,
    "retry_base_sec": 30.0,
    "max_session_messages": 0,
    "keep_recent_messages": 0,
    "max_context_tokens": 60000,
    # 模型上下文窗口（tokens）——TUI 模式行行首上下文使用率百分比的分母。
    # 默认 1M（用户环境 deepseek 1M 上下文；不同模型可配置覆盖）。
    "model_context_tokens": 1000000,
    "summary_token_budget": 2000,
    "auto_force_compress_threshold": 60000,
    "enable_notifications": True,
    "notify_on_chat_completion": True,
    "models": [],
    "token_prices": {},
    # 显式声明为多模态（视觉输入）的模型名列表（小写子串匹配，覆盖
    # src/api/multimodal.py 内置模式未覆盖的模型；read_image 等图像工具
    # 据此判断是否返回 base64 图片 content blocks）
    "multimodal_models": [],
    # ── 上传前图片优化（发送副本上执行，不改 read_image 输出/会话存档）──
    # 多图场景下每轮请求都会重传全部历史图片 base64，体积线性膨胀导致
    # 请求卡顿；以下配置控制：折叠旧图 + 压缩大图 + 编码缓存。
    "image_upload_optimize": True,
    "image_upload_keep_recent": 4,
    "image_upload_max_dimension": 1568,
    "image_upload_quality": 80,
    # ── 多模态图片视觉 token 估算（上下文占用百分比 / 压缩判断口径）──
    # 图片视觉 token = ceil(w/patch) * ceil(h/patch)（长边先按
    # image_upload_max_dimension 降采样，仅计 image_upload_keep_recent 张）；
    # 无法读尺寸（http URL / 解码失败 / Pillow 缺失）时每图按 default 计。
    # 计费（/cost 输入）来自 API 真实 usage，与本估算无关。
    "multimodal_image_token_patch": 28,
    "multimodal_image_token_default": 800,
    # ── MCP（Model Context Protocol）外部工具服务器 ──
    # 列表元素：{"name","transport"(stdio/http/sse),"command","args","env","cwd",
    #   "url","headers","enabled","agents","timeout","parallel_safe","description"}
    # 空列表 → 不连接任何外部 server（零开销）；应用启动时 setup_mcp() 读取。
    "mcp_servers": [],
    "theme": "dark",
    # TUI 拖放文件路径规范化（用户需求 2026-10-07：输入框支持拖动文件输入
    # 文件路径）。True（默认）= 拖动文件到输入框时把终端注入的路径文本
    # 规范化（去引号/还原反斜杠转义/file URI 解析/Windows→POSIX，多文件每行
    # 一个，含空格路径双引号包裹）；False = 粘贴文本一律原样插入。
    "tui_drop_path_normalize": True,
    # 技能（skill）子系统配置
    "skills": {
        "enabled": True,
        "catalog_description_max_length": 500,
        "auto_load": [],
    },
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
    "REASONING_EFFORT": {
        "rc_path": ("reasoning_effort",),
        "type": str,
        "default": "max",
        "cacheable": True,
    },
    "TEMPERATURE": {
        "rc_path": ("temperature",),
        "type": float,
        "default": 0.2,
        "cacheable": True,
    },
    "THEME": {
        "rc_path": ("theme",),
        "type": str,
        "default": "dark",
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
        # 引用 DEFAULTS 保持单一事实源（_safe_merge 以 DEFAULTS 为 base，
        # 此处 default 仅作 rc 键缺失时的兜底，双源漂移会导致两种路径
        # 返回不同默认值——见 review P1）
        "default": DEFAULTS["max_retries"],
        "cacheable": True,
    },
    "RETRY_BASE_SEC": {
        "rc_path": ("retry_base_sec",),
        "type": float,
        "default": DEFAULTS["retry_base_sec"],
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
    "MODEL_CONTEXT_TOKENS": {
        "rc_path": ("model_context_tokens",),
        "type": int,
        "default": 1000000,
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
    "MULTIMODAL_MODELS": {
        "rc_path": ("multimodal_models",),
        "type": list,
        "default": [],
        "cacheable": True,
    },
    # ---- 上传前图片优化（多模态图片瘦身） ----
    "IMAGE_UPLOAD_OPTIMIZE": {
        "rc_path": ("image_upload_optimize",),
        "type": bool,
        "default": DEFAULTS["image_upload_optimize"],
        "cacheable": True,
    },
    "IMAGE_UPLOAD_KEEP_RECENT": {
        "rc_path": ("image_upload_keep_recent",),
        "type": int,
        "default": DEFAULTS["image_upload_keep_recent"],
        "cacheable": True,
    },
    "IMAGE_UPLOAD_MAX_DIMENSION": {
        "rc_path": ("image_upload_max_dimension",),
        "type": int,
        "default": DEFAULTS["image_upload_max_dimension"],
        "cacheable": True,
    },
    "IMAGE_UPLOAD_QUALITY": {
        "rc_path": ("image_upload_quality",),
        "type": int,
        "default": DEFAULTS["image_upload_quality"],
        "cacheable": True,
    },
    # ---- 多模态图片视觉 token 估算（上下文统计口径） ----
    "MULTIMODAL_IMAGE_TOKEN_PATCH": {
        "rc_path": ("multimodal_image_token_patch",),
        "type": int,
        "default": DEFAULTS["multimodal_image_token_patch"],
        "cacheable": True,
    },
    "MULTIMODAL_IMAGE_TOKEN_DEFAULT": {
        "rc_path": ("multimodal_image_token_default",),
        "type": int,
        "default": DEFAULTS["multimodal_image_token_default"],
        "cacheable": True,
    },
    # ---- MCP 外部工具服务器 ----
    "MCP_SERVERS": {
        "rc_path": ("mcp_servers",),
        "type": list,
        "default": [],
        "cacheable": True,
    },
    # ---- TUI 交互行为 ----
    "TUI_DROP_PATH_NORMALIZE": {
        "rc_path": ("tui_drop_path_normalize",),
        "type": bool,
        "default": True,
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
