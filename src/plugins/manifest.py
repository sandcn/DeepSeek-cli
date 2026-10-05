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
        "id": "core",
        "description": "基础层：配置、事件、提词、策略、工具、技能",
        "includes": ["tools", "commands"],
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
            {"id": "tools", "plugin": "src.plugins.tools"},
            {"id": "tools_builtin", "plugin": "src.plugins.tools_builtin"},
            {"id": "context", "plugin": "src.plugins.context"},
            {"id": "seams", "plugin": "src.plugins.seams:apply_seams"},
            {"id": "skills", "plugin": "src.plugins.skills"},
            {"id": "presets", "plugin": "src.plugins.presets"},
            {"id": "invariants", "plugin": "src.plugins.invariants"},
            {"id": "app", "plugin": "src.plugins.app"},
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
        "description": "运行时：会话、Agent 循环、命令、MCP",
        "plugins": [
            {"id": "sessions", "plugin": "src.plugins.sessions"},
            {"id": "tool_scheduler", "plugin": "src.plugins.tool_scheduler"},
            {"id": "agent_middleware", "plugin": "src.plugins.agent_middleware"},
            {"id": "agent_loop", "plugin": "src.plugins.agent_loop"},
            {"id": "commands", "plugin": "src.plugins.commands"},
            {"id": "mcp", "plugin": "src.plugins.mcp"},
            {"id": "clawbot", "plugin": "src.plugins.clawbot"},
        ],
    },
    {
        "id": "presentation",
        "description": "表现层：UI、渲染器",
        "plugins": [
            {"id": "renderer", "plugin": "src.plugins.renderer"},
            {"id": "renderer_builtin", "plugin": "src.plugins.renderer_builtin"},
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
    "build_config_tree",
]
