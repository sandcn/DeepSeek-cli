"""ClawBot 远程命令注册表 — 每个斜杠指令的单一来源（一切皆插件）。

微信远程控制的斜杠指令（``/help`` / ``/shell`` / ``/clear`` / ``/new`` /
``/time`` / ``/status`` / ``/model`` / ``/stop``）不再硬编码在
``runner._dispatch_cmd`` 的 if-else 里，而是由本模块**声明**，并由清单中的
**独立插件条目**（``clawbot_command``，经 ``src.plugins.clawbot_commands``）
显式注册——因此可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、
覆盖或替换。

**清单接管**：``clawbot`` 聚合插件收到组合根注入的 ``managed_clawbot_commands``
（清单已接管的 id，含被禁用的）时经 ``set_managed_builtin_clawbot_commands``
声明这些 id 由清单条目负责；无清单（单元测试、独立调用）时无接管，全部内置
命令默认生效。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

_lock = threading.RLock()
_ABSENT = object()

#: 帮助文本标题（动态帮助行的表头）
HELP_HEADER = (
    "🤖 DeepSeek ClawBot 远程控制\n"
    "────────────────────────\n"
    "发送普通消息 → AI 对话（自动调用工具）\n"
    "\n"
    "可用指令："
)


@dataclass(frozen=True)
class ClawbotCommand:
    """一个微信远程指令。"""

    name: str
    usage: str = ""
    description: str = ""
    aliases: Tuple[str, ...] = ()
    order: int = 0
    hidden: bool = False
    handler: Optional[Callable[..., Any]] = field(default=None, compare=False)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "usage": self.usage or f"/{self.name}",
            "description": self.description,
            "aliases": list(self.aliases),
            "order": self.order,
            "hidden": self.hidden,
        }


# ── 内置命令处理器（接收 runner 作为第一参数） ────────────────


async def _cmd_help(runner, from_id: str, ctx: str, arg: str) -> None:
    await runner._send(from_id, ctx, build_help_text())


async def _cmd_shell(runner, from_id: str, ctx: str, arg: str) -> None:
    await runner._cmd_shell(from_id, ctx, arg)


async def _cmd_clear(runner, from_id: str, ctx: str, arg: str) -> None:
    await runner._cmd_clear(from_id, ctx)


async def _cmd_new(runner, from_id: str, ctx: str, arg: str) -> None:
    await runner._cmd_new(from_id, ctx)


async def _cmd_time(runner, from_id: str, ctx: str, arg: str) -> None:
    await runner._cmd_time(from_id, ctx)


async def _cmd_status(runner, from_id: str, ctx: str, arg: str) -> None:
    await runner._cmd_status(from_id, ctx)


async def _cmd_model(runner, from_id: str, ctx: str, arg: str) -> None:
    await runner._cmd_model(from_id, ctx, arg)


async def _cmd_stop(runner, from_id: str, ctx: str, arg: str) -> None:
    await runner._cmd_stop(from_id, ctx)


#: 内置命令声明（id → 规格）——每项由清单中的独立插件条目注册。
_BUILTIN_COMMAND_SPECS: Dict[str, ClawbotCommand] = {
    "help": ClawbotCommand(
        "help", usage="/help", description="显示本帮助", order=0, handler=_cmd_help,
    ),
    "shell": ClawbotCommand(
        "shell", usage="/shell <命令>", description="远程执行 shell 命令", order=1,
        handler=_cmd_shell,
    ),
    "clear": ClawbotCommand(
        "clear", usage="/clear", description="清空当前会话上下文", order=2, handler=_cmd_clear,
    ),
    "new": ClawbotCommand(
        "new", usage="/new", description="开始新会话", order=3, handler=_cmd_new,
    ),
    "time": ClawbotCommand(
        "time", usage="/time", description="显示连接剩余时间", order=4, handler=_cmd_time,
    ),
    "status": ClawbotCommand(
        "status", usage="/status", description="显示模型与会话状态", order=5, handler=_cmd_status,
    ),
    "model": ClawbotCommand(
        "model", usage="/model <名称>", description="切换模型", order=6, handler=_cmd_model,
    ),
    "stop": ClawbotCommand(
        "stop", usage="/stop", description="停止当前 AI 生成（流式输出）", order=7,
        handler=_cmd_stop,
    ),
}

_registered_builtin: Dict[str, ClawbotCommand] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, ClawbotCommand] = {}


def builtin_clawbot_command_ids() -> List[str]:
    """全部内置命令 id（含被接管/禁用的，按声明顺序）。"""
    return list(_BUILTIN_COMMAND_SPECS)


def _normalize_names(names) -> List[str]:
    if isinstance(names, str):
        names = [names]
    selected: List[str] = []
    for item in names or ():
        if item not in _BUILTIN_COMMAND_SPECS:
            raise KeyError(
                f"未知内置 ClawBot 命令: {item!r}（可用: {list(_BUILTIN_COMMAND_SPECS)}）"
            )
        selected.append(item)
    return selected


def default_clawbot_command(name: str) -> ClawbotCommand:
    try:
        return _BUILTIN_COMMAND_SPECS[name]
    except KeyError:
        raise KeyError(
            f"未知内置 ClawBot 命令: {name!r}（可用: {list(_BUILTIN_COMMAND_SPECS)}）"
        ) from None


def active_clawbot_commands() -> Dict[str, ClawbotCommand]:
    """当前生效的命令（内置装配 + 扩展），按名字。"""
    with _lock:
        result: Dict[str, ClawbotCommand] = {}
        for name, default in _BUILTIN_COMMAND_SPECS.items():
            if name in _disabled_builtin:
                continue
            override = _registered_builtin.get(name)
            if override is not None:
                result[name] = override
                continue
            if name in _managed_builtin:
                continue
            result[name] = default
        result.update(_extension)
        return result


def resolve_clawbot_command(name: str) -> Optional[ClawbotCommand]:
    """按名或别名解析命令（未知返回 None）。"""
    if not name:
        return None
    key = str(name).lstrip("/").lower()
    commands = active_clawbot_commands()
    command = commands.get(key)
    if command is not None:
        return command
    for candidate in commands.values():
        if key in candidate.aliases:
            return candidate
    return None


def register_builtin_clawbot_command(
    name: str, command: Optional[ClawbotCommand] = None
) -> Callable[[], None]:
    """注册/覆盖一个内置命令（``command=None`` 用默认声明）；返回幂等撤销。"""
    if name not in _BUILTIN_COMMAND_SPECS:
        raise KeyError(
            f"未知内置 ClawBot 命令: {name!r}（可用: {list(_BUILTIN_COMMAND_SPECS)}）"
        )
    effective = command if command is not None else _BUILTIN_COMMAND_SPECS[name]
    with _lock:
        previous = _registered_builtin.get(name, _ABSENT)
        _registered_builtin[name] = effective

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(name, None)
            else:
                _registered_builtin[name] = previous

    return _undo


def unregister_builtin_clawbot_command(name: str) -> bool:
    with _lock:
        return _registered_builtin.pop(name, None) is not None


def set_managed_builtin_clawbot_commands(names) -> Callable[[], None]:
    """声明这些内置命令 id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_names(names)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_clawbot_command_ids() -> List[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_clawbot_commands(names) -> Callable[[], None]:
    """禁用一个或多个内置命令（返回幂等撤销）。"""
    selected = _normalize_names(names)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_clawbot_command(command: ClawbotCommand) -> Callable[[], None]:
    """注册一个扩展命令（返回幂等撤销）。"""
    if not isinstance(command, ClawbotCommand):
        raise TypeError(f"命令规格非法: {command!r}")
    if not command.name:
        raise ValueError("命令名必须是非空字符串")
    with _lock:
        previous = _extension.get(command.name, _ABSENT)
        _extension[command.name] = command

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension.pop(command.name, None)
            else:
                _extension[command.name] = previous

    return _undo


def unregister_clawbot_command(name: str) -> bool:
    with _lock:
        return _extension.pop(name, None) is not None


def command_names() -> List[str]:
    return sorted(active_clawbot_commands())


def build_help_text() -> str:
    """按当前生效命令生成帮助文本（注册表为空时回退静态 HELP_TEXT）。"""
    commands = active_clawbot_commands()
    visible = [cmd for cmd in commands.values() if not cmd.hidden]
    if not visible:
        from .commands import HELP_TEXT

        return HELP_TEXT
    visible.sort(key=lambda cmd: (cmd.order, cmd.name))
    lines = [HELP_HEADER]
    for cmd in visible:
        usage = cmd.usage or f"/{cmd.name}"
        lines.append(f"{usage:<14}{cmd.description}")
    return "\n".join(lines)


def clear() -> None:
    """清空扩展命令与清单注册（测试用；不影响内置默认与禁用状态）。"""
    with _lock:
        _extension.clear()
        _registered_builtin.clear()


def reset() -> None:
    """重置全部状态到「无清单、无禁用、全部默认」（测试隔离用）。"""
    with _lock:
        _extension.clear()
        _registered_builtin.clear()
        _managed_builtin.clear()
        _disabled_builtin.clear()


__all__ = [
    "ClawbotCommand",
    "HELP_HEADER",
    "builtin_clawbot_command_ids",
    "default_clawbot_command",
    "active_clawbot_commands",
    "resolve_clawbot_command",
    "register_builtin_clawbot_command",
    "unregister_builtin_clawbot_command",
    "set_managed_builtin_clawbot_commands",
    "managed_clawbot_command_ids",
    "disable_builtin_clawbot_commands",
    "register_clawbot_command",
    "unregister_clawbot_command",
    "command_names",
    "build_help_text",
    "clear",
    "reset",
]
