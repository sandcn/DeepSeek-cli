"""内置插件清单条目 — 按能力域分组的独立插件条目定义。

从 ``src.plugins.manifest`` 拆出（该文件曾把全部条目内联为 900+ 行字面量，
新增条目时冲突与漏改风险高）。本模块只负责**条目声明**：

- 每个条目是 ``{"id", "plugin", "config"}``，可被 Profile/Bundle 声明，
  也可被 Patch/Overlay 按 id 覆盖配置、禁用或替换；
- ``config.name`` / ``config.id`` 供组合根收集「清单已接管项」，注入聚合
  插件以抑制默认装配，使 overlay 禁用单项真正生效；
- 条目按插入顺序参与 Bundle 组装，**顺序即注册顺序**，调整顺序会影响
  依赖解析与呈现顺序。

Bundle/Profile 组装与 ConfigTree 构建见 ``src.plugins.manifest``。
"""

from __future__ import annotations

from ..core.cordis_tools import CORDIS_TOOLS
from ..kernel.invariant_registry import builtin_invariant_ids
from ..prompt_builder.sections import builtin_section_ids

# ── 运行时不变量条目（每条检查一个独立插件条目，可被 patch/overlay 禁用/替换） ──

INVARIANT_ENTRIES = [
    {"id": f"invariant_{name.replace('.', '_')}",
     "plugin": "src.plugins.invariant_entries:apply_invariant",
     "config": {"name": name}}
    for name in builtin_invariant_ids()
]

# ── 内置工具条目（每个工具一个独立插件条目，可被 patch/overlay 禁用/替换） ──

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
    "CompactionChangedEvent",
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

GLOBAL_DISABLED_TOOL_ENTRIES = [
    {"id": f"global_disabled_tool_{name}",
     "plugin": "src.plugins.tool_policy_entries:apply_global_disabled_tool",
     "config": {"name": name}}
    for name in CORDIS_TOOLS
]

# ── 内置命令条目（每个命令一个独立插件条目，可被 patch/overlay 禁用/替换） ──

COMMAND_PLUGIN_ENTRIES = [
    {"id": "cmd_changes", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._session_cmd.ChangesCommand", "name": "changes"}},
    {"id": "cmd_sandbox", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._sandbox_cmd.SandboxCommand", "name": "sandbox"}},
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
    {"id": "cmd_retry", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._session_cmd.RetryCommand", "name": "retry"}},
    {"id": "cmd_sessions", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._data_cmd.SessionsCommand", "name": "sessions"}},
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
    {"id": "cmd_models", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands.plugins.models_plugin.ModelsPlugin", "name": "models"}},
    {"id": "cmd_skill", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands.plugins.skill_plugin.SkillPlugin", "name": "skill"}},
    {"id": "cmd_compact", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands.plugins.compact_plugin.CompactPlugin", "name": "compact"}},
    {"id": "cmd_context", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands.plugins.context_plugin.ContextPlugin", "name": "context"}},
    # ── 2026-10 新增视图命令 ──
    {"id": "cmd_keymap", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._keymap_cmd.KeymapCommand", "name": "keymap"}},
    {"id": "cmd_mcp", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._mcp_cmd.McpCommand", "name": "mcp"}},
    {"id": "cmd_usage", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._usage_cmd.UsageCommand", "name": "usage"}},
    {"id": "cmd_search", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._search_cmd.SearchCommand", "name": "search"}},
    {"id": "cmd_notify", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._notify_cmd.NotifyCommand", "name": "notify"}},
    {"id": "cmd_logs", "plugin": "src.plugins.command_plugin",
     "config": {"command": "src.core.commands._logs_cmd.LogsCommand", "name": "logs"}},
]

# ── 渲染扩展条目（每个内置 filter 一个独立插件条目） ──

RENDERER_FILTER_ENTRIES = [
    {"id": "renderer_filter_code_block_batcher", "plugin": "src.plugins.renderer_entries:apply_filter",
     "config": {"id": "code_block_batcher"}},
    {"id": "renderer_filter_heading_anchor", "plugin": "src.plugins.renderer_entries:apply_filter",
     "config": {"id": "heading_anchor"}},
    {"id": "renderer_filter_stream_optimizer", "plugin": "src.plugins.renderer_entries:apply_filter",
     "config": {"id": "stream_optimizer"}},
]

# ── Agent 中间件条目（每个内置中间件一个独立插件条目） ──────

MIDDLEWARE_ENTRIES = [
    {"id": "middleware_interrupt", "plugin": "src.plugins.middleware_entries:apply_middleware",
     "config": {"id": "interrupt"}},
    {"id": "middleware_observability", "plugin": "src.plugins.middleware_entries:apply_middleware",
     "config": {"id": "observability"}},
    {"id": "middleware_audit", "plugin": "src.plugins.middleware_entries:apply_middleware",
     "config": {"id": "audit"}},
]

# ── SubAgent 类型条目（每个内置类型一个独立插件条目） ──────

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

NOTIFICATION_BACKEND_ENTRIES = [
    {"id": "notification_backend_termux", "plugin": "src.plugins.notification_backends:apply_notification_backend",
     "config": {"id": "termux"}},
    {"id": "notification_backend_linux", "plugin": "src.plugins.notification_backends:apply_notification_backend",
     "config": {"id": "linux"}},
    {"id": "notification_backend_windows", "plugin": "src.plugins.notification_backends:apply_notification_backend",
     "config": {"id": "windows"}},
]

# ── 上下文压缩策略条目（每个内置策略一个独立插件条目） ──────

CONTEXT_STRATEGY_ENTRIES = [
    {"id": "context_strategy_summarize", "plugin": "src.plugins.context_strategy_entries:apply_context_strategy",
     "config": {"name": "summarize"}},
    {"id": "context_strategy_drop", "plugin": "src.plugins.context_strategy_entries:apply_context_strategy",
     "config": {"name": "drop"}},
]

# ── MCP 传输条目（每个内置传输一个独立插件条目） ────────────

MCP_TRANSPORT_ENTRIES = [
    {"id": "mcp_transport_stdio", "plugin": "src.plugins.mcp_transports:apply_mcp_transport",
     "config": {"name": "stdio"}},
    {"id": "mcp_transport_http", "plugin": "src.plugins.mcp_transports:apply_mcp_transport",
     "config": {"name": "http"}},
    {"id": "mcp_transport_sse", "plugin": "src.plugins.mcp_transports:apply_mcp_transport",
     "config": {"name": "sse"}},
]

# ── 工具执行引擎条目（每个内置引擎一个独立插件条目） ────────

TOOL_ENGINE_ENTRIES = [
    {"id": "tool_engine_dag", "plugin": "src.plugins.tool_engine_entries:apply_tool_engine",
     "config": {"id": "dag"}},
    {"id": "tool_engine_serial", "plugin": "src.plugins.tool_engine_entries:apply_tool_engine",
     "config": {"id": "serial"}},
    {"id": "tool_engine_parallel", "plugin": "src.plugins.tool_engine_entries:apply_tool_engine",
     "config": {"id": "parallel"}},
]

# ── 事件消费者条目（每个内置消费者一个独立插件条目） ────────

CONSUMER_ENTRIES = [
    {"id": "consumer_output", "plugin": "src.plugins.consumer_entries:apply_consumer",
     "config": {"id": "output"}},
    {"id": "consumer_chat_ui", "plugin": "src.plugins.consumer_entries:apply_consumer",
     "config": {"id": "chat_ui"}},
    {"id": "consumer_error_handler", "plugin": "src.plugins.consumer_entries:apply_consumer",
     "config": {"id": "error_handler"}},
]

# ── UI 视图条目（每个内置 TUI 视图一个独立插件条目） ────────

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
    {"id": "ui_view_model", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "model"}},
    {"id": "ui_view_user_select", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "user_select"}},
    {"id": "ui_view_editmsg", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "editmsg"}},
    # ── 2026-10 新增全屏视图批次（每视图一个独立插件条目，可被
    #    Profile/Bundle 声明、Patch/Overlay 按 id 禁用/覆盖） ──
    {"id": "ui_view_sessions", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "sessions"}},
    {"id": "ui_view_changes", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "changes"}},
    {"id": "ui_view_sandbox", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "sandbox"}},
    {"id": "ui_view_sandbox_history", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "sandbox_history"}},
    {"id": "ui_view_sandbox_records", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "sandbox_records"}},
    {"id": "ui_view_theme", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "theme"}},
    {"id": "ui_view_skill", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "skill"}},
    {"id": "ui_view_mcp", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "mcp"}},
    {"id": "ui_view_usage", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "usage"}},
    {"id": "ui_view_search", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "search"}},
    {"id": "ui_view_keymap", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "keymap"}},
    {"id": "ui_view_notify", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "notify"}},
    {"id": "ui_view_export", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "export"}},
    {"id": "ui_view_logs", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "logs"}},
    {"id": "ui_view_palette", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "palette"}},
]

# ── Preset 条目（每个内置 preset 一个独立插件条目） ─────────

PRESET_ENTRIES = [
    {"id": "preset_standard", "plugin": "src.plugins.preset_entries:apply_preset",
     "config": {"name": "standard"}},
    {"id": "preset_minimal", "plugin": "src.plugins.preset_entries:apply_preset",
     "config": {"name": "minimal"}},
    {"id": "preset_code", "plugin": "src.plugins.preset_entries:apply_preset",
     "config": {"name": "code"}},
]

# ── 提示词条目（每个运行模式 / 提词来源一个独立插件条目） ──────

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

PROMPT_SECTION_ENTRIES = [
    {"id": f"prompt_section_{name}",
     "plugin": "src.plugins.prompt_section_entries:apply_prompt_section",
     "config": {"name": name}}
    for name in builtin_section_ids()
]

# ── ClawBot 远程命令条目（每个斜杠指令一个独立插件条目） ──────

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
        "plugin_keymap", "config_keymap", "model_keymap",
        "model_patterns",
    )
]

# ── host 组件条目（每个内置 host 一个独立插件条目） ───────────

HOST_ENTRIES = [
    {"id": "host_static_lines", "plugin": "src.plugins.host_entries:apply_host",
     "config": {"id": "static-lines"}},
    {"id": "host_live_lines", "plugin": "src.plugins.host_entries:apply_host",
     "config": {"id": "live-lines"}},
]

# ── 补全提供者条目（每个内置提供者一个独立插件条目） ──────────

COMPLETION_PROVIDER_ENTRIES = [
    {"id": "completion_provider_command", "plugin": "src.plugins.completion_provider_entries:apply_completion_provider",
     "config": {"id": "command"}},
    {"id": "completion_provider_param", "plugin": "src.plugins.completion_provider_entries:apply_completion_provider",
     "config": {"id": "param"}},
    {"id": "completion_provider_path", "plugin": "src.plugins.completion_provider_entries:apply_completion_provider",
     "config": {"id": "path"}},
]

# ── 状态栏段条目（每个内置段一个独立插件条目） ────────────────

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
    {"id": "status_segment_workspace", "plugin": "src.plugins.status_segment_entries:apply_status_segment",
     "config": {"id": "workspace"}},
    {"id": "status_segment_speed", "plugin": "src.plugins.status_segment_entries:apply_status_segment",
     "config": {"id": "speed"}},
]

# ── 运行时数据服务条目（未接缝能力下沉为 ctx.* 服务） ────────

RUNTIME_DATA_ENTRIES = [
    {"id": "message_queue", "plugin": "src.plugins.runtime_data:apply_message_queue"},
    {"id": "multimodal", "plugin": "src.plugins.runtime_data:apply_multimodal"},
    {"id": "context_selector", "plugin": "src.plugins.runtime_data:apply_context_selector"},
    {"id": "context_summarizer", "plugin": "src.plugins.runtime_data:apply_context_summarizer"},
    {"id": "stats", "plugin": "src.plugins.runtime_data:apply_stats"},
    {"id": "tokens", "plugin": "src.plugins.runtime_data:apply_tokens"},
]

# ── Web 搜索/抓取提供者条目（每个内置提供者一个独立插件条目） ──

WEB_SEARCH_PROVIDER_ENTRIES = [
    {"id": "web_search_provider_deepseek", "plugin": "src.plugins.web_search_providers:apply_search_provider",
     "config": {"id": "deepseek"}},
]

WEB_FETCH_PROVIDER_ENTRIES = [
    {"id": "web_fetch_provider_http", "plugin": "src.plugins.web_fetch_providers:apply_fetch_provider",
     "config": {"id": "http"}},
]

# ── 主题条目（每个内置主题一个独立插件条目） ────────────────

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
    {"id": "theme_tokyo_night", "plugin": "src.plugins.theme_entries:apply_theme",
     "config": {"name": "tokyo-night"}},
    {"id": "theme_catppuccin", "plugin": "src.plugins.theme_entries:apply_theme",
     "config": {"name": "catppuccin"}},
]

# ── 技能来源条目（每个内置来源一个独立插件条目） ────────────

SKILL_SOURCE_ENTRIES = [
    {"id": "skill_source_project", "plugin": "src.plugins.skill_source_entries:apply_skill_source",
     "config": {"id": "project"}},
    {"id": "skill_source_installed", "plugin": "src.plugins.skill_source_entries:apply_skill_source",
     "config": {"id": "installed"}},
]

# ── 会话投影条目（每个内置投影一个独立插件条目） ────────────

SESSION_PROJECTION_ENTRIES = [
    {"id": "session_projection_turn_boundary", "plugin": "src.plugins.session_projection_entries:apply_projection",
     "config": {"name": "turnBoundary"}},
]

__all__ = [
    "INVARIANT_ENTRIES",
    "TOOL_PLUGIN_ENTRIES",
    "TOOL_METADATA_ENTRIES",
    "TOOL_CONST_ENTRIES",
    "EVENT_TYPE_ENTRIES",
    "NAMED_STYLE_ENTRIES",
    "GLOBAL_DISABLED_TOOL_ENTRIES",
    "COMMAND_PLUGIN_ENTRIES",
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
    "RUNTIME_DATA_ENTRIES",
    "WEB_SEARCH_PROVIDER_ENTRIES",
    "WEB_FETCH_PROVIDER_ENTRIES",
    "THEME_ENTRIES",
    "SKILL_SOURCE_ENTRIES",
    "SESSION_PROJECTION_ENTRIES",
]
