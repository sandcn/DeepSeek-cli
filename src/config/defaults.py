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
        # 最新一代（官方 Models & Pricing，2026-10）：
        #   deepseek-flash   — DeepSeek-V4.1 Flash（最新一代 Flash，原生多模态视觉）；
        #   deepseek-v4-pro  — DeepSeek-V4-Pro-0813（旗舰，1M 上下文 / 384K 输出）。
        # 旧名 deepseek-v4-flash / deepseek-v4-flash-vision-exp 已下线，官方仍接受
        # 请求并路由到 V4.1 Flash（按 Flash 价计费，含视觉能力）——保留以兼容旧配置。
        "models": ["deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"],
        "token_prices": {
            # 价格单位：美元 / 百万 tokens（高峰价；闲时（非 peak 时段）为高峰价一半）。
            # input_cache_hit 为缓存命中输入价格（DeepSeek 上下文缓存：命中部分
            # 按未命中价 ~1/50 计费，缺失时 /cost 回退按 input 全价计费，保守不低估）。
            # V4.1 Flash（deepseek-flash）：原生多模态，图片按 token 计费；
            # 旧名 deepseek-v4-flash / deepseek-v4-flash-vision-exp 的请求由
            # V4.1 Flash 承接，按相同单价计费。
            "deepseek-flash": {"input": 0.3, "output": 1.2, "input_cache_hit": 0.006},
            "deepseek-v4-pro": {"input": 1.32, "output": 3.96, "input_cache_hit": 0.044},
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
        # 最新一代（官方模型概览 / 定价，2026-10）：
        #   claude-opus-5-5   — 官方推荐默认（1M 上下文 / 128K 输出）；
        #   claude-sonnet-5-5 — 速度与智能最佳平衡；
        #   claude-haiku-5-5  — 高并发低成本；
        #   claude-fable-5-1  — 最强通用模型（长程 Agent / 深度推理，每 token 最贵）。
        # 上一代（legacy，仍可用）：claude-opus-5 / claude-sonnet-5 /
        # claude-haiku-4-5 / claude-opus-4-8 / claude-sonnet-4-6。
        "default_model": "claude-opus-5-5",
        "models": [
            "claude-opus-5-5",
            "claude-sonnet-5-5",
            "claude-haiku-5-5",
            "claude-fable-5-1",
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-haiku-4-5-20251001",
            "claude-opus-4-8",
            "claude-sonnet-4-6",
        ],
        "token_prices": {
            # 美元 / 百万 tokens（标准档）；input_cache_hit = 缓存读取单价
            # （缺失时 /cost 回退按 input 全价计费，保守不低估）。
            "claude-opus-5-5": {"input": 4.0, "output": 20.0, "input_cache_hit": 0.20},
            "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "input_cache_hit": 0.10},
            "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "input_cache_hit": 0.01},
            "claude-fable-5-1": {"input": 10.0, "output": 50.0, "input_cache_hit": 0.25},
            "claude-opus-5": {"input": 5.0, "output": 25.0, "input_cache_hit": 0.50},
            "claude-sonnet-5": {"input": 2.0, "output": 10.0, "input_cache_hit": 0.20},
            "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0, "input_cache_hit": 0.10},
            "claude-opus-4-8": {"input": 5.0, "output": 25.0},
            "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
        }
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        # 最新一代（智谱模型概览 / 定价，2026-10）：
        #   glm-5.3        — 旗舰（复杂软件工程 / Agent 任务；仅文本，1M 上下文）；
        #   glm-5.3-flash  — 首个原生多模态模型（图片 / 视频 / 文件 / 文本）；
        #   glm-5.3-flashx — GLM-5.3-Flash 加速版（推理速度 200 tokens/s）。
        # 上一代仍可用：glm-5.2 / glm-5.1 / glm-5 / glm-4.7。
        # 单价以人民币计价（元/百万 tokens），与美元口径的 provider 不同，
        # 故不并入 token_prices（/models 候选不显示单价）。
        "default_model": "glm-5.3",
        "models": [
            "glm-5.3",
            "glm-5.3-flash",
            "glm-5.3-flashx",
            "glm-5.2",
            "glm-5.1",
            "glm-5",
            "glm-4.7",
        ],
        "token_prices": {}
    },
    "mimo": {
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1/chat/completions",
        # 最新一代（Xiaomi MiMo 开放平台模型列表，2026-10）：
        #   mimo-v2.6-pro            — 旗舰（文本生成 / 全模态理解 / 深度思考）；
        #   mimo-v2.6-flash          — 专业办公高频调用与规模化任务；
        #   mimo-v2.6-pro-ultraspeed — 强实时交互（定制服务）。
        # 上一代 mimo-v2.5-pro / mimo-v2.5 将于 2026-10-21 下线，
        # 保留候选便于迁移（官方建议尽快切换到 V2.6 系列）。
        # 单价以人民币计价，故不并入美元口径的 token_prices。
        "default_model": "mimo-v2.6-pro",
        "models": [
            "mimo-v2.6-pro",
            "mimo-v2.6-flash",
            "mimo-v2.6-pro-ultraspeed",
            "mimo-v2.5-pro",
            "mimo-v2.5",
        ],
        "token_prices": {}
    }
}

#: 思考级别允许值域（模型档案 ``reasoning_effort`` 与全局默认共用）。
REASONING_EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "max")

#: 温度允许值域（模型档案 ``temperature`` 与全局默认共用）。
TEMPERATURE_RANGE: tuple[float, float] = (0.0, 2.0)

DEFAULTS = {
    # ── 模型档案（模型选择器：每个档案含 name/model/base_url/api_key/provider
    #    以及可选的 temperature/reasoning_effort——按模型覆盖生成参数）──
    # **LLM 访问的唯一入口**：模型列表、API 密钥、接口地址、提供商与当前生效
    # 模型全部来自模型档案（``model_profiles`` + ``active_model_profile``）；
    # 环境变量 CHAT_API_KEY/CHAT_MODEL/CHAT_BASE_URL/CHAT_LOW_MODEL 与 RC 旧
    # 键 api_key/base_url/model/provider/low_model 均已移除。内置 provider
    # 元数据（PROVIDERS）仅用于默认接口地址与 token 价格。
    "model_profiles": [],
    # 当前生效档案下标（-1 = 未选择；由模型选择器写入）
    "active_model_profile": -1,
    "reasoning_effort": "max",
    "temperature": 0.2,
    # 思考模式开关（Ctrl+T 切换；默认开启）。关闭时 DeepSeek V4 请求注入
    # thinking.type=disabled（官方默认 enabled），开启时注入 enabled+effort。
    "thinking_enabled": True,
    # ── 上下文窗口阈值（2026-10-07 用户需求：60k → 1M tokens 窗口）──
    # max_context_tokens：压缩触发的 token 口径上限（1M）；
    # max_context_chars：同一窗口的字符口径上限，取 1M tokens 对应的字符
    #   上界（ASCII ≈ 0.3 token/字符 → 1M tokens ≈ 3.33M 字符；取 3M 可
    #   覆盖纯 ASCII 会话约 90 万 tokens），避免字符阈值先于 token 阈值
    #   触发而把 1M 窗口提前截断；
    # auto_force_compress_threshold：**token 口径**（2026-10-09 用户需求：
    #   单位由字符改为 token）——当前上下文 tokens 超过该值即强制全量压缩
    #   （默认 600k tokens，与 max_context_tokens 同口径便于直接比较）。
    #   判定口径与 TUI 模式行 ``main · N%`` 使用率显示同源（真实基线优先：
    #   服务端 prompt_tokens + 新增消息估算；无基线时回退全量估算），确保
    #   「显示达到阈值」与「自动压缩触发」永不脱节。
    "max_context_chars": 3000000,
    # 单次输出最大字符数（默认 1M = 1000000 字符；可在 RC / /config 中覆盖）
    "max_output_chars": 1000000,
    "max_retries": 10,
    "retry_base_sec": 30.0,
    # ── 空响应（无正文/无推理/无工具调用）自动重发上限 ──
    # 空响应不是"调用失败"而是"没拿到回答"：请求健康、未渲染任何内容、重发
    # 幂等安全，故不占用 max_retries 瞬时错误预算，默认**无限**重试（0 =
    # 无限），直到拿到有效回答或用户 ESC 中断——避免以「模型返回空响应」错误
    # 结束本轮、让用户手动「继续」。有界调用方（SubAgent / 压缩摘要）不受此
    # 默认值影响，按各自预算快速失败。
    "empty_response_max_retries": 0,
    "max_session_messages": 0,
    "keep_recent_messages": 0,
    "max_context_tokens": 1000000,
    # 模型上下文窗口（tokens）——TUI 模式行行首上下文使用率百分比的分母。
    # 默认 1M（用户环境 deepseek 1M 上下文；不同模型可配置覆盖）。
    "model_context_tokens": 1000000,
    # 摘要生成预算（tokens，默认 100k = 100000）：build_summary_prompt 按该值
    # 折算单条消息保留字符数（budget_chars = budget * 1.5，每条下限 200
    # 字符），避免长会话压缩时因预算过小丢失关键信息。
    "summary_token_budget": 100000,
    "auto_force_compress_threshold": 600000,
    "enable_notifications": True,
    "notify_on_chat_completion": True,
    "token_prices": {},
    # 显式声明为多模态（视觉输入）的模型名列表（小写子串匹配，覆盖
    # src/core/multimodal.py 内置模式未覆盖的模型；用于**用户消息中的图片
    # 引用**内嵌为 image_url blocks。read_image 工具是否加载/可执行改由
    # 模型档案的 `multimodal`（/models「多模态读图」）开关决定）。
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
    # 括号粘贴（bracketed paste，用户需求 2026-10-07：TUI React Ink 改进）。
    # True（默认，对齐官方 React Ink v7）= TUI 启动时启用终端括号粘贴模式
    # （``CSI ?2004h``，退出还原）：粘贴内容以 ``ESC[200~ … ESC[201~`` 整段
    # 到达，不会被拆成逐字符按键（多行粘贴不再误触发 Enter 提交，拖放的
    # 文件路径仍走同一插入/规范化路径）；False = 不协商，回退「单次输入
    # 多字符」启发式识别粘贴。
    "tui_bracketed_paste": True,
    # 鼠标上报（SGR 1006：滚轮 / 点击 / 拖拽，用户需求 2026-10）。
    # True（默认）= TUI 启动时启用终端鼠标上报（``CSI ?1000h ?1002h ?1006h``，
    # 退出/挂起还原）：列表与视口支持滚轮滚动、列表支持点击选中；
    # 终端原生文本选择在多数终端需按住 Shift（xterm 约定）。
    # False = 不协商（终端原生选择优先，失去滚轮/点击交互）。
    "tui_mouse": True,
    # ── 上下文压缩（compaction，dsh 同款语义） ──
    # 设上下文窗口为 W、请求输出预留为 O、额外压力余量 B：
    #   触发阈值 thresholdTokens = floor(min(W * threshold_ratio, W - O - B))
    #   逐字保留尾部 retainTokens = floor((W - O) * retain_ratio)
    # 逐 provider/model 精确覆盖走 model_policies（每项形如
    # {"provider":..,"model":..,"threshold_ratio":..}）。
    # prune_* 为无模型的工具结果头/中/尾剪枝预算。
    "compaction": {
        "enabled": True,
        "auto": True,
        "threshold_ratio": 0.8,
        "headroom_tokens": 65536,
        # 逐字保留的近期对话：默认用 retain_ratio；显式改配 retain_tokens
        # 时二者互斥（不要再提供 retain_ratio）——见 core.compaction.config。
        "retain_ratio": 0.16,
        "max_tokens": 65536,
        "compaction_retries": 1,
        "max_overflow_retries": 1,
        "reserved_tokens": 0,
        "summarization_provider": "",
        "summarization_model": "",
        "model_policies": [],
        "prune_enabled": True,
        "prune_threshold_chars": 8192,
        "prune_head_chars": 4096,
        "prune_tail_chars": 1024,
    },
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
    # 模型列表（模型选择器：name/model/base_url/api_key/provider 结构由
    # config.model_profiles 维护；本键使其在 /config 界面可编辑。**LLM 访问
    # 的唯一入口**——环境变量 CHAT_API_KEY/CHAT_MODEL/CHAT_BASE_URL 与 RC
    # 旧键 api_key/base_url/model/provider/low_model 均已移除）
    "MODEL_PROFILES": {
        "rc_path": ("model_profiles",),
        "type": list,
        "default": [],
        "cacheable": True,
    },
    # 当前生效档案下标（-1 = 未选择；由模型选择器写入）
    "ACTIVE_MODEL_PROFILE": {
        "rc_path": ("active_model_profile",),
        "type": int,
        "default": -1,
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
    # 思考模式开关（Ctrl+T 切换；默认开启）。关闭时 DeepSeek V4 请求注入
    # thinking.type=disabled（官方默认 enabled），开启时注入 enabled+effort。
    # 官方文档「思考模式不支持 temperature」：开启时请求不携带 temperature。
    "THINKING_ENABLED": {
        "rc_path": ("thinking_enabled",),
        "type": bool,
        "default": True,
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
        # 引用 DEFAULTS 保持单一事实源（与 MAX_RETRIES 同一约定，
        # 避免 rc 键缺失兜底与 DEFAULTS 双源漂移）
        "default": DEFAULTS["max_context_chars"],
        "cacheable": True,
    },
    "MAX_OUTPUT_CHARS": {
        "rc_path": ("max_output_chars",),
        "type": int,
        # 引用 DEFAULTS 保持单一事实源（与 MAX_RETRIES 同一约定，
        # 避免 rc 键缺失兜底与 DEFAULTS 双源漂移）
        "default": DEFAULTS["max_output_chars"],
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
    # 空响应自动重发上限（0 = 无限；默认值见 DEFAULTS["empty_response_max_retries"]）
    "EMPTY_RESPONSE_MAX_RETRIES": {
        "rc_path": ("empty_response_max_retries",),
        "type": int,
        "default": DEFAULTS["empty_response_max_retries"],
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
        "default": DEFAULTS["max_context_tokens"],
        "cacheable": True,
    },
    "MODEL_CONTEXT_TOKENS": {
        "rc_path": ("model_context_tokens",),
        "type": int,
        "default": DEFAULTS["model_context_tokens"],
        "cacheable": True,
    },
    "SUMMARY_TOKEN_BUDGET": {
        "rc_path": ("summary_token_budget",),
        "type": int,
        "default": DEFAULTS["summary_token_budget"],
        "cacheable": True,
    },
    # token 口径（2026-10-09 用户需求：单位由字符改为 token，默认 600k）
    "AUTO_FORCE_COMPRESS_THRESHOLD": {
        "rc_path": ("auto_force_compress_threshold",),
        "type": int,
        "default": DEFAULTS["auto_force_compress_threshold"],
        "cacheable": True,
    },
    # ---- 上下文压缩（dsh 同款 compaction 策略） ----
    "COMPACTION": {
        "rc_path": ("compaction",),
        "type": dict,
        "default": DEFAULTS["compaction"],
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
    "TUI_BRACKETED_PASTE": {
        "rc_path": ("tui_bracketed_paste",),
        "type": bool,
        "default": True,
        "cacheable": True,
    },
    # 鼠标上报开关（滚轮/点击交互；默认开启）
    "TUI_MOUSE": {
        "rc_path": ("tui_mouse",),
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

# ============================================================
# CONFIG_BOUNDS — 数值配置的合法区间（用户输入校验真源）
# ------------------------------------------------------------
# 键 → ``(最小值, 最大值)``（任一端为 ``None`` 表示该侧无界）。
# 校验在 ``config.loader.update_config``（配置写入的唯一真源）执行——
# CLI ``chat.py config set``、TUI ``/config`` 编辑界面、配置导入等**所有**
# 写入路径共用同一道校验；越界值一律拒绝（``ValueError``），不再写入与
# 文档/运行语义不符的值（修复前 ``config set temperature 99``、
# ``config set max_retries -5`` 均被接受并落盘）。
#
# 键名可用大写写回键（``TEMPERATURE``）或 rc_path 末段（``temperature``）；
# 仅对 int/float 值生效（bool 不计入），无边界的键不受影响。
# ============================================================
CONFIG_BOUNDS = {
    # 当前生效档案下标（-1 = 未选择）
    "ACTIVE_MODEL_PROFILE": (-1, None),
    # 全局默认温度（0.0~2.0；/models 中可按模型档案覆盖）
    "TEMPERATURE": (0.0, 2.0),
    "MAX_CONTEXT_CHARS": (1, None),
    "MAX_OUTPUT_CHARS": (1, None),
    # API 调用最大重试次数（负数无语义）
    "MAX_RETRIES": (0, 100),
    # 重试基础间隔（秒）
    "RETRY_BASE_SEC": (0.0, None),
    # 空响应自动重发上限（0 = 无限）
    "EMPTY_RESPONSE_MAX_RETRIES": (0, None),
    # 会话消息数上限 / 压缩保留数（0 = 无限制）
    "MAX_SESSION_MESSAGES": (0, None),
    "KEEP_RECENT_MESSAGES": (0, None),
    "MAX_CONTEXT_TOKENS": (0, None),
    # 模型上下文窗口（使用率分母，0 无意义）
    "MODEL_CONTEXT_TOKENS": (1, None),
    "SUMMARY_TOKEN_BUDGET": (0, None),
    "AUTO_FORCE_COMPRESS_THRESHOLD": (0, None),
    # 上传前图片优化
    "IMAGE_UPLOAD_KEEP_RECENT": (0, None),
    "IMAGE_UPLOAD_MAX_DIMENSION": (1, None),
    # JPEG 质量 1~100
    "IMAGE_UPLOAD_QUALITY": (1, 100),
    # 多模态图片视觉 token 估算
    "MULTIMODAL_IMAGE_TOKEN_PATCH": (1, None),
    "MULTIMODAL_IMAGE_TOKEN_DEFAULT": (0, None),
    # HTTP 客户端（超时/连接数 > 0；keep-alive 允许 0 = 不复用）
    "HTTP_CONNECT_TIMEOUT": (1, None),
    "HTTP_READ_TIMEOUT": (1, None),
    "HTTP_WRITE_TIMEOUT": (1, None),
    "HTTP_MAX_CONNECTIONS": (1, None),
    "HTTP_MAX_CONNECTIONS_PER_HOST": (1, None),
    "HTTP_KEEP_ALIVE_TIMEOUT": (0, None),
}

