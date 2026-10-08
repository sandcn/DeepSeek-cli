"""进程窗口截图能力（``bash_opt`` 的 ``op=screenshot`` / ``op=windows`` / ``op=window`` 实现层）。

职责划分：
  - 本模块：平台后端注册表 + 公共入口 :func:`capture_process_window`
    （截图）、:func:`list_process_windows`（窗口清单）、
    :func:`control_process_window`（窗口控制）；
  - ``win`` / ``x11`` / ``macos``：各平台后端（相同契约：``supports()`` +
    ``capture(pid, path, crop=None, window=None, grid=None)`` +
    ``list_windows(pid)`` + ``control(pid, request)``）；
  - ``windows``：跨平台窗口描述（:class:`WindowInfo`）与**统一选择语义**
    （main / active / #N / handle / title / class / popup / dialog），
    截图与输入注入共用同一套规则；
  - ``proctree``：跨平台进程树收集（目标进程 + 子进程）；
  - ``png`` / ``png_decode``：零依赖 PNG 编码 / 解码（截图落盘与裁剪读回）；
  - ``transform``：像素变换（区域裁剪：:class:`CropRegion`）与 PNG 原子写入；
  - ``grid``：截图坐标网格（参考线叠加，便于读图定位像素）。

扩展方式：新增平台只需实现上述契约并 :func:`register_backend`（内置后端
按 Windows → macOS → X11 顺序探测），无需改动工具层与既有后端；新增变换
在 ``transform`` / ``grid`` 模块扩展，后端按需接入；新增窗口选择形式在
``windows`` 模块扩展，全部平台与工具层自动获得该能力。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Callable

from .grid import draw_grid_bgra, paint_grid_on_png_file, resolve_step
from .diff import (
    DEFAULT_TOLERANCE,
    DiffResult,
    compare_images,
    compare_png_files,
    images_equal,
)
from .elements import (
    DEFAULT_ELEMENT_LIMIT,
    ElementError,
    ElementInfo,
    classify_control,
    describe_elements,
    filter_elements,
    list_process_elements,
    match_element,
)
from .result import CaptureResult, NoWindowError, ScreenshotError
from .transform import CropError, CropRegion, apply_crop_to_png_file
from .windows import (
    SelectorError,
    WindowControlRequest,
    WindowInfo,
    WindowSelector,
    describe_windows,
    indexed_summary,
    parse_control_request,
    parse_selector,
    pick_window,
    window_geometry,
    window_hint,
)

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


def capture_process_window(pid: int, path: str,
                           crop: CropRegion | None = None,
                           window: str | None = None,
                           grid: int | None = None) -> CaptureResult:
    """截取进程窗口并写入 ``path``（PNG），返回截图结果。

    Args:
        pid: 目标进程 PID（含其子进程一起参与窗口匹配）。
        path: 输出文件路径（调用方已确保目录可用）。
        crop: 可选裁剪区域（``CropRegion``，以整窗截图左上角为原点）；
            省略时输出整窗原始像素。区域越界抛 ``CropError``。
        window: 可选窗口选择器（``main`` / ``active`` / ``#1`` /
            ``handle:0x…`` / ``title:子串`` / ``class:子串`` / ``popup`` /
            ``dialog``；见 :mod:`.windows`）；省略时取主窗口。
        grid: 可选坐标网格步长（像素，``0`` = 自动）；非空时在产物上叠加
            参考线，便于读图定位坐标。

    Raises:
        ScreenshotError: 平台不支持、进程号非法、无窗口、抓取或落盘失败。
        SelectorError: 窗口选择器非法或没有匹配窗口。
        CropError: 裁剪区域越界或产物无法解码用于裁剪。
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ScreenshotError(f"进程号非法，无法截图: {pid!r}")
    backend = resolve_backend()
    if backend is None:
        raise ScreenshotError(
            f"当前平台（{sys.platform}）没有可用的截图后端（支持 Windows / "
            f"macOS / Linux X11）"
        )
    kwargs: dict = {"crop": crop}
    # 仅在需要时透传新参数：保持对既有后端 / 测试替身（只认 crop）的兼容
    if window:
        kwargs["window"] = window
    if grid is not None:
        kwargs["grid"] = grid
    try:
        result = backend.capture(pid, path, **kwargs)
    except (ScreenshotError, SelectorError, CropError):
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise ScreenshotError(
            f"截图失败（后端 {getattr(backend, 'name', '?')}，进程 {pid}）: {exc}"
        ) from exc
    if not os.path.exists(result.path):
        raise ScreenshotError(f"截图命令已执行但未生成文件: {result.path}")
    return result


def list_process_windows(pid: int) -> list[WindowInfo]:
    """返回 ``pid`` 及其子进程当前全部可操作窗口（按 Z 序）。

    纯查询、无副作用；后端不支持枚举窗口时返回空列表（调用方据此给出提示）。
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return []
    backend = resolve_backend()
    enumerate_fn = getattr(backend, "list_windows", None)
    if enumerate_fn is None:
        return []
    try:
        return list(enumerate_fn(pid))
    except Exception:  # pragma: no cover - 枚举失败不应中断调用方
        logger.debug("窗口枚举失败（后端 %s，进程 %s）",
                     getattr(backend, "name", "?"), pid, exc_info=True)
        return []


def control_process_window(pid: int, request: WindowControlRequest) -> dict:
    """对 ``pid`` 进程树中被选窗口执行状态 / 几何控制，返回动作细节。

    Raises:
        ScreenshotError: 平台后端不支持窗口控制，或控制调用失败。
        SelectorError: 窗口选择器非法或没有匹配窗口。
        NoWindowError: 进程树内没有可操作窗口。
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ScreenshotError(f"进程号非法，无法控制窗口: {pid!r}")
    backend = resolve_backend()
    control_fn = getattr(backend, "control", None)
    if control_fn is None:
        raise ScreenshotError(
            f"当前平台后端（{getattr(backend, 'name', '?')}）暂不支持窗口控制"
        )
    try:
        return dict(control_fn(pid, request))
    except (ScreenshotError, SelectorError):
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise ScreenshotError(
            f"窗口控制失败（后端 {getattr(backend, 'name', '?')}，进程 {pid}）: {exc}"
        ) from exc


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
    "CropError",
    "CropRegion",
    "DEFAULT_ELEMENT_LIMIT",
    "DEFAULT_TOLERANCE",
    "DiffResult",
    "ElementError",
    "ElementInfo",
    "NoWindowError",
    "ScreenshotError",
    "SelectorError",
    "WindowControlRequest",
    "WindowInfo",
    "WindowSelector",
    "apply_crop_to_png_file",
    "available_backends",
    "capture_process_window",
    "classify_control",
    "compare_images",
    "compare_png_files",
    "control_process_window",
    "describe_elements",
    "describe_windows",
    "draw_grid_bgra",
    "filter_elements",
    "images_equal",
    "indexed_summary",
    "list_process_elements",
    "list_process_windows",
    "match_element",
    "paint_grid_on_png_file",
    "parse_control_request",
    "parse_selector",
    "pick_window",
    "register_backend",
    "resolve_backend",
    "resolve_step",
    "window_geometry",
    "window_hint",
]
