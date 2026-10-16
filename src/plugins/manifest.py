"""内置插件清单 — Bundle / Profile 组装与 ConfigTree 构建。

对应 DeepSeek Harness 的 Profile/Bundle/Patch 三层组装：
- **Bundle** 描述一组插件条目；
- **Profile** 选择 bundle 并叠加 Patch；
- **Patch** 按插件 id（``<bundle>::<id>``）覆盖配置或禁用。

「一切皆插件」：每个内置工具、命令、渲染 handler/filter、Agent 中间件都是
清单中的**独立插件条目**——可被 Profile/Bundle 声明，也可被 Patch/Overlay
按 id 覆盖配置、禁用或替换，而非隐藏在某处的一次性自动注册副作用里。

**条目声明与组装分离**：全部 ``*_ENTRIES`` 条目定义已拆至
``src.plugins.manifest_entries``（该文件曾把 40 余组条目内联为 900+ 行字面量，
新增条目时冲突与漏改风险高）；本模块只负责 Bundle/Profile 组装与
``build_config_tree()``，并按名 re-export 全部条目以保持既有导入路径兼容。

内置 profile：

- ``cli``      完整终端应用（内核 + 工具 + 技能 + MCP + 模型 + 会话 +
                Agent 循环 + 命令 + 事件 + UI/渲染器 + 策略）；
- ``headless`` 无 UI 的后端运行时；
- ``minimal``  最小内核（配置 + 事件 + 工具 + 模型）；
- ``full``     等同 cli（语义化别名，便于扩展）。
"""

from __future__ import annotations

from ..kernel.config_tree import ConfigTree
from .manifest_entries import (
    AGENT_TYPE_ENTRIES,
    CLAWBOT_COMMAND_ENTRIES,
    COMMAND_PLUGIN_ENTRIES,
    COMPLETION_PROVIDER_ENTRIES,
    CONSUMER_ENTRIES,
    CONTEXT_STRATEGY_ENTRIES,
    EVENT_TYPE_ENTRIES,
    GLOBAL_DISABLED_TOOL_ENTRIES,
    HOST_ENTRIES,
    INVARIANT_ENTRIES,
    KEYBINDING_ENTRIES,
    MCP_TRANSPORT_ENTRIES,
    MIDDLEWARE_ENTRIES,
    NAMED_STYLE_ENTRIES,
    NOTIFICATION_BACKEND_ENTRIES,
    PRESENTATION_DATA_ENTRIES,
    PRESET_ENTRIES,
    PROMPT_MODE_ENTRIES,
    PROMPT_SECTION_ENTRIES,
    PROMPT_SOURCE_ENTRIES,
    RENDERER_FILTER_ENTRIES,
    RENDERER_HANDLER_ENTRIES,
    RENDERER_TARGET_ENTRIES,
    RUNTIME_DATA_ENTRIES,
    SESSION_PROJECTION_ENTRIES,
    SKILL_SOURCE_ENTRIES,
    SPECIAL_KEY_ENTRIES,
    STATUS_SEGMENT_ENTRIES,
    STREAM_HANDLER_ENTRIES,
    SUBCOMMAND_ENTRIES,
    SYNTAX_LANGUAGE_ENTRIES,
    THEME_ENTRIES,
    TOOL_CONST_ENTRIES,
    TOOL_ENGINE_ENTRIES,
    TOOL_METADATA_ENTRIES,
    TOOL_PLUGIN_ENTRIES,
    TOOL_STYLE_ENTRIES,
    UI_VIEW_ENTRIES,
    WEB_FETCH_PROVIDER_ENTRIES,
    WEB_SEARCH_PROVIDER_ENTRIES,
)

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
    "build_config_tree",
    # 条目 re-export（实现位于 src.plugins.manifest_entries，保持旧导入路径兼容）
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
    "RENDERER_TARGET_ENTRIES",
]
