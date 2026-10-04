"""内置插件清单 — Bundle / Profile / Patch 定义。

对应 DeepSeek Harness 的 Profile/Bundle/Patch 三层组装：
- **Bundle** 描述一组插件条目；
- **Profile** 选择 bundle 并叠加 Patch；
- **Patch** 按插件 id（``<bundle>::<id>``）覆盖配置或禁用。

内置 profile：

- ``cli``      完整终端应用（内核 + 工具 + 技能 + MCP + 模型 + 会话 +
                Agent 循环 + 命令 + 事件 + UI/渲染器 + 策略）；
- ``headless`` 无 UI 的后端运行时；
- ``minimal``  最小内核（配置 + 事件 + 工具 + 模型）；
- ``full``     等同 cli（语义化别名，便于扩展）。
"""

from __future__ import annotations

from ..kernel.config_tree import ConfigTree

# ── Bundle 定义 ─────────────────────────────────────────────

BUNDLES = [
    {
        "id": "core",
        "description": "基础层：配置、事件、提词、策略、工具、技能",
        "plugins": [
            {"id": "config", "plugin": "src.plugins.config"},
            {"id": "events", "plugin": "src.plugins.events"},
            {"id": "prompt", "plugin": "src.plugins.prompt", "config": {"empty_mode": True}},
            {"id": "fs", "plugin": "src.plugins.seams:apply_fs"},
            {"id": "subprocess", "plugin": "src.plugins.seams:apply_subprocess"},
            {"id": "shell", "plugin": "src.plugins.seams:apply_shell"},
            {"id": "terminals", "plugin": "src.plugins.seams:apply_terminals"},
            {"id": "jobs", "plugin": "src.plugins.seams:apply_jobs"},
            {"id": "sandbox", "plugin": "src.plugins.seams:apply_sandbox"},
            {"id": "policy", "plugin": "src.plugins.policy"},
            {"id": "agents", "plugin": "src.plugins.agents"},
            {"id": "session_log", "plugin": "src.plugins.session_log"},
            {"id": "session_projections", "plugin": "src.plugins.session_projections"},
            {"id": "tools", "plugin": "src.plugins.tools"},
            {"id": "tools_builtin", "plugin": "src.plugins.tools_builtin"},
            {"id": "seams", "plugin": "src.plugins.seams:apply_seams"},
            {"id": "skills", "plugin": "src.plugins.skills"},
            {"id": "presets", "plugin": "src.plugins.presets"},
            {"id": "invariants", "plugin": "src.plugins.invariants"},
        ],
    },
    {
        "id": "model",
        "description": "模型适配器（Provider 路由）",
        "plugins": [
            {"id": "llm", "plugin": "src.plugins.llm"},
        ],
    },
    {
        "id": "runtime",
        "description": "运行时：会话、Agent 循环、命令、MCP",
        "plugins": [
            {"id": "sessions", "plugin": "src.plugins.sessions"},
            {"id": "agent_loop", "plugin": "src.plugins.agent_loop"},
            {"id": "commands", "plugin": "src.plugins.commands"},
            {"id": "mcp", "plugin": "src.plugins.mcp"},
        ],
    },
    {
        "id": "presentation",
        "description": "表现层：UI、渲染器",
        "plugins": [
            {"id": "renderer", "plugin": "src.plugins.renderer"},
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
        "patches": [
            {"target": "core::tools", "config": {"discover": True}},
        ],
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


__all__ = ["BUNDLES", "PROFILES", "DEFAULT_PROFILE", "build_config_tree"]
