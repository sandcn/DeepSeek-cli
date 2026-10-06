"""窗口输入注入能力（``bash_opt`` 的鼠标 / 键盘 / 文本 / 拖动 op 实现层）。

职责划分：

  - 本模块：平台后端注册表 + 公共入口 :func:`send_window_input`；
  - ``win`` / ``x11`` / ``macos``：各平台后端（相同契约：``supports()`` +
    ``send(pid, action) -> InputResult``）；
  - ``action``：与平台无关的输入动作模型与参数校验；
  - ``keys``：规范键名与各平台键码映射；
  - ``result``：结果类型与异常。

坐标语义：以窗口截图左上角为原点（与 ``bash_opt`` 的 ``op=screenshot``
产物一致），各后端按自己的窗口几何换算为屏幕坐标后再注入。

扩展方式：新增平台只需实现上述契约并 :func:`register_backend`（内置后端
按 Windows → macOS → X11 顺序探测），无需改动工具层与既有后端。
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable

from .action import (  # noqa: F401  # 对外导出动作模型
    INPUT_OPS,
    ClickAction,
    DragAction,
    InputAction,
    KeyAction,
    MoveAction,
    ScrollAction,
    TextAction,
    build_action,
    describe_action,
    resolve_point,
)
from .result import ActionError, InputError, InputResult, NoWindowError

logger = logging.getLogger(__name__)

_BACKENDS: list = []
_LOCK = threading.RLock()
_BUILTINS_LOADED = False

#: 动作类型联合（运行时校验用）
_ACTION_TYPES = (MoveAction, ClickAction, DragAction, ScrollAction,
                 KeyAction, TextAction)


def register_backend(backend, *, prepend: bool = False) -> Callable[[], None]:
    """注册平台输入后端，返回幂等撤销函数。

    Args:
        backend: 需实现 ``name`` / ``supports()`` / ``send(pid, action)``。
        prepend: 是否插入到探测序列最前（自定义实现覆盖内置时使用）。
    """
    with _LOCK:
        if prepend:
            _BACKENDS.insert(0, backend)
        else:
            _BACKENDS.append(backend)

        def _undo() -> None:
            with _LOCK:
                if backend in _BACKENDS:
                    _BACKENDS.remove(backend)

        return _undo


def available_backends() -> list:
    """当前已注册的后端（含不支持当前平台的，按探测顺序）。"""
    _ensure_builtins()
    with _LOCK:
        return list(_BACKENDS)


def resolve_backend():
    """返回当前平台上第一个可用的后端（无则 None）。"""
    for backend in available_backends():
        try:
            if backend.supports():
                return backend
        except Exception:  # pragma: no cover - 后端探测异常不应中断
            logger.debug("输入后端 %s 探测失败", getattr(backend, "name", backend),
                         exc_info=True)
    return None


def send_window_input(pid: int, action: InputAction) -> InputResult:
    """向 ``pid`` 的窗口注入一个输入动作，返回注入结果。

    Args:
        pid: 目标进程 PID（含其子进程一起参与窗口匹配）。
        action: 由 :func:`build_action` 构建的动作对象。

    Raises:
        ActionError: 参数非法（进程号、动作类型）。
        NoWindowError: 目标进程没有可接收输入的窗口。
        InputError: 平台不支持、平台工具缺失或注入失败。
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ActionError(f"进程号非法，无法注入输入: {pid!r}")
    if not isinstance(action, _ACTION_TYPES):
        raise ActionError(f"输入动作类型非法: {type(action).__name__}")
    backend = resolve_backend()
    if backend is None:
        raise InputError(
            f"当前平台（{sys.platform}）没有可用的窗口输入后端（支持 Windows / "
            f"macOS / Linux X11）"
        )
    try:
        return backend.send(pid, action)
    except InputError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise InputError(
            f"输入注入失败（后端 {getattr(backend, 'name', '?')}，进程 {pid}）: {exc}"
        ) from exc


def _ensure_builtins() -> None:
    """幂等注册内置后端（延迟导入，避免包初始化期的循环引用）。"""
    global _BUILTINS_LOADED
    with _LOCK:
        if _BUILTINS_LOADED:
            return
        _BUILTINS_LOADED = True
    from .macos import MacOSInputBackend
    from .win import WindowsInputBackend
    from .x11 import X11InputBackend

    for backend in (WindowsInputBackend(), MacOSInputBackend(), X11InputBackend()):
        register_backend(backend)


__all__ = [
    "ActionError",
    "ClickAction",
    "DragAction",
    "INPUT_OPS",
    "InputAction",
    "InputError",
    "InputResult",
    "KeyAction",
    "MoveAction",
    "NoWindowError",
    "ScrollAction",
    "TextAction",
    "available_backends",
    "build_action",
    "describe_action",
    "register_backend",
    "resolve_backend",
    "resolve_point",
    "send_window_input",
]
