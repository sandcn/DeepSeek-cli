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

from ..kernel.config_tree import ConfigTree

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
    {"id": "ui_view_user_select", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "user_select"}},
    {"id": "ui_view_editmsg", "plugin": "src.plugins.ui_views:apply_ui_view",
     "config": {"id": "editmsg"}},
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
        "id": "tools",
        "description": "内置工具：每个工具一个独立插件条目（可 patch/overlay 禁用/替换）",
        "plugins": list(TOOL_PLUGIN_ENTRIES),
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
        "id": "core",
        "description": "基础层：配置、事件、提词、策略、工具、技能、通知后端",
        "includes": ["tools", "commands", "notification_backends", "context_strategies", "runtime_data", "web", "skill_sources"],
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
            {"id": "presets", "plugin": "src.plugins.presets"},
            {"id": "invariants", "plugin": "src.plugins.invariants"},
            {"id": "app", "plugin": "src.plugins.app"},
            {"id": "escape_monitor", "plugin": "src.plugins.escape_monitor"},
        ],
    },
    {
        "id": "model",
        "description": "模型适配器（Provider 路由 + 内置 Provider 插件）",
        "plugins": [
            {"id": "llm", "plugin": "src.plugins.llm"},
            {"id": "llm_provider_deepseek", "plugin": "src.plugins.llm_providers:apply_deepseek"},
            {"id": "llm_provider_anthropic", "plugin": "src.plugins.llm_providers:apply_anthropic"},
            {"id": "llm_provider_ollama", "plugin": "src.plugins.llm_providers:apply_ollama"},
            {"id": "llm_provider_openai_compat", "plugin": "src.plugins.llm_providers:apply_openai_compat"},
        ],
    },
    {
        "id": "runtime",
        "description": "运行时：会话、SubAgent、流式管线、Agent 循环、命令、MCP",
        "includes": ["middleware", "agent_types", "stream", "mcp_transports", "tool_engines"],
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
            {"id": "clawbot", "plugin": "src.plugins.clawbot"},
        ],
    },
    {
        "id": "presentation",
        "description": "表现层：UI、渲染器、事件消费者",
        "includes": ["renderer_ext", "consumers", "ui_views", "themes"],
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
    "TOOL_PLUGIN_ENTRIES",
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
    "WEB_SEARCH_PROVIDER_ENTRIES",
    "WEB_FETCH_PROVIDER_ENTRIES",
    "THEME_ENTRIES",
    "SKILL_SOURCE_ENTRIES",
    "SESSION_PROJECTION_ENTRIES",
    "RENDERER_TARGET_ENTRIES",
    "build_config_tree",
]
