"""I/O 输出管理 — message_display 的输出目标管理职责。

包含 OutputManager 类、模块级实例、便捷写入函数及 I/O 适配器。
"""

from __future__ import annotations

import logging

from src._compat import dataclass
from ..core.output_target import IOutputTarget, TerminalTarget


# ── 输出目标管理器（封装模块级可变状态） ──

class OutputManager:
    """输出目标管理器 — 封装 IOutputTarget 的全局访问。

    消除模块级可变变量 + getter/setter 函数模式，
    提供统一写入接口。模块级实例供内部函数使用，
    get_message_output/set_message_output 保持向后兼容。
    """

    def __init__(self, target: IOutputTarget | None = None) -> None:
        self._target: IOutputTarget = target if target is not None else TerminalTarget()

    @property
    def target(self) -> IOutputTarget:
        return self._target

    @target.setter
    def target(self, new_target: IOutputTarget | None) -> None:
        self._target = new_target if new_target is not None else TerminalTarget()

    def write(self, text: str) -> None:
        self._target.write(text)

    def write_line(self, text: str = "") -> None:
        self._target.write_line(text)


# 模块级实例（保持向后兼容）
# 设计妥协：模块级可变状态违反「零模块级可变状态」架构原则，
# 但完全迁移为实例级依赖注入需改动所有调用方（message_editor 等），
# 当前提供 reset_message_output() 供测试隔离使用。
_manager: OutputManager = OutputManager()


def reset_message_output() -> None:
    """重置消息输出目标为默认 TerminalTarget（测试用）。"""
    _manager.target = TerminalTarget()


def get_message_output() -> IOutputTarget:
    """获取当前消息显示输出目标（动态解析，非 import 时固定值）。

    所有 display 函数通过此函数获取输出目标，确保 set_message_output()
    注入后所有调用方即时生效（消除 Python import 值绑定导致的引用僵死）。
    """
    return _manager.target


def set_message_output(target: IOutputTarget | None) -> None:
    """设置消息显示模块的输出目标（用于测试注入）。

    Args:
        target: 输出目标实例。None 时恢复默认 TerminalTarget。
    """
    _manager.target = target


# ── 模块级便捷写入函数（统一外部调用方访问路径） ─────

def write(text: str) -> None:
    """写入文本到消息显示输出。"""
    _manager.write(text)


def write_line(text: str = "") -> None:
    """写入一行文本到消息显示输出。"""
    _manager.write_line(text)


# ── I/O 注入适配器 ──────────────────────────────────────

@dataclass(slots=True)
class _OutputFileAdapter:
    """将 IOutputTarget 适配为 file-like 对象，供 IncrementalRenderer._file 注入。

    Rich Console 的 file 参数需要 write + flush 方法。
    此适配器将 IOutputTarget.write() 桥接到 file-like 协议，
    实现显示输出的 I/O 注入闭环。
    """
    _target: IOutputTarget

    def write(self, s: str) -> None:
        self._target.write(s)

    def flush(self) -> None:
        pass  # IOutputTarget 无 flush 概念

    def isatty(self) -> bool:
        return hasattr(self._target, 'isatty') and self._target.isatty()


__all__ = [
    "OutputManager",
    "_manager",
    "reset_message_output",
    "get_message_output",
    "set_message_output",
    "write",
    "write_line",
    "_OutputFileAdapter",
]
