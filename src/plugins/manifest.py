"""内置插件清单 — Bundle / Profile / Patch 定义。

对应 DeepSeek Harness 的 Profile/Bundle/Patch 三层组装：
- **Bundle** 描述一组插件条目；
- **Profile** 选择 bundle 并叠加 Patch；
- **Patch** 按插件 id（``<bundle>::<id>``）覆盖配置或禁用。

「一切皆插件」：每个内置工具、命令、渲染 handler/filter、Agent 中间件都是
清单中的**独立插件条目**——可被 Profile/Bundle 声明，也可被 Patch/Overlay
按 id 覆盖配置、禁用或替换，而非隐藏在某处的一次性自动注册副作用里。

内置 profile：

- ``cli``      完整终端应用（内核 + 工具 + 技能 + MCP + 模型 + 会话 +
                Agent 循环 + 命令 + 事件 + UI/渲染器 + 策略）；
- ``headless`` 无 UI 的后端运行时；
- ``minimal``  最小内核（配置 + 事件 + 工具 + 模型）；
- ``full``     等同 cli（语义化别名，便于扩展）。
"""

from __future__ import annotations

from ..core.cordis_tools import CORDIS_TOOLS
from ..kernel.config_tree import ConfigTree
from ..kernel.invariant_registry import builtin_invariant_ids
from ..prompt_builder.sections import builtin_section_ids

# ── 运行时不变量条目（每条检查一个独立插件条目，可被 patch/overlay 禁用/替换） ──
#
# 条目经 ``src.plugins.invariant_entries`` 把 ``config.name`` 指向的内置检查
# 注册进 ``src.kernel.invariant_registry``；``config.name`` 供组合根收集
# 「清单已接管的检查」（注入 invariants 聚合插件，抑制默认装配，使 overlay
# 禁用单项真正生效）。

INVARIANT_ENTRIES = [
    {"id": f"invariant_{name.replace('.', '_')}",
     "plugin": "src.plugins.invariant_entries:apply_invariant",
     "config": {"name": name}}
    for name in builtin_invariant_ids()
]

# ── 内置工具条目（每个工具一个独立插件条目，可被 patch/overlay 禁用/替换） ──
#
# 条目经 ``src.plugins.tool_plugin`` 导入 ``config.tool`` 指向的 Func 子类并
# 注册进 ``ctx.tools``；``config.name`` 供组合根收集“已由清单接管的工具名”
# （注入 ``tools_builtin``，避免被兜底自动发现重新注册，使 overlay disable
# 单个工具真正生效）。

TOOL_PLUGIN_ENTRIES = [
    {"id": "tool_bash", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.bash.BashFunc", "name": "bash"}},
    {"id": "tool_bash_opt", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.bash_opt.BashOptFunc", "name": "bash_opt"}},
    {"id": "tool_cp", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.cp.CpFunc", "name": "cp"}},
    {"id": "tool_find", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.find.FindFunc", "name": "find"}},
    {"id": "tool_ls", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.ls.LsFunc", "name": "ls"}},
    {"id": "tool_mkdir", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.mkdir.MkdirFunc", "name": "mkdir"}},
    {"id": "tool_mv", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.mv.MvFunc", "name": "mv"}},
    {"id": "tool_read_file", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.read_file.ReadFileFunc", "name": "read_file"}},
    {"id": "tool_read_image", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.read_image.ReadImageFunc", "name": "read_image"}},
    {"id": "tool_rm", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.rm.RmFunc", "name": "rm"}},
    {"id": "tool_search", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.search.SearchFunc", "name": "search"}},
    {"id": "tool_skill", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.skill_tool.SkillFunc", "name": "skill"}},
    {"id": "tool_subagent", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.subagent.SubagentFunc", "name": "subagent"}},
    {"id": "tool_subagent_opt", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.subagent_opt.SubagentOptFunc", "name": "subagent_opt"}},
    {"id": "tool_update_file", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.update_file.UpdateFileFunc", "name": "update_file"}},
    {"id": "tool_user_select", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.user_select.UserSelectFunc", "name": "user_select"}},
    {"id": "tool_web_fetch", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.web_fetch.WebFetchFunc", "name": "web_fetch"}},
    {"id": "tool_web_search", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.web_search.WebSearchFunc", "name": "web_search"}},
    {"id": "tool_write_file", "plugin": "src.plugins.tool_plugin",
     "config": {"tool": "src.tools.write_file.WriteFileFunc", "name": "write_file"}},
]

# ── 工具元数据条目（每个内置工具一条独立元数据条目） ────────
#
# 条目经 ``src.plugins.tool_metadata_entries`` 把 ``config.name`` 指向的内置
# 工具元数据注册进 ``src.tools.metadata_registry``；``config.name`` 供组合根
# 收集「清单已接管的元数据」（注入 tool_metadata 聚合插件，抑制默认装配，使
# overlay 禁用单项真正生效）。

_TOOL_METADATA_NAMES = (
    "bash", "bash_opt", "cp", "find", "ls", "mkdir", "mv", "read_file",
    "read_image", "rm", "search", "skill", "subagent", "subagent_opt",
    "update_file", "user_select", "web_fetch", "web_search", "write_file",
)

TOOL_METADATA_ENTRIES = [
    {"id": f"tool_metadata_{name}",
     "plugin": "src.plugins.tool_metadata_entries:apply_tool_metadata",
     "config": {"name": name}}
    for name in _TOOL_METADATA_NAMES
]

# ── 工具常量条目（每个内置常量一条独立条目） ────────────────
#
# 条目经 ``src.plugins.tool_const_entries`` 把 ``config.name`` 指向的内置常量
# 注册进 ``src.tools.const_registry``；``config.name`` 供组合根收集「清单已
# 接管的常量」（注入 tool_consts 聚合插件，抑制默认装配，使 overlay 禁用单项
# 真正生效）。

_TOOL_CONST_NAMES = (
    "EXCLUDED_DIRS", "EXCLUDED_FILE_PATTERNS", "DANGEROUS_DEVICE_FILES",
    "SYSTEM_CRITICAL_PATHS", "DOS_DEVICE_NAMES", "WIN_DEVICE_PREFIXES",
    "DEFAULT_ENCODING", "DEFAULT_ERRORS", "MAX_FILE_SIZE_MB",
    "CATCHALL_ENCODINGS", "MAX_DETECT_BYTES", "COMMON_ENCODINGS",
    "FALLBACK_ENCODINGS", "BOM_MARKERS", "ENCODING_ALIASES",
    "LARGE_FILE_THRESHOLD",
    "IMAGE_EXT_FORMAT", "IMAGE_FORMAT_MEDIA", "IMAGE_EXTENSIONS",
    "REMOVE_TAGS", "REMOVE_CLASS_KEYWORDS", "PRIVATE_PREFIXES",
    "DATE_META_PATTERNS",
    "DATE_PATTERNS", "STRPTIME_RE_PART", "CONTENT_SELECTORS",
)

TOOL_CONST_ENTRIES = [
    {"id": f"tool_const_{name.lower()}",
     "plugin": "src.plugins.tool_const_entries:apply_tool_const",
     "config": {"name": name}}
    for name in _TOOL_CONST_NAMES
]

# ── 事件类型条目（每个内置事件类型一条独立条目） ────────────
#
# 条目经 ``src.plugins.event_type_entries`` 把 ``config.name``（复合 id
# ``<domain>::<name>``）指向的内置事件类型注册进
# ``src.core.events.type_registry``；``config.name`` 供组合根收集「清单已接管
# 的事件类型」（注入 event_types 聚合插件，抑制默认装配，使 overlay 禁用单项
# 真正生效）。

_EVENT_TYPE_CORE_NAMES = (
    "MODEL_CALL_STARTED", "MODEL_CALL_COMPLETED", "MODEL_CALL_FAILED",
    "MODEL_STREAM_CHUNK",
    "TOOL_CALL_STARTED", "TOOL_CALL_COMPLETED", "TOOL_CALL_FAILED",
    "SESSION_STARTED", "SESSION_COMPLETED", "SESSION_INTERRUPTED", "SESSION_SAVED",
    "CONTEXT_COMPRESSED", "CONTEXT_COMPRESS_FAILED",
    "CONFIG_CHANGED",
    "APP_BOOTSTRAP", "APP_SHUTDOWN",
)

_EVENT_TYPE_DISPLAY_NAMES = (
    "SessionStarted", "SessionStopped",
    "ToolParsingEvent", "ToolStartedEvent", "ToolDoneEvent", "ToolOutputChunkEvent",
    "ToolBatchStartedEvent", "ToolNoticeEvent",
    "AgentAddedEvent", "AgentStatusChanged",
    "ModelPhaseEvent", "PhaseDoneEvent", "UsageUpdatedEvent",
    "ContentChunkEvent", "ReasoningChunkEvent",
    "ParseInfoEvent", "ParseInfoDoneEvent", "MetricsUpdateEvent",
    "OutputEvent", "ToolSummaryEvent",
    "SubagentPromptEvent",
    "AgentResultEvent",
    "BackgroundTaskChangedEvent",
)

_EVENT_TYPE_SESSION_NAMES = (
    "TURN_START", "TURN_END", "STEP_START", "STEP_END",
    "SYSTEM_MESSAGE", "USER_MESSAGE", "ASSISTANT_MESSAGE", "ASSISTANT_ATTEMPT",
    "TOOL_RESULT", "REQUEST_HEADER", "REQUEST_CONTEXT",
    "INSERT", "REPLACE", "DELETE", "TRUNCATE", "RESET",
    "EMIT",
)

_EVENT_TYPE_AGENT_NAMES = (
    "CREATED", "DESTROYED", "INBOX", "PRE_STEP", "STEP_START", "STEP_END",
    "REQUEST", "ASSISTANT_STREAM", "TURN_STOPPING", "STATUS", "VALIDATION",
    "CONTINUATION",
)

_EVENT_TYPE_CAPABILITY_NAMES = (
    "TOOLS_PRE_EXECUTE", "TOOLS_EXECUTE", "TOOLS_POST_EXECUTE",
    "FS_READ", "FS_WRITE", "FS_REMOVE", "FS_MOVE", "FS_LIST",
    "SHELL_SPAWN", "SUBPROCESS_SPAWN",
    "TERMINALS_OPEN", "TERMINALS_CLOSE", "JOBS_START", "JOBS_STOP",
    "SANDBOX_CHECK", "APPROVAL_REQUEST",
    "TELEMETRY_EVENT",
)

EVENT_TYPE_ENTRIES = [
    {"id": f"event_type_core_{name.lower()}",
     "plugin": "src.plugins.event_type_entries:apply_event_type",
     "config": {"name": f"core::{name}"}}
    for name in _EVENT_TYPE_CORE_NAMES
] + [
    {"id": f"event_type_display_{name.lower()}",
     "plugin": "src.plugins.event_type_entries:apply_event_type",
     "config": {"name": f"display::{name}"}}
    for name in _EVENT_TYPE_DISPLAY_NAMES
] + [
    {"id": f"event_type_{domain}_{name.lower()}",
     "plugin": "src.plugins.event_type_entries:apply_event_type",
     "config": {"name": f"{domain}::{name}"}}
    for domain, names in (
        ("session", _EVENT_TYPE_SESSION_NAMES),
        ("agent", _EVENT_TYPE_AGENT_NAMES),
        ("capability", _EVENT_TYPE_CAPABILITY_NAMES),
    )
    for name in names
]

# ── 命名样式条目（每个内置命名样式一条独立条目） ────────────
#
# 条目经 ``src.plugins.style_entries`` 把 ``config.name`` 指向的内置命名样式
# 注册进 ``src.tui.core.style`` 的命名样式注册表；``config.name`` 供组合根收集
# 「清单已接管的样式」（注入 named_styles 聚合插件，抑制默认装配，使 overlay
# 禁用单项真正生效）。

_NAMED_STYLE_NAMES = (
    "dim", "bold", "italic", "underline", "bold_dim", "dim_italic", "bold_italic",
    "error", "success", "warn", "info", "muted", "border_breath",
    "diff_add", "diff_del", "diff_ctx",
    "user_icon", "asst_icon", "tool_icon", "tool_txt",
    "separator", "highlight", "accent", "deco", "neon",
    "tree_branch", "tree_leaf",
)

NAMED_STYLE_ENTRIES = [
    {"id": f"named_style_{name}",
     "plugin": "src.plugins.style_entries:apply_named_style",
     "config": {"name": name}}
    for name in _NAMED_STYLE_NAMES
]

# ── 全局禁用工具条目（每个禁用项一个独立插件条目） ──────────
#
# 条目经 ``src.plugins.tool_policy_entries`` 把 ``config.name`` 指向的内置项注册
# 进 ``src.tools.tool_policy`` 的全局禁用注册表；``config.name`` 供组合根收集
# 「清单已接管的禁用项」（注入 policy，抑制默认装配，使 overlay 禁用单项真正生效
# ——被禁用的条目不再注册，对应工具随之解除全局禁用）。

GLOBAL_DISABLED_TOOL_ENTRIES = [
    {"id": f"global_disabled_tool_{name}",
     "plugin": "src.plugins.tool_policy_entries:apply_global_disabled_tool",
     "config": {"name": name}}
    for name in CORDIS_TOOLS
]

# ── 内置命令条目（每个命令一个独立插件条目，可被 patch/overlay 禁用/替换） ──
#
# 条目经 ``src.plugins.command_plugin`` 导入 ``config.command`` 指向的
# CommandPlugin 子类并注册进 ``ctx.commands``；``config.name`` 供组合根收集
# “已由清单接管的命令名”（注入 commands 服务，使 overlay disable 单个命令生效）。

COMMAND_PLUGIN_ENTRIES = [
    {"id": "cmd_changes", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._session_cmd.ChangesCommand", "name": "changes"}},
    {"id": "cmd_clear", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._session_cmd.ClearCommand", "name": "clear"}},
    {"id": "cmd_config", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._config_cmd.ConfigCommand", "name": "config"}},
    {"id": "cmd_cost", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._config_cmd.CostCommand", "name": "cost"}},
    {"id": "cmd_edit", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._session_cmd.EditCommand", "name": "edit"}},
    {"id": "cmd_export", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._export_cmd.ExportCommand", "name": "export"}},
    {"id": "cmd_help", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._data_cmd.HelpCommand", "name": "help"}},
    {"id": "cmd_load", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._data_cmd.LoadCommand", "name": "load"}},
    {"id": "cmd_pin", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._session_cmd.PinCommand", "name": "pin"}},
    {"id": "cmd_plugin", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._plugin_cmd.PluginCommand", "name": "plugin"}},
    {"id": "cmd_reasoning", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._config_cmd.ReasoningCommand", "name": "reasoning"}},
    {"id": "cmd_retry", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._session_cmd.RetryCommand", "name": "retry"}},
    {"id": "cmd_sessions", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._data_cmd.SessionsCommand", "name": "sessions"}},
    {"id": "cmd_temperature", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._config_cmd.TemperatureCommand", "name": "temperature"}},
    {"id": "cmd_theme", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._config_cmd.ThemeCommand", "name": "theme"}},
    {"id": "cmd_undo", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._session_cmd.UndoCommand", "name": "undo"}},
    {"id": "cmd_deitmsg", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands.plugins.deitmsg_plugin.DeitmsgPlugin", "name": "deitmsg"}},
    {"id": "cmd_editmsg", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands.plugins.editmsg_plugin.EditmsgPlugin", "name": "editmsg"}},
    {"id": "cmd_loop", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands.plugins.loop_plugin.LoopPlugin", "name": "loop"}},
    {"id": "cmd_model", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands.plugins.model_plugin.ModelPlugin", "name": "model"}},
    {"id": "cmd_skill", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands.plugins.skill_plugin.SkillPlugin", "name": "skill"}},
]

# ── 渲染扩展条目（每个内置 handler / filter 一个独立插件条目） ──
#
# 条目经 ``src.plugins.renderer_entries`` 把 ``config.id`` 指向的内置项注册进
# ``src.renderer.extensions``；``config.id`` 供组合根收集「清单已接管的渲染项」
# （注入 ``renderer_builtin``，抑制默认装配，使 overlay disable 单个项真正生效）。

RENDERER_HANDLER_ENTRIES = [
    {"id": "renderer_handler_inline", "plugin": "src.plugins.renderer_entries:apply_handler",
     "config": {"id": "inline"}},
    {"id": "renderer_handler_code", "plugin": "src.plugins.renderer_entries:apply_handler",
     "config": {"id": "code"}},
    {"id": "renderer_handler_math", "plugin": "src.plugins.renderer_entries:apply_handler",
     "config": {"id": "math"}},
    {"id": "renderer_handler_mermaid", "plugin": "src.plugins.renderer_entries:apply_handler",
     "config": {"id": "mermaid"}},
    {"id": "renderer_handler_details", "plugin": "src.plugins.renderer_entries:apply_handler",
     "config": {"id": "details"}},
    {"id": "renderer_handler_admonition", "plugin": "src.plugins.renderer_entries:apply_handler",
     "config": {"id": "admonition"}},
    {"id": "renderer_handler_html_block", "plugin": "src.plugins.renderer_entries:apply_handler",
     "config": {"id": "html_block"}},
    {"id": "renderer_handler_table", "plugin": "src.plugins.renderer_entries:apply_handler",
     "config": {"id": "table"}},
    {"id": "renderer_handler_fenced_div", "plugin": "src.plugins.renderer_entries:apply_handler",
     "config": {"id": "fenced_div"}},
]

RENDERER_FILTER_ENTRIES = [
    {"id": "renderer_filter_code_block_batcher", "plugin": "src.plugins.renderer_entries:apply_filter",
     "config": {"id": "code_block_batcher"}},
    {"id": "renderer_filter_heading_anchor", "plugin": "src.plugins.renderer_entries:apply_filter",
     "config": {"id": "heading_anchor"}},
    {"id": "renderer_filter_stream_optimizer", "plugin": "src.plugins.renderer_entries:apply_filter",
     "config": {"id": "stream_optimizer"}},
]

# ── Agent 中间件条目（每个内置中间件一个独立插件条目） ──────
#
# 条目经 ``src.plugins.middleware_entries`` 把 ``config.id`` 指向的内置中间件
# 注册进 ``src.core.middleware.registry``；``config.id`` 供组合根收集
# 「清单已接管的中间件」（注入 ``agent_middleware``，抑制默认装配）。

MIDDLEWARE_ENTRIES = [
    {"id": "middleware_interrupt", "plugin": "src.plugins.middleware_entries:apply_middleware",
     "config": {"id": "interrupt"}},
    {"id": "middleware_observability", "plugin": "src.plugins.middleware_entries:apply_middleware",
     "config": {"id": "observability"}},
    {"id": "middleware_audit", "plugin": "src.plugins.middleware_entries:apply_middleware",
     "config": {"id": "audit"}},
]

# ── SubAgent 类型条目（每个内置类型一个独立插件条目） ──────
#
# 条目经 ``src.plugins.agent_type_entries`` 把 ``config.name`` 指向的内置类型
# 注册进 ``src.core.agent_types``（Agent 类型注册表）；``config.name`` 供组合根
# 收集「清单已接管的类型」（注入 ``subagents``，抑制默认装配，使 overlay disable
# 单个类型真正生效）。

AGENT_TYPE_ENTRIES = [
    {"id": "agent_type_map", "plugin": "src.plugins.agent_type_entries:apply_agent_type",
     "config": {"name": "map"}},
    {"id": "agent_type_review", "plugin": "src.plugins.agent_type_entries:apply_agent_type",
     "config": {"name": "review"}},
    {"id": "agent_type_plan", "plugin": "src.plugins.agent_type_entries:apply_agent_type",
     "config": {"name": "plan"}},
    {"id": "agent_type_execute", "plugin": "src.plugins.agent_type_entries:apply_agent_type",
     "config": {"name": "execute"}},
]

# ── 流式处理器条目（每个内置流式 chunk 处理器一个独立插件条目） ──
#
# 条目经 ``src.plugins.stream_entries`` 把 ``config.id`` 指向的内置项注册进
# ``src.api.stream.registry``；``config.id`` 供组合根收集「清单已接管的处理器」
# （注入 ``stream``，抑制默认装配，使 overlay disable 单个处理器真正生效）。

STREAM_HANDLER_ENTRIES = [
    {"id": "stream_handler_reasoning", "plugin": "src.plugins.stream_entries:apply_stream_handler",
     "config": {"id": "reasoning"}},
    {"id": "stream_handler_content", "plugin": "src.plugins.stream_entries:apply_stream_handler",
     "config": {"id": "content"}},
    {"id": "stream_handler_tool_calls", "plugin": "src.plugins.stream_entries:apply_stream_handler",
     "config": {"id": "tool_calls"}},
    {"id": "stream_handler_speed", "plugin": "src.plugins.stream_entries:apply_stream_handler",
     "config": {"id": "speed"}},
]

# ── 通知后端条目（每个内置平台后端一个独立插件条目） ──────
#
# 条目经 ``src.plugins.notification_backends`` 把 ``config.id`` 指向的内置后端
# 注册进 ``src.notifications.registry``；``config.id`` 供组合根收集「清单已接管
# 的后端」（注入 notifications，抑制默认装配）。

NOTIFICATION_BACKEND_ENTRIES = [
    {"id": "notification_backend_termux", "plugin": "src.plugins.notification_backends:apply_notification_backend",
     "config": {"id": "termux"}},
    {"id": "notification_backend_linux", "plugin": "src.plugins.notification_backends:apply_notification_backend",
     "config": {"id": "linux"}},
    {"id": "notification_backend_windows", "plugin": "src.plugins.notification_backends:apply_notification_backend",
     "config": {"id": "windows"}},
]

# ── 上下文压缩策略条目（每个内置策略一个独立插件条目） ──────
#
# 条目经 ``src.plugins.context_strategy_entries`` 把 ``config.name`` 指向的内置
# 策略注册进 ``ctx.context``；``config.name`` 供组合根收集「清单已接管的策略」
# （注入 context，抑制默认装配）。

CONTEXT_STRATEGY_ENTRIES = [
    {"id": "context_strategy_summarize", "plugin": "src.plugins.context_strategy_entries:apply_context_strategy",
     "config": {"name": "summarize"}},
    {"id": "context_strategy_drop", "plugin": "src.plugins.context_strategy_entries:apply_context_strategy",
     "config": {"name": "drop"}},
]

# ── MCP 传输条目（每个内置传输一个独立插件条目） ────────────
#
# 条目经 ``src.plugins.mcp_transports`` 把 ``config.name`` 指向的内置传输注册进
# ``src.mcp.transport_registry``；``config.name`` 供组合根收集「清单已接管的
# 传输」（注入 mcp，抑制默认装配）。

MCP_TRANSPORT_ENTRIES = [
    {"id": "mcp_transport_stdio", "plugin": "src.plugins.mcp_transports:apply_mcp_transport",
     "config": {"name": "stdio"}},
    {"id": "mcp_transport_http", "plugin": "src.plugins.mcp_transports:apply_mcp_transport",
     "config": {"name": "http"}},
    {"id": "mcp_transport_sse", "plugin": "src.plugins.mcp_transports:apply_mcp_transport",
     "config": {"name": "sse"}},
]

# ── 工具执行引擎条目（每个内置引擎一个独立插件条目） ────────
#
# 条目经 ``src.plugins.tool_engine_entries`` 把 ``config.id`` 指向的内置引擎注册
# 进 ``src.core.tool_engines``；``config.id`` 供组合根收集「清单已接管的引擎」
# （注入 tool_scheduler，抑制默认装配）。

TOOL_ENGINE_ENTRIES = [
    {"id": "tool_engine_dag", "plugin": "src.plugins.tool_engine_entries:apply_tool_engine",
     "config": {"id": "dag"}},
    {"id": "tool_engine_serial", "plugin": "src.plugins.tool_engine_entries:apply_tool_engine",
     "config": {"id": "serial"}},
    {"id": "tool_engine_parallel", "plugin": "src.plugins.tool_engine_entries:apply_tool_engine",
     "config": {"id": "parallel"}},
]

# ── 事件消费者条目（每个内置消费者一个独立插件条目） ────────
#
# 条目经 ``src.plugins.consumer_entries`` 把 ``config.id`` 指向的内置消费者注册
# 进 ``src.tui.events.consumer_registry``；``config.id`` 供组合根收集「清单已
# 接管的消费者」（注入 consumers，抑制默认装配）。

CONSUMER_ENTRIES = [
    {"id": "consumer_output", "plugin": "src.plugins.consumer_entries:apply_consumer",
     "config": {"id": "output"}},
    {"id": "consumer_chat_ui", "plugin": "src.plugins.consumer_entries:apply_consumer",
     "config": {"id": "chat_ui"}},
    {"id": "consumer_error_handler", "plugin": "src.plugins.consumer_entries:apply_consumer",
     "config": {"id": "error_handler"}},
]

# ── UI 视图条目（每个内置 TUI 视图一个独立插件条目） ────────
#
# 条目经 ``src.plugins.ui_views`` 把 ``config.id`` 指向的内置视图注册进
# ``src.tui.app.view_registry``；``config.id`` 供组合根收集「清单已接管的视图」
# （注入 ui，抑制默认装配）。

UI_VIEW_ENTRIES = [
    {"id": "ui_view_trace", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "trace"}},
    {"id": "ui_view_trace_tools", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "trace_tools"}},
    {"id": "ui_view_config", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "config"}},
    {"id": "ui_view_plugin", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "plugin"}},
    {"id": "ui_view_help", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "help"}},
    {"id": "ui_view_user_select", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "user_select"}},
    {"id": "ui_view_editmsg", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "editmsg"}},
]

# ── Preset 条目（每个内置 preset 一个独立插件条目） ─────────
#
# 条目经 ``src.plugins.preset_entries`` 把 ``config.name`` 指向的内置 preset 注册
# 进 ``src.core.presets`` 注册表；``config.name`` 供组合根收集「清单已接管的
# preset」（注入 presets 聚合插件，抑制默认装配，使 overlay 禁用单项真正生效）。

PRESET_ENTRIES = [
    {"id": "preset_standard", "plugin": "src.plugins.preset_entries:apply_preset",
     "config": {"name": "standard"}},
    {"id": "preset_minimal", "plugin": "src.plugins.preset_entries:apply_preset",
     "config": {"name": "minimal"}},
    {"id": "preset_code", "plugin": "src.plugins.preset_entries:apply_preset",
     "config": {"name": "code"}},
]

# ── 提示词条目（每个运行模式 / 提词来源一个独立插件条目） ──────
#
# 条目经 ``src.plugins.prompt_entries`` 把 ``config.name`` 指向的内置模式 /
# 来源注册进 ``src.prompt_builder.modes`` / ``sources`` 注册表；``config.name``
# 供组合根收集「清单已接管项」（注入 prompt 聚合插件，抑制默认装配）。

PROMPT_MODE_ENTRIES = [
    {"id": "prompt_mode_empty", "plugin": "src.plugins.prompt_entries:apply_prompt_mode",
     "config": {"name": "empty"}},
    {"id": "prompt_mode_simple", "plugin": "src.plugins.prompt_entries:apply_prompt_mode",
     "config": {"name": "simple"}},
    {"id": "prompt_mode_standard", "plugin": "src.plugins.prompt_entries:apply_prompt_mode",
     "config": {"name": "standard"}},
]

PROMPT_SOURCE_ENTRIES = [
    {"id": "prompt_source_sub", "plugin": "src.plugins.prompt_entries:apply_prompt_source",
     "config": {"name": "sub"}},
    {"id": "prompt_source_map", "plugin": "src.plugins.prompt_entries:apply_prompt_source",
     "config": {"name": "map"}},
    {"id": "prompt_source_review", "plugin": "src.plugins.prompt_entries:apply_prompt_source",
     "config": {"name": "review"}},
    {"id": "prompt_source_plan", "plugin": "src.plugins.prompt_entries:apply_prompt_source",
     "config": {"name": "plan"}},
    {"id": "prompt_source_execute", "plugin": "src.plugins.prompt_entries:apply_prompt_source",
     "config": {"name": "execute"}},
]

# ── 提示词片段条目（每个系统提词片段一个独立插件条目） ────────
#
# 条目经 ``src.plugins.prompt_section_entries`` 把 ``config.name`` 指向的内置
# 片段注册进 ``src.prompt_builder.sections`` 注册表；``config.name`` 供组合根
# 收集「清单已接管的片段」（注入 prompt 聚合插件，抑制默认装配，使 overlay
# 禁用单项真正生效）。

PROMPT_SECTION_ENTRIES = [
    {"id": f"prompt_section_{name}",
     "plugin": "src.plugins.prompt_section_entries:apply_prompt_section",
     "config": {"name": name}}
    for name in builtin_section_ids()
]

# ── ClawBot 远程命令条目（每个斜杠指令一个独立插件条目） ──────
#
# 条目经 ``src.plugins.clawbot_commands`` 把 ``config.name`` 指向的内置指令注册
# 进 ``src.clawbot.command_registry`` 注册表；``config.name`` 供组合根收集
# 「清单已接管的指令」（注入 clawbot 聚合插件，抑制默认装配）。

CLAWBOT_COMMAND_ENTRIES = [
    {"id": "clawbot_command_help", "plugin": "src.plugins.clawbot_commands:apply_clawbot_command",
     "config": {"name": "help"}},
    {"id": "clawbot_command_shell", "plugin": "src.plugins.clawbot_commands:apply_clawbot_command",
     "config": {"name": "shell"}},
    {"id": "clawbot_command_clear", "plugin": "src.plugins.clawbot_commands:apply_clawbot_command",
     "config": {"name": "clear"}},
    {"id": "clawbot_command_new", "plugin": "src.plugins.clawbot_commands:apply_clawbot_command",
     "config": {"name": "new"}},
    {"id": "clawbot_command_time", "plugin": "src.plugins.clawbot_commands:apply_clawbot_command",
     "config": {"name": "time"}},
    {"id": "clawbot_command_status", "plugin": "src.plugins.clawbot_commands:apply_clawbot_command",
     "config": {"name": "status"}},
    {"id": "clawbot_command_model", "plugin": "src.plugins.clawbot_commands:apply_clawbot_command",
     "config": {"name": "model"}},
    {"id": "clawbot_command_stop", "plugin": "src.plugins.clawbot_commands:apply_clawbot_command",
     "config": {"name": "stop"}},
]

# ── CLI 子命令条目（每个顶层子命令一个独立插件条目） ──────────
#
# 条目经 ``src.plugins.subcommand_entries`` 把 ``config.name`` 指向的内置子命令
# 注册进 ``src.app_init.subcommands`` 注册表；``config.name`` 供组合根收集
# 「清单已接管的子命令」（注入 app 聚合插件，抑制默认装配）。

SUBCOMMAND_ENTRIES = [
    {"id": "subcommand_version", "plugin": "src.plugins.subcommand_entries:apply_subcommand",
     "config": {"name": "version"}},
    {"id": "subcommand_dump_config", "plugin": "src.plugins.subcommand_entries:apply_subcommand",
     "config": {"name": "dump-config"}},
    {"id": "subcommand_plugin", "plugin": "src.plugins.subcommand_entries:apply_subcommand",
     "config": {"name": "plugin"}},
    {"id": "subcommand_session", "plugin": "src.plugins.subcommand_entries:apply_subcommand",
     "config": {"name": "session"}},
    {"id": "subcommand_config", "plugin": "src.plugins.subcommand_entries:apply_subcommand",
     "config": {"name": "config"}},
    {"id": "subcommand_check_invariants", "plugin": "src.plugins.subcommand_entries:apply_subcommand",
     "config": {"name": "check-invariants"}},
    {"id": "subcommand_clawbot", "plugin": "src.plugins.subcommand_entries:apply_subcommand",
     "config": {"name": "clawbot"}},
]

# ── 键位绑定条目（每个内置 Ctrl 绑定一个独立插件条目） ──────
#
# 条目经 ``src.plugins.keybinding_entries`` 把 ``config.id`` 指向的内置绑定
# 注册进 ``src.tui._keybindings`` 注册表；``config.id`` 供组合根收集「清单已
# 接管的绑定」（注入 keybindings 聚合插件，抑制默认装配，使 overlay 禁用单项
# 真正生效）。

KEYBINDING_ENTRIES = [
    {"id": "keybinding_ctrl_g", "plugin": "src.plugins.keybinding_entries:apply_keybinding",
     "config": {"id": "ctrl_g"}},
    {"id": "keybinding_ctrl_o", "plugin": "src.plugins.keybinding_entries:apply_keybinding",
     "config": {"id": "ctrl_o"}},
    {"id": "keybinding_ctrl_h", "plugin": "src.plugins.keybinding_entries:apply_keybinding",
     "config": {"id": "ctrl_h"}},
    {"id": "keybinding_ctrl_r", "plugin": "src.plugins.keybinding_entries:apply_keybinding",
     "config": {"id": "ctrl_r"}},
    {"id": "keybinding_ctrl_l", "plugin": "src.plugins.keybinding_entries:apply_keybinding",
     "config": {"id": "ctrl_l"}},
    {"id": "keybinding_ctrl_d", "plugin": "src.plugins.keybinding_entries:apply_keybinding",
     "config": {"id": "ctrl_d"}},
    {"id": "keybinding_ctrl_t", "plugin": "src.plugins.keybinding_entries:apply_keybinding",
     "config": {"id": "ctrl_t"}},
    {"id": "keybinding_ctrl_n", "plugin": "src.plugins.keybinding_entries:apply_keybinding",
     "config": {"id": "ctrl_n"}},
    {"id": "keybinding_ctrl_p", "plugin": "src.plugins.keybinding_entries:apply_keybinding",
     "config": {"id": "ctrl_p"}},
    {"id": "keybinding_ctrl_b", "plugin": "src.plugins.keybinding_entries:apply_keybinding",
     "config": {"id": "ctrl_b"}},
    {"id": "keybinding_ctrl_slash", "plugin": "src.plugins.keybinding_entries:apply_keybinding",
     "config": {"id": "ctrl_slash"}},
    {"id": "keybinding_ctrl_z", "plugin": "src.plugins.keybinding_entries:apply_keybinding",
     "config": {"id": "ctrl_z"}},
    {"id": "keybinding_ctrl_y", "plugin": "src.plugins.keybinding_entries:apply_keybinding",
     "config": {"id": "ctrl_y"}},
]

# ── 特殊键处理器条目（每个内置 action 一个独立插件条目） ──────
#
# 条目经 ``src.plugins.special_key_entries`` 把 ``config.id`` 指向的内置处理器
# 注册进 ``src.app_loop._special_handlers`` 注册表；``config.id`` 供组合根收集
# 「清单已接管的处理器」（注入 special_keys 聚合插件，抑制默认装配）。

SPECIAL_KEY_ENTRIES = [
    {"id": "special_key_vim", "plugin": "src.plugins.special_key_entries:apply_special_key",
     "config": {"id": "vim"}},
    {"id": "special_key_editmsg", "plugin": "src.plugins.special_key_entries:apply_special_key",
     "config": {"id": "editmsg"}},
    {"id": "special_key_retry", "plugin": "src.plugins.special_key_entries:apply_special_key",
     "config": {"id": "retry"}},
    {"id": "special_key_toggle_theme", "plugin": "src.plugins.special_key_entries:apply_special_key",
     "config": {"id": "toggle_theme"}},
    {"id": "special_key_switch_model", "plugin": "src.plugins.special_key_entries:apply_special_key",
     "config": {"id": "switch_model"}},
    {"id": "special_key_cycle_mode", "plugin": "src.plugins.special_key_entries:apply_special_key",
     "config": {"id": "cycle_mode"}},
]

# ── 工具表现条目（每个工具 / 类别 / Agent 类型一个独立插件条目） ──
#
# 条目经 ``src.plugins.tool_style_entries`` 把 ``config.id`` 指向的内置表现注册
# 进 ``src.tui._tool_styles`` 注册表；``config.id`` 供组合根收集「清单已接管的
# 表现」（注入 tool_styles 聚合插件，抑制默认装配，使 overlay 覆盖/禁用单项真正
# 生效）。

_TOOL_STYLE_IDS = (
    "tool_bash", "tool_execute_command", "tool_read_file", "tool_write_file",
    "tool_update_file", "tool_str_replace_editor", "tool_file_editor",
    "tool_subagent", "tool_subagent_opt", "tool_user_select",
    "tool_web_search", "tool_web_fetch", "tool_rm", "tool_grep", "tool_find", "tool_glob",
    "cat_shell", "cat_file_read", "cat_file_write", "cat_search",
    "cat_agent", "cat_interact", "cat_delete",
    "agent_map", "agent_review", "agent_plan", "agent_execute",
)

TOOL_STYLE_ENTRIES = [
    {"id": f"tool_style_{spec_id}",
     "plugin": "src.plugins.tool_style_entries:apply_tool_style",
     "config": {"id": spec_id}}
    for spec_id in _TOOL_STYLE_IDS
]

# ── 语法高亮语言条目（每个内置语言一个独立插件条目） ──────────
#
# 条目经 ``src.plugins.syntax_entries`` 把 ``config.id`` 指向的内置语言注册进
# ``src.tui.ink.widgets._syntax_registry`` 注册表；``config.id`` 供组合根收集
# 「清单已接管的语言」（注入 syntax 聚合插件，抑制默认装配）。

SYNTAX_LANGUAGE_ENTRIES = [
    {"id": f"syntax_language_{lang_id}",
     "plugin": "src.plugins.syntax_entries:apply_syntax_language",
     "config": {"id": lang_id}}
    for lang_id in (
        "python", "javascript", "typescript", "go", "rust", "java", "c", "cpp",
        "ruby", "shell", "sql", "yaml", "json", "css", "html",
    )
]

# ── 表现层数据条目（每张数据表一个独立插件条目） ──────────────
#
# 条目经 ``src.plugins.presentation_data_entries`` 把 ``config.id`` 指向的内置表
# 注册进 ``src.presentation_data`` 注册表；``config.id`` 供组合根收集「清单已
# 接管的数据表」（注入 presentation_data 聚合插件，抑制默认装配）。

PRESENTATION_DATA_ENTRIES = [
    {"id": f"presentation_data_{table_id}",
     "plugin": "src.plugins.presentation_data_entries:apply_presentation_data",
     "config": {"id": table_id}}
    for table_id in (
        "emoji", "inline_subscript", "inline_superscript", "circled_digits",
        "html_tag_color", "bullet", "trace_kind", "trace_status",
        "mode_text", "mode_style",
        "tool_display_name", "admonition_style", "spinner_frames",
        "inline_spinner_frames", "config_entry_desc", "config_entry_option",
        "trace_kind_order", "trace_block_kind", "message_role_icon",
        "border_chars", "border_object_default",
        "billing_default", "metric_defaults", "ui_defaults",
        "semantic_color", "gradient_stops", "shell_detect",
        "http_error_hint", "badge_metrics", "kitty_protocol",
        "nested_bullet", "diff_style", "trace_style", "trace_keymap",
        "model_patterns",
    )
]

# ── host 组件条目（每个内置 host 一个独立插件条目） ───────────
#
# 条目经 ``src.plugins.host_entries`` 把 ``config.id`` 指向的内置 host 注册进
# ``src.tui.ink.registry`` 注册表；``config.id`` 供组合根收集「清单已接管的
# host」（注入 hosts 聚合插件，抑制默认装配）。

HOST_ENTRIES = [
    {"id": "host_static_lines", "plugin": "src.plugins.host_entries:apply_host",
     "config": {"id": "static-lines"}},
]

# ── 补全提供者条目（每个内置提供者一个独立插件条目） ──────────
#
# 条目经 ``src.plugins.completion_provider_entries`` 把 ``config.id`` 指向的内置
# 提供者注册进 ``src.tui._completion_providers`` 注册表；``config.id`` 供组合根
# 收集「清单已接管的提供者」（注入 completion_providers 聚合插件，抑制默认装配）。

COMPLETION_PROVIDER_ENTRIES = [
    {"id": "completion_provider_command", "plugin": "src.plugins.completion_provider_entries:apply_completion_provider",
     "config": {"id": "command"}},
    {"id": "completion_provider_param", "plugin": "src.plugins.completion_provider_entries:apply_completion_provider",
     "config": {"id": "param"}},
    {"id": "completion_provider_path", "plugin": "src.plugins.completion_provider_entries:apply_completion_provider",
     "config": {"id": "path"}},
]

# ── 状态栏段条目（每个内置段一个独立插件条目） ────────────────
#
# 条目经 ``src.plugins.status_segment_entries`` 把 ``config.id`` 指向的内置段注册
# 进 ``src.tui.app._status_segments`` 注册表；``config.id`` 供组合根收集「清单已
# 接管的段」（注入 status_segments 聚合插件，抑制默认装配）。

STATUS_SEGMENT_ENTRIES = [
    {"id": "status_segment_model", "plugin": "src.plugins.status_segment_entries:apply_status_segment",
     "config": {"id": "model"}},
    {"id": "status_segment_tools", "plugin": "src.plugins.status_segment_entries:apply_status_segment",
     "config": {"id": "tools"}},
    {"id": "status_segment_elapsed", "plugin": "src.plugins.status_segment_entries:apply_status_segment",
     "config": {"id": "elapsed"}},
    {"id": "status_segment_tokens", "plugin": "src.plugins.status_segment_entries:apply_status_segment",
     "config": {"id": "tokens"}},
    {"id": "status_segment_messages", "plugin": "src.plugins.status_segment_entries:apply_status_segment",
     "config": {"id": "messages"}},
    {"id": "status_segment_speed", "plugin": "src.plugins.status_segment_entries:apply_status_segment",
     "config": {"id": "speed"}},
]

# ── 运行时数据服务条目（未接缝能力下沉为 ctx.* 服务） ────────
#
# 每项一个独立插件条目（经 ``src.plugins.runtime_data`` 提供对应服务），可按
# Profile/Patch 禁用或替换。

RUNTIME_DATA_ENTRIES = [
    {"id": "message_queue", "plugin": "src.plugins.runtime_data:apply_message_queue"},
    {"id": "multimodal", "plugin": "src.plugins.runtime_data:apply_multimodal"},
    {"id": "context_selector", "plugin": "src.plugins.runtime_data:apply_context_selector"},
    {"id": "context_summarizer", "plugin": "src.plugins.runtime_data:apply_context_summarizer"},
    {"id": "stats", "plugin": "src.plugins.runtime_data:apply_stats"},
    {"id": "tokens", "plugin": "src.plugins.runtime_data:apply_tokens"},
]

# ── Web 搜索/抓取提供者条目（每个内置提供者一个独立插件条目） ──
#
# 条目经 ``src.plugins.web_search_providers`` / ``web_fetch_providers`` 把该
# id 的内置提供者注册进对应注册表；``config.id`` 供组合根收集「清单已接管的
# 提供者」（注入 web_search / web_fetch 聚合插件，抑制默认装配）。

WEB_SEARCH_PROVIDER_ENTRIES = [
    {"id": "web_search_provider_deepseek", "plugin": "src.plugins.web_search_providers:apply_search_provider",
     "config": {"id": "deepseek"}},
]

WEB_FETCH_PROVIDER_ENTRIES = [
    {"id": "web_fetch_provider_http", "plugin": "src.plugins.web_fetch_providers:apply_fetch_provider",
     "config": {"id": "http"}},
]

# ── 主题条目（每个内置主题一个独立插件条目） ────────────────
#
# 条目经 ``src.plugins.theme_entries`` 把 ``config.name`` 指向的内置主题注册
# 进 ``src.tui.core._theme``；``config.name`` 供组合根收集「清单已接管的主题」
# （注入 themes 聚合插件，抑制默认装配）。

THEME_ENTRIES = [
    {"id": "theme_dark", "plugin": "src.plugins.theme_entries:apply_theme",
     "config": {"name": "dark"}},
    {"id": "theme_light", "plugin": "src.plugins.theme_entries:apply_theme",
     "config": {"name": "light"}},
    {"id": "theme_high_contrast", "plugin": "src.plugins.theme_entries:apply_theme",
     "config": {"name": "high-contrast"}},
    {"id": "theme_nord", "plugin": "src.plugins.theme_entries:apply_theme",
     "config": {"name": "nord"}},
    {"id": "theme_dracula", "plugin": "src.plugins.theme_entries:apply_theme",
     "config": {"name": "dracula"}},
    {"id": "theme_gruvbox", "plugin": "src.plugins.theme_entries:apply_theme",
     "config": {"name": "gruvbox"}},
]

# ── 技能来源条目（每个内置来源一个独立插件条目） ────────────
#
# 条目经 ``src.plugins.skill_source_entries`` 把 ``config.id`` 指向的内置来源
# 注册进 ``src.skills.source_registry``；``config.id`` 供组合根收集「清单已
# 接管的来源」（注入 skill_sources 聚合插件，抑制默认装配）。

SKILL_SOURCE_ENTRIES = [
    {"id": "skill_source_project", "plugin": "src.plugins.skill_source_entries:apply_skill_source",
     "config": {"id": "project"}},
    {"id": "skill_source_installed", "plugin": "src.plugins.skill_source_entries:apply_skill_source",
     "config": {"id": "installed"}},
]

# ── 会话投影条目（每个内置投影一个独立插件条目） ────────────
#
# 条目经 ``src.plugins.session_projection_entries`` 把 ``config.name`` 指向的
# 内置投影注册进 ``ctx.session_projections``；``config.name`` 供组合根收集
# 「清单已接管的投影」（注入 session_projections 聚合插件，抑制默认装配）。

SESSION_PROJECTION_ENTRIES = [
    {"id": "session_projection_turn_boundary", "plugin": "src.plugins.session_projection_entries:apply_projection",
     "config": {"name": "turnBoundary"}},
]

# ── 渲染目标条目（每个内置目标一个独立插件条目） ────────────
#
# 条目经 ``src.plugins.renderer_targets`` 把 ``config.id`` 指向的内置目标注册
# 进 ``src.renderer.targets.registry``；``config.id`` 供组合根收集「清单已
# 接管的渲染目标」（注入 renderer 聚合插件，抑制默认装配）。

RENDERER_TARGET_ENTRIES = [
    {"id": "renderer_target_terminal", "plugin": "src.plugins.renderer_targets:apply_renderer_target",
     "config": {"id": "terminal"}},
    {"id": "renderer_target_file", "plugin": "src.plugins.renderer_targets:apply_renderer_target",
     "config": {"id": "file"}},
]

# ── Bundle 定义 ─────────────────────────────────────────────

BUNDLES = [
    {
        "id": "invariants",
        "description": "运行时不变量：每条检查一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(INVARIANT_ENTRIES),
    },
    {
        "id": "tools",
        "description": "内置工具：每个工具一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(TOOL_PLUGIN_ENTRIES),
    },
    {
        "id": "tool_metadata",
        "description": "工具元数据：每个内置工具一条独立条目（可 patch/overlay 覆盖/禁用/替换）",
        "plugins": [
            {"id": "tool_metadata", "plugin": "src.plugins.tool_metadata"},
        ] + list(TOOL_METADATA_ENTRIES),
    },
    {
        "id": "tool_consts",
        "description": "工具常量：每个内置常量一条独立条目（可 patch/overlay 覆盖/禁用/替换）",
        "plugins": [
            {"id": "tool_consts", "plugin": "src.plugins.tool_consts"},
        ] + list(TOOL_CONST_ENTRIES),
    },
    {
        "id": "event_types",
        "description": "事件类型：每个内置事件类型一条独立条目（可 patch/overlay 覆盖/禁用/替换）",
        "plugins": [
            {"id": "event_types", "plugin": "src.plugins.event_types"},
        ] + list(EVENT_TYPE_ENTRIES),
    },
    {
        "id": "named_styles",
        "description": "命名样式：每个内置命名样式一条独立条目（可 patch/overlay 覆盖/禁用/替换）",
        "plugins": [
            {"id": "named_styles", "plugin": "src.plugins.named_styles"},
        ] + list(NAMED_STYLE_ENTRIES),
    },
    {
        "id": "tool_policy",
        "description": "全局禁用工具：每个禁用项一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(GLOBAL_DISABLED_TOOL_ENTRIES),
    },
    {
        "id": "commands",
        "description": "内置命令：每个命令一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(COMMAND_PLUGIN_ENTRIES),
    },
    {
        "id": "renderer_ext",
        "description": "渲染扩展：每个内置 handler/filter/target 一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(RENDERER_HANDLER_ENTRIES) + list(RENDERER_FILTER_ENTRIES) + list(RENDERER_TARGET_ENTRIES),
    },
    {
        "id": "web",
        "description": "Web 能力：搜索/抓取提供者各一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": [
            {"id": "web_search", "plugin": "src.plugins.web_search"},
            {"id": "web_fetch", "plugin": "src.plugins.web_fetch"},
        ] + list(WEB_SEARCH_PROVIDER_ENTRIES) + list(WEB_FETCH_PROVIDER_ENTRIES),
    },
    {
        "id": "themes",
        "description": "主题：每个内置主题一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": [
            {"id": "themes", "plugin": "src.plugins.themes"},
        ] + list(THEME_ENTRIES),
    },
    {
        "id": "skill_sources",
        "description": "技能来源：每个内置来源一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": [
            {"id": "skill_sources", "plugin": "src.plugins.skill_sources"},
        ] + list(SKILL_SOURCE_ENTRIES),
    },
    {
        "id": "middleware",
        "description": "Agent 中间件：每个内置中间件一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(MIDDLEWARE_ENTRIES),
    },
    {
        "id": "agent_types",
        "description": "SubAgent 类型：每个内置类型一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(AGENT_TYPE_ENTRIES),
    },
    {
        "id": "stream",
        "description": "流式处理器：每个内置流式 chunk 处理器一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(STREAM_HANDLER_ENTRIES),
    },
    {
        "id": "notification_backends",
        "description": "通知后端：每个内置平台后端一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(NOTIFICATION_BACKEND_ENTRIES),
    },
    {
        "id": "context_strategies",
        "description": "上下文压缩策略：每个内置策略一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(CONTEXT_STRATEGY_ENTRIES),
    },
    {
        "id": "mcp_transports",
        "description": "MCP 传输：每个内置传输一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(MCP_TRANSPORT_ENTRIES),
    },
    {
        "id": "tool_engines",
        "description": "工具执行引擎：每个内置引擎一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(TOOL_ENGINE_ENTRIES),
    },
    {
        "id": "consumers",
        "description": "事件消费者：每个内置消费者一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(CONSUMER_ENTRIES),
    },
    {
        "id": "ui_views",
        "description": "UI 视图：每个内置 TUI 视图一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(UI_VIEW_ENTRIES),
    },
    {
        "id": "runtime_data",
        "description": "运行时数据服务：消息队列 / 多模态 / 上下文选择与摘要 / 统计 / token 估算",
        "plugins": list(RUNTIME_DATA_ENTRIES),
    },
    {
        "id": "presets",
        "description": "Preset：每个内置能力组合一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": [
            {"id": "presets", "plugin": "src.plugins.presets"},
        ] + list(PRESET_ENTRIES),
    },
    {
        "id": "prompts",
        "description": "提示词：每个运行模式 / 提词来源 / 提词片段一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(PROMPT_MODE_ENTRIES) + list(PROMPT_SOURCE_ENTRIES) + list(PROMPT_SECTION_ENTRIES),
    },
    {
        "id": "clawbot",
        "description": "ClawBot 远程控制：每个斜杠指令一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": [
            {"id": "clawbot", "plugin": "src.plugins.clawbot"},
        ] + list(CLAWBOT_COMMAND_ENTRIES),
    },
    {
        "id": "subcommands",
        "description": "CLI 顶层子命令：每个子命令一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(SUBCOMMAND_ENTRIES),
    },
    {
        "id": "keybindings",
        "description": "TUI 键位绑定：每个 Ctrl 绑定一个独立插件条目（可 patch/overlay 改键/禁用/替换）",
        "plugins": [
            {"id": "keybindings", "plugin": "src.plugins.keybindings"},
        ] + list(KEYBINDING_ENTRIES),
    },
    {
        "id": "special_keys",
        "description": "特殊键处理器：每个 action 一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": [
            {"id": "special_keys", "plugin": "src.plugins.special_keys"},
        ] + list(SPECIAL_KEY_ENTRIES),
    },
    {
        "id": "tool_styles",
        "description": "工具表现：每个工具/类别/Agent 类型一个独立插件条目（可 patch/overlay 覆盖/禁用/替换）",
        "plugins": [
            {"id": "tool_styles", "plugin": "src.plugins.tool_styles"},
        ] + list(TOOL_STYLE_ENTRIES),
    },
    {
        "id": "syntax_languages",
        "description": "语法高亮语言：每个内置语言一个独立插件条目（可 patch/overlay 覆盖/禁用/替换）",
        "plugins": [
            {"id": "syntax", "plugin": "src.plugins.syntax"},
        ] + list(SYNTAX_LANGUAGE_ENTRIES),
    },
    {
        "id": "presentation_data",
        "description": "表现层数据表：每张数据表一个独立插件条目（可 patch/overlay 覆盖/禁用/替换）",
        "plugins": [
            {"id": "presentation_data", "plugin": "src.plugins.presentation_data"},
        ] + list(PRESENTATION_DATA_ENTRIES),
    },
    {
        "id": "hosts",
        "description": "内核 host 组件：每个内置 host 一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": [
            {"id": "hosts", "plugin": "src.plugins.hosts"},
        ] + list(HOST_ENTRIES),
    },
    {
        "id": "completion_providers",
        "description": "补全提供者：每个提供者一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": [
            {"id": "completion_providers", "plugin": "src.plugins.completion_providers"},
        ] + list(COMPLETION_PROVIDER_ENTRIES),
    },
    {
        "id": "status_segments",
        "description": "状态栏段：每个段一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": [
            {"id": "status_segments", "plugin": "src.plugins.status_segments"},
        ] + list(STATUS_SEGMENT_ENTRIES),
    },
    {
        "id": "core",
        "description": "基础层：配置、事件、提词、策略、工具、技能、通知后端",
        "includes": ["tools", "tool_metadata", "tool_consts", "tool_policy", "event_types", "commands", "notification_backends", "context_strategies", "runtime_data", "presets", "prompts", "subcommands", "web", "skill_sources", "invariants"],
        "plugins": [
            {"id": "config", "plugin": "src.plugins.config"},
            {"id": "events", "plugin": "src.plugins.events"},
            {"id": "output", "plugin": "src.plugins.output"},
            {"id": "cache", "plugin": "src.plugins.cache"},
            {"id": "observability", "plugin": "src.plugins.observability"},
            {"id": "prompt", "plugin": "src.plugins.prompt", "config": {"empty_mode": True}},
            {"id": "fs", "plugin": "src.plugins.seams:apply_fs"},
            {"id": "subprocess", "plugin": "src.plugins.seams:apply_subprocess"},
            {"id": "shell", "plugin": "src.plugins.seams:apply_shell"},
            {"id": "terminals", "plugin": "src.plugins.seams:apply_terminals"},
            {"id": "jobs", "plugin": "src.plugins.seams:apply_jobs"},
            {"id": "sandbox", "plugin": "src.plugins.seams:apply_sandbox"},
            {"id": "policy", "plugin": "src.plugins.policy"},
            {"id": "notifications", "plugin": "src.plugins.notifications"},
            {"id": "agents", "plugin": "src.plugins.agents"},
            {"id": "persistence", "plugin": "src.plugins.persistence:apply_persistence"},
            {"id": "checkpoint", "plugin": "src.plugins.persistence:apply_checkpoint"},
            {"id": "session_log", "plugin": "src.plugins.session_log"},
            {"id": "session_projections", "plugin": "src.plugins.session_projections"},
            {"id": "session_projection_turn_boundary", "plugin": "src.plugins.session_projection_entries:apply_projection", "config": {"name": "turnBoundary"}},
            {"id": "tools", "plugin": "src.plugins.tools"},
            {"id": "tools_builtin", "plugin": "src.plugins.tools_builtin"},
            {"id": "context", "plugin": "src.plugins.context"},
            {"id": "seams", "plugin": "src.plugins.seams:apply_seams"},
            {"id": "skills", "plugin": "src.plugins.skills"},
            {"id": "invariants", "plugin": "src.plugins.invariants"},
            {"id": "kernel_admin", "plugin": "src.plugins.kernel_admin"},
            {"id": "app", "plugin": "src.plugins.app"},
            {"id": "escape_monitor", "plugin": "src.plugins.escape_monitor"},
        ],
    },
    {
        "id": "model",
        "description": "模型适配器（Provider 路由 + 内置 Provider 插件）",
        "plugins": [
            {"id": "llm", "plugin": "src.plugins.llm"},
            {"id": "llm_provider_deepseek", "plugin": "src.plugins.llm_providers:apply_deepseek",
             "config": {"name": "deepseek"}},
            {"id": "llm_provider_anthropic", "plugin": "src.plugins.llm_providers:apply_anthropic",
             "config": {"name": "anthropic"}},
            {"id": "llm_provider_ollama", "plugin": "src.plugins.llm_providers:apply_ollama",
             "config": {"name": "ollama"}},
            {"id": "llm_provider_openai_compat", "plugin": "src.plugins.llm_providers:apply_openai_compat",
             "config": {"name": "openai_compat"}},
        ],
    },
    {
        "id": "runtime",
        "description": "运行时：会话、SubAgent、流式管线、Agent 循环、命令、MCP",
        "includes": ["middleware", "agent_types", "stream", "mcp_transports", "tool_engines", "clawbot"],
        "plugins": [
            {"id": "sessions", "plugin": "src.plugins.sessions"},
            {"id": "subagents", "plugin": "src.plugins.subagents"},
            {"id": "stream", "plugin": "src.plugins.stream"},
            {"id": "tool_scheduler", "plugin": "src.plugins.tool_scheduler"},
            {"id": "agent_middleware", "plugin": "src.plugins.agent_middleware"},
            {"id": "agent_loop", "plugin": "src.plugins.agent_loop"},
            {"id": "interactive_loop", "plugin": "src.plugins.application:apply_interactive_loop"},
            {"id": "application", "plugin": "src.plugins.application:apply_application"},
            {"id": "commands", "plugin": "src.plugins.commands"},
            {"id": "mcp", "plugin": "src.plugins.mcp"},
        ],
    },
    {
        "id": "presentation",
        "description": "表现层：UI、渲染器、事件消费者、键位",
        "includes": ["renderer_ext", "consumers", "ui_views", "themes", "named_styles", "keybindings", "special_keys", "tool_styles", "syntax_languages", "presentation_data", "hosts", "completion_providers", "status_segments"],
        "plugins": [
            {"id": "renderer", "plugin": "src.plugins.renderer"},
            {"id": "renderer_builtin", "plugin": "src.plugins.renderer_builtin"},
            {"id": "consumers", "plugin": "src.plugins.consumers"},
            {"id": "ui", "plugin": "src.plugins.ui"},
        ],
    },
]

# ── Profile 定义 ────────────────────────────────────────────

PROFILES = [
    {
        "name": "cli",
        "description": "完整终端应用",
        "bundles": ["core", "model", "runtime", "presentation"],
    },
    {
        "name": "headless",
        "description": "无 UI 后端运行时",
        "bundles": ["core", "model", "runtime"],
    },
    {
        "name": "minimal",
        "description": "最小内核",
        "bundles": ["core", "model"],
    },
    {
        "name": "full",
        "description": "完整应用（cli 的语义化别名）",
        "bundles": ["core", "model", "runtime", "presentation"],
    },
]

DEFAULT_PROFILE = "cli"


def build_config_tree() -> ConfigTree:
    """构建内置 ConfigTree。"""
    tree = ConfigTree()
    for bundle in BUNDLES:
        tree.add_bundle_dict(bundle)
    for profile in PROFILES:
        tree.add_profile_dict(profile)
    return tree


__all__ = [
    "BUNDLES",
    "PROFILES",
    "DEFAULT_PROFILE",
    "INVARIANT_ENTRIES",
    "TOOL_PLUGIN_ENTRIES",
    "TOOL_METADATA_ENTRIES",
    "TOOL_CONST_ENTRIES",
    "EVENT_TYPE_ENTRIES",
    "NAMED_STYLE_ENTRIES",
    "GLOBAL_DISABLED_TOOL_ENTRIES",
    "COMMAND_PLUGIN_ENTRIES",
    "RENDERER_HANDLER_ENTRIES",
    "RENDERER_FILTER_ENTRIES",
    "MIDDLEWARE_ENTRIES",
    "AGENT_TYPE_ENTRIES",
    "STREAM_HANDLER_ENTRIES",
    "NOTIFICATION_BACKEND_ENTRIES",
    "CONTEXT_STRATEGY_ENTRIES",
    "MCP_TRANSPORT_ENTRIES",
    "TOOL_ENGINE_ENTRIES",
    "CONSUMER_ENTRIES",
    "UI_VIEW_ENTRIES",
    "RUNTIME_DATA_ENTRIES",
    "PRESET_ENTRIES",
    "PROMPT_MODE_ENTRIES",
    "PROMPT_SOURCE_ENTRIES",
    "PROMPT_SECTION_ENTRIES",
    "CLAWBOT_COMMAND_ENTRIES",
    "SUBCOMMAND_ENTRIES",
    "KEYBINDING_ENTRIES",
    "SPECIAL_KEY_ENTRIES",
    "TOOL_STYLE_ENTRIES",
    "SYNTAX_LANGUAGE_ENTRIES",
    "PRESENTATION_DATA_ENTRIES",
    "HOST_ENTRIES",
    "COMPLETION_PROVIDER_ENTRIES",
    "STATUS_SEGMENT_ENTRIES",
    "WEB_SEARCH_PROVIDER_ENTRIES",
    "WEB_FETCH_PROVIDER_ENTRIES",
    "THEME_ENTRIES",
    "SKILL_SOURCE_ENTRIES",
    "SESSION_PROJECTION_ENTRIES",
    "RENDERER_TARGET_ENTRIES",
    "build_config_tree",
]
