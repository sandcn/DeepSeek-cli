"""CLI 子命令注册表 — 每个顶层子命令的单一来源（一切皆插件）。

``chat.py`` 的顶层子命令（``version`` / ``dump-config`` / ``plugin`` /
``session`` / ``config`` / ``check-invariants`` / ``clawbot``）不再硬编码在
``AppService._dispatch`` 的 if-else 里，而是由本模块**声明**，并由清单中的
**独立插件条目**（``subcommand``，经 ``src.plugins.subcommand_entries``）显式
注册——因此可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、覆盖
或替换。

每个子命令声明：

- ``matcher(args) -> bool``：判定本次调用是否命中该子命令；
- ``handler(app, args)``：异步执行体（``app`` 为 ``ctx.app`` 服务）；
- ``phase``：``pre``（MCP 初始化之前）/ ``post``（MCP 初始化之后）——
  保持既有分派时机语义；
- ``order``：同阶段内的判定顺序。

**清单接管**：``app`` 聚合插件收到组合根注入的 ``managed_subcommands``（清单已
接管的 id，含被禁用的）时经 ``set_managed_builtin_subcommands`` 声明这些 id 由
清单条目负责；无清单（单元测试、独立调用）时无接管，全部内置子命令默认生效。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

_lock = threading.RLock()
_ABSENT = object()

#: 阶段常量
PHASE_PRE = "pre"
PHASE_POST = "post"


@dataclass(frozen=True)
class Subcommand:
    """一个顶层 CLI 子命令。"""

    name: str
    description: str = ""
    phase: str = PHASE_PRE
    order: int = 0
    matcher: Optional[Callable[..., bool]] = field(default=None, compare=False)
    handler: Optional[Callable[..., Any]] = field(default=None, compare=False)
    hidden: bool = False

    def matches(self, args) -> bool:
        if self.matcher is None:
            return False
        try:
            return bool(self.matcher(args))
        except Exception:
            return False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "phase": self.phase,
            "order": self.order,
            "hidden": self.hidden,
        }


# ── 内置子命令处理器 ─────────────────────────────────────────


def _match_flag_or_command(flag: str, command: str):
    def _matcher(args) -> bool:
        return bool(getattr(args, flag, False) or getattr(args, "command", None) == command)

    return _matcher


def _match_command(command: str):
    def _matcher(args) -> bool:
        return getattr(args, "command", None) == command

    return _matcher


async def _handle_version(app, args) -> None:
    from ..tui.events.consumers import publish_output
    from ._args import VERSION

    publish_output(f"  Chat {VERSION}", level="raw")


async def _handle_dump_config(app, args) -> None:
    await app._dump_config(args)


async def _handle_plugin(app, args) -> None:
    from ._plugin_cmd import _handle_plugin_command

    _handle_plugin_command(args)


async def _handle_session(app, args) -> None:
    from ._session_cmd import _handle_session_command

    _handle_session_command(args)


async def _handle_config(app, args) -> None:
    from ._config_cmd import _handle_config_command

    _handle_config_command(args)


async def _handle_check_invariants(app, args) -> None:
    app._check_invariants()


async def _handle_clawbot(app, args) -> None:
    await app._run_clawbot(args)


#: 内置子命令声明（id → 规格）——每项由清单中的独立插件条目注册。
_BUILTIN_SUBCOMMAND_SPECS: Dict[str, Subcommand] = {
    "version": Subcommand(
        "version", "显示版本信息", phase=PHASE_PRE, order=0,
        matcher=_match_flag_or_command("version", "version"), handler=_handle_version,
    ),
    "dump-config": Subcommand(
        "dump-config", "打印最终运行时插件配置", phase=PHASE_PRE, order=1,
        matcher=_match_flag_or_command("dump_config", "dump-config"), handler=_handle_dump_config,
    ),
    "plugin": Subcommand(
        "plugin", "插件发现 / 安装 / 卸载", phase=PHASE_PRE, order=2,
        matcher=_match_command("plugin"), handler=_handle_plugin,
    ),
    "session": Subcommand(
        "session", "会话列举 / 删除 / 导出", phase=PHASE_PRE, order=3,
        matcher=_match_command("session"), handler=_handle_session,
    ),
    "config": Subcommand(
        "config", "配置查看 / 设置 / 重置", phase=PHASE_PRE, order=4,
        matcher=_match_command("config"), handler=_handle_config,
    ),
    "check-invariants": Subcommand(
        "check-invariants", "运行时不变量检查", phase=PHASE_POST, order=0,
        matcher=lambda args: bool(getattr(args, "check_invariants", False)),
        handler=_handle_check_invariants,
    ),
    "clawbot": Subcommand(
        "clawbot", "微信 ClawBot 远程控制", phase=PHASE_POST, order=1,
        matcher=_match_command("clawbot"), handler=_handle_clawbot,
    ),
}

_registered_builtin: Dict[str, Subcommand] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, Subcommand] = {}


def builtin_subcommand_ids() -> List[str]:
    """全部内置子命令 id（含被接管/禁用的，按声明顺序）。"""
    return list(_BUILTIN_SUBCOMMAND_SPECS)


def _normalize_names(names) -> List[str]:
    if isinstance(names, str):
        names = [names]
    selected: List[str] = []
    for item in names or ():
        if item not in _BUILTIN_SUBCOMMAND_SPECS:
            raise KeyError(
                f"未知内置子命令: {item!r}（可用: {list(_BUILTIN_SUBCOMMAND_SPECS)}）"
            )
        selected.append(item)
    return selected


def default_subcommand(name: str) -> Subcommand:
    try:
        return _BUILTIN_SUBCOMMAND_SPECS[name]
    except KeyError:
        raise KeyError(
            f"未知内置子命令: {name!r}（可用: {list(_BUILTIN_SUBCOMMAND_SPECS)}）"
        ) from None


def active_subcommands() -> Dict[str, Subcommand]:
    """当前生效的子命令（内置装配 + 扩展），按名字。"""
    with _lock:
        result: Dict[str, Subcommand] = {}
        for name, default in _BUILTIN_SUBCOMMAND_SPECS.items():
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


def subcommands_for_phase(phase: str) -> List[Subcommand]:
    """某阶段当前生效的子命令（按 ``order`` 排序）。"""
    commands = [cmd for cmd in active_subcommands().values() if cmd.phase == phase]
    commands.sort(key=lambda cmd: (cmd.order, cmd.name))
    return commands


def resolve_subcommand(name: str) -> Optional[Subcommand]:
    return active_subcommands().get(name)


def register_builtin_subcommand(name: str, command: Optional[Subcommand] = None) -> Callable[[], None]:
    """注册/覆盖一个内置子命令（``command=None`` 用默认声明）；返回幂等撤销。"""
    if name not in _BUILTIN_SUBCOMMAND_SPECS:
        raise KeyError(
            f"未知内置子命令: {name!r}（可用: {list(_BUILTIN_SUBCOMMAND_SPECS)}）"
        )
    effective = command if command is not None else _BUILTIN_SUBCOMMAND_SPECS[name]
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


def unregister_builtin_subcommand(name: str) -> bool:
    with _lock:
        return _registered_builtin.pop(name, None) is not None


def set_managed_builtin_subcommands(names) -> Callable[[], None]:
    """声明这些内置子命令 id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_names(names)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_subcommand_ids() -> List[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_subcommands(names) -> Callable[[], None]:
    """禁用一个或多个内置子命令（返回幂等撤销）。"""
    selected = _normalize_names(names)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_subcommand(command: Subcommand) -> Callable[[], None]:
    """注册一个扩展子命令（返回幂等撤销）。"""
    if not isinstance(command, Subcommand):
        raise TypeError(f"子命令规格非法: {command!r}")
    if not command.name:
        raise ValueError("子命令名必须是非空字符串")
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


def unregister_subcommand(name: str) -> bool:
    with _lock:
        return _extension.pop(name, None) is not None


def clear() -> None:
    """清空扩展子命令与清单注册（测试用；不影响内置默认与禁用状态）。"""
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
    "Subcommand",
    "PHASE_PRE",
    "PHASE_POST",
    "builtin_subcommand_ids",
    "default_subcommand",
    "active_subcommands",
    "subcommands_for_phase",
    "resolve_subcommand",
    "register_builtin_subcommand",
    "unregister_builtin_subcommand",
    "set_managed_builtin_subcommands",
    "managed_subcommand_ids",
    "disable_builtin_subcommands",
    "register_subcommand",
    "unregister_subcommand",
    "clear",
    "reset",
]
