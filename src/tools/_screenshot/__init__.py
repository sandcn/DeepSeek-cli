"""进程窗口截图能力（``bash_opt`` 的 ``op=screenshot`` 实现层）。

职责划分：
  - 本模块：平台后端注册表 + 公共入口 :func:`capture_process_window`；
  - ``win`` / ``x11`` / ``macos``：各平台后端（相同契约：``supports()`` +
    ``capture(pid, path) -> CaptureResult``）；
  - ``proctree``：跨平台进程树收集（目标进程 + 子进程）；
  - ``png``：零依赖 PNG 编码（截图落盘）。

扩展方式：新增平台只需实现上述契约并 :func:`register_backend`（内置后端
按 Windows → macOS → X11 顺序探测），无需改动工具层与既有后端。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Callable

from .result import CaptureResult, NoWindowError, ScreenshotError

logger = logging.getLogger(__name__)

_BACKENDS: list = []
_LOCK = threading.RLock()
_BUILTINS_LOADED = False


def register_backend(backend, *, prepend: bool = False) -> Callable[[], None]:
    """注册平台截图后端，返回幂等撤销函数。

    Args:
        backend: 需实现 ``name`` / ``supports()`` / ``capture(pid, path)``。
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
            logger.debug("后端 %s 探测失败", getattr(backend, "name", backend), exc_info=True)
    return None


def capture_process_window(pid: int, path: str) -> CaptureResult:
    """截取进程窗口并写入 ``path``（PNG），返回截图结果。

    Args:
        pid: 目标进程 PID（含其子进程一起参与窗口匹配）。
        path: 输出文件路径（调用方已确保目录可用）。

    Raises:
        ScreenshotError: 平台不支持、进程号非法、无窗口、抓取或落盘失败。
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ScreenshotError(f"进程号非法，无法截图: {pid!r}")
    backend = resolve_backend()
    if backend is None:
        raise ScreenshotError(
            f"当前平台（{sys.platform}）没有可用的截图后端（支持 Windows / "
            f"macOS / Linux X11）"
        )
    try:
        result = backend.capture(pid, path)
    except ScreenshotError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise ScreenshotError(
            f"截图失败（后端 {getattr(backend, 'name', '?')}，进程 {pid}）: {exc}"
        ) from exc
    if not os.path.exists(result.path):
        raise ScreenshotError(f"截图命令已执行但未生成文件: {result.path}")
    return result


def _ensure_builtins() -> None:
    """幂等注册内置后端（延迟导入，避免包初始化期的循环引用）。"""
    global _BUILTINS_LOADED
    with _LOCK:
        if _BUILTINS_LOADED:
            return
        _BUILTINS_LOADED = True
    from .macos import MacOSBackend
    from .win import WindowsBackend
    from .x11 import X11Backend

    for backend in (WindowsBackend(), MacOSBackend(), X11Backend()):
        register_backend(backend)


__all__ = [
    "CaptureResult",
    "NoWindowError",
    "ScreenshotError",
    "available_backends",
    "capture_process_window",
    "register_backend",
    "resolve_backend",
]
