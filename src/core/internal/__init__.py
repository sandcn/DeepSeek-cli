"""core/internal — 内部实现模块（按领域分组）

子包结构：
- session/ — 会话消息管理、状态、持久化、压缩
- agent/ — 工具回调、子代理生成、输出捕获
- commands/ — 命令注册表
- shared/ — 缓存、沙盒历史

【架构】此目录为纯实现细节。外部代码统一通过 src.core.internal 子包入口导入。

★ 启动性能 + 循环导入修复（懒导出）：本 ``__init__`` 不再 eager 导入
``.session`` / ``.agent`` / ``.commands`` / ``.shared`` 四个子包。原实现的问题：

1. **导入代价**：任何 ``import src.core.internal.<submodule>``（如
   ``src.core.sandbox_manager`` → ``.internal.shared._sandbox_history``）都会
   先执行本文件，从而加载 commands 注册表、agent 实现等整条重链。
2. **循环导入**：``src.core.base_agent`` → ``.sandbox_manager`` →
   ``.internal.shared._sandbox_history`` → 本 ``__init__`` → ``.agent`` →
   ``._subagent_spawner`` → ``src.core.subagent`` → ``.base_agent``（尚未完成
   初始化）→ ``ImportError: partially initialized module``。原实现靠「先由
   ``src.core.agent_builder`` 完成 ``base_agent`` 加载」的导入顺序侥幸避开。

改用 PEP 562 模块级 ``__getattr__`` 惰性解析：四个子包按需加载，导入顺序
不再影响正确性（消除隐性顺序契约），同时缩短冷启动路径。
"""

from __future__ import annotations

from importlib import import_module

#: 惰性导出表：公开名 → (子模块相对名, 子模块内属性名)
_LAZY_EXPORTS = {
    # ── session 子包 ──
    "add_message": (".session", "add_message"),
    "non_system_messages": (".session", "non_system_messages"),
    "system_messages": (".session", "system_messages"),
    "CoreEventBus": (".session", "CoreEventBus"),
    "SessionState": (".session", "SessionState"),
    "_validate_compress_preconditions": (".session", "_validate_compress_preconditions"),
    "SessionPersistenceManager": (".session", "SessionPersistenceManager"),
    "SessionMessagingManager": (".session", "SessionMessagingManager"),
    # ── agent 子包 ──
    "ToolCallbackChain": (".agent", "ToolCallbackChain"),
    "SubAgentSpawner": (".agent", "SubAgentSpawner"),
    "CaptureManager": (".agent", "CaptureManager"),
    # ── commands 子包 ──
    "register_command": (".commands._command_core", "register_command"),
    "handle_command": (".commands._command_core", "handle_command"),
    "CommandContext": (".commands._command_core", "CommandContext"),
    "get_registered_command_names": (".commands._command_core", "get_registered_command_names"),
    "COMMANDS_HELP": (".commands._command_core", "COMMANDS_HELP"),
    "_commands": (".commands._command_core", "_commands"),
    "_format_cost_duration": (".commands._command_core", "_format_cost_duration"),
    "show_cost": (".commands._command_core", "show_cost"),
    "compute_cost": (".commands._command_core", "compute_cost"),
    "_pop_assistant_tool_messages": (".commands._command_core", "_pop_assistant_tool_messages"),
    "_cmd_help": (".commands._command_core", "_cmd_help"),
    "get_dynamic_help_text": (".commands._command_core", "get_dynamic_help_text"),
    # ── shared 子包 ──
    "MessageStatsCache": (".shared", "MessageStatsCache"),
    "FileSnapshot": (".shared", "FileSnapshot"),
}

__all__ = [
    # session 子包
    "add_message", "non_system_messages", "system_messages",
    "CoreEventBus", "SessionState",
    "_validate_compress_preconditions",
    "SessionPersistenceManager", "SessionMessagingManager",
    # agent 子包
    "ToolCallbackChain", "SubAgentSpawner", "CaptureManager",
    # commands 子包
    "register_command", "handle_command",
    "CommandContext", "get_registered_command_names",
    "COMMANDS_HELP", "_commands",
    "_format_cost_duration", "show_cost", "compute_cost",
    "_pop_assistant_tool_messages", "_cmd_help",
    "get_dynamic_help_text",
    # shared 子包
    "MessageStatsCache", "FileSnapshot",
]


def __getattr__(name: str):
    """PEP 562 模块级惰性属性解析（首次访问后缓存到模块命名空间）。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
