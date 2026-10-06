"""macOS 截图后端。

窗口定位：
  1. ``Quartz``（pyobjc）：``CGWindowListCopyWindowInfo`` 按 ``kCGWindowOwnerPID``
     匹配进程树成员，取普通层（``kCGWindowLayer == 0``）中面积最大的窗口号；
  2. 无 pyobjc 时用 ``osascript``（System Events）取窗口位置与尺寸。

像素获取：系统自带 ``/usr/sbin/screencapture``——有窗口号用 ``-l <id>``
精确截窗口；仅有位置尺寸时用 ``-R<x,y,w,h>`` 区域截图。

裁剪：``crop`` 指定时对产出 PNG 解码裁剪后原子重写。

两次截图都需要「屏幕录制」权限（macOS 10.15+），权限缺失时系统返回的图
为桌面壁纸，本后端无法区分，仅在命令失败时抛出错误。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from . import png, proctree, transform
from .grid import paint_grid_on_png_file
from .result import CaptureResult, NoWindowError, ScreenshotError
from .transform import CropRegion
from .windows import (
    DEFAULT_SELECTOR,
    WindowControlRequest,
    WindowInfo,
    mark_main,
    pick_window,
)

logger = logging.getLogger(__name__)

_COMMAND_TIMEOUT = 30.0
#: 窗口控制（移动 / 缩放 / 最大化等）后等待状态生效的时间（秒）
_CONTROL_SETTLE_SECONDS = 0.2


@dataclass
class _MacWindow:
    """macOS 窗口条目（``number`` 为 CGWindowNumber，无 pyobjc 时为 None）。

    ``x`` / ``y`` 为窗口左上角的屏幕坐标（CGWindowBounds / System Events 位置）。
    """

    number: int | None
    pid: int
    title: str
    width: int
    height: int
    x: int = 0
    y: int = 0
    class_name: str = ""
    visible: bool = True
    foreground: bool = False

    @property
    def area(self) -> int:
        return self.width * self.height


def to_window_info(window: _MacWindow, order: int = 0) -> WindowInfo:
    """macOS 窗口条目 → 通用窗口描述（句柄为 CGWindowNumber，缺省 0）。"""
    return WindowInfo(
        handle=int(window.number or 0),
        pid=window.pid,
        title=window.title,
        class_name=window.class_name,
        width=window.width,
        height=window.height,
        left=window.x,
        top=window.y,
        visible=window.visible,
        foreground=window.foreground,
        order=order,
    )


class MacOSBackend:
    """macOS 平台截图后端。"""

    name = "macos"

    def supports(self) -> bool:
        return sys.platform == "darwin"

    def capture(self, pid: int, path: str,
                crop: CropRegion | None = None,
                window: str | None = None,
                grid: int | None = None) -> CaptureResult:
        """截取 ``pid``（及其子进程）的窗口到 ``path``（PNG）。

        Args:
            pid: 目标进程 PID（含子进程）。
            path: 输出 PNG 路径。
            crop: 可选裁剪区域；越界抛 ``CropError``。
            window: 可选窗口选择器（缺省 = 主窗口）；无匹配抛 ``SelectorError``。
            grid: 可选坐标网格步长（像素，``0`` = 自动）。

        Raises:
            ScreenshotError: 无可用截图工具或命令失败。
            NoWindowError: 进程树内没有可见窗口。
        """
        screencapture = shutil.which("screencapture") or "/usr/sbin/screencapture"
        pids = proctree.collect_process_tree(pid)
        if not pids:
            raise ScreenshotError(f"进程号非法，无法截图: {pid}")
        windows = _process_windows(pids)
        if not windows:
            raise NoWindowError(
                f"未找到进程 {pid} 及其子进程的可见窗口（纯命令行进程没有图形窗口）"
            )
        infos = mark_main([to_window_info(item, index)
                           for index, item in enumerate(windows)])
        target_info = pick_window(infos, window)
        target = _find_by_info(windows, target_info)
        if target.number is not None:
            _run_checked([screencapture, "-x", "-o", "-l", str(target.number), path])
        else:
            bounds = _osascript_bounds(target.pid)
            if bounds is None:
                raise ScreenshotError(
                    f"进程 {target.pid} 的窗口位置读取失败（需要「辅助功能」权限），"
                    f"且未安装 pyobjc 无法按窗口号精确截图"
                )
            x, y, width, height = bounds
            _run_checked([
                screencapture, "-x", "-R", f"{x},{y},{width},{height}", path,
            ])
        width, height = _read_size(path, target)
        if crop is not None:
            width, height = transform.apply_crop_to_png_file(path, crop)
        if grid is not None:
            width, height, _step = paint_grid_on_png_file(path, int(grid))
        return CaptureResult(
            path=path,
            width=width,
            height=height,
            window_pid=target.pid,
            window_title=target.title,
            backend=self.name,
            window_handle=int(target.number or 0),
            windows_total=len(windows),
            window_selector=window or DEFAULT_SELECTOR,
            window_summary=target_info.summary(),
        )

    def list_windows(self, pid: int) -> list[WindowInfo]:
        """枚举该进程树的全部可见窗口（``op=windows`` 数据源）。"""
        pids = proctree.collect_process_tree(pid)
        if not pids:
            return []
        windows = _process_windows(pids)
        return mark_main([to_window_info(item, index)
                          for index, item in enumerate(windows)])

    def control(self, pid: int, request: WindowControlRequest) -> dict:
        """对被选窗口执行激活 / 最大化 / 最小化 / 还原 / 关闭 / 移动 / 缩放。"""
        return control_window(pid, request, runner=self._run)


def select_macos_window(windows: list[_MacWindow]) -> _MacWindow | None:
    """选择最可能的窗口：有标题 > 面积大（纯函数，便于单测）。"""
    if not windows:
        return None
    return max(windows, key=lambda item: (bool(item.title.strip()), item.area))


def _quartz_windows(pids: list[int],
                    frontmost_pid: int | None = None) -> list[_MacWindow]:
    """经 pyobjc Quartz 枚举窗口（未安装 pyobjc 返回空表）。"""
    try:
        import Quartz
    except ImportError:
        logger.debug("未安装 pyobjc（Quartz），回退 osascript 方案")
        return []
    options = (
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements
    )
    try:
        infos = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID)
    except Exception as exc:  # pragma: no cover - 依赖系统框架
        logger.debug("CGWindowListCopyWindowInfo 失败: %s", exc)
        return []
    windows: list[_MacWindow] = []
    for info in infos or []:
        try:
            window_pid = int(info.get(Quartz.kCGWindowOwnerPID, 0))
            if window_pid not in pids:
                continue
            if int(info.get(Quartz.kCGWindowLayer, 0)) != 0:
                continue
            bounds = info.get(Quartz.kCGWindowBounds) or {}
            width = int(bounds.get("Width", 0))
            height = int(bounds.get("Height", 0))
            if width <= 0 or height <= 0:
                continue
            windows.append(_MacWindow(
                number=int(info.get(Quartz.kCGWindowNumber, 0)) or None,
                pid=window_pid,
                title=str(info.get(Quartz.kCGWindowName) or ""),
                width=width,
                height=height,
                x=int(bounds.get("X", 0)),
                y=int(bounds.get("Y", 0)),
                class_name=str(info.get(Quartz.kCGWindowOwnerName) or ""),
                visible=bool(info.get(Quartz.kCGWindowIsOnscreen, True)),
                foreground=(frontmost_pid is not None and window_pid == frontmost_pid),
            ))
        except (TypeError, ValueError, KeyError) as exc:
            logger.debug("窗口信息解析失败: %s", exc)
    return windows


def _process_windows(pids: list[int]) -> list[_MacWindow]:
    """进程树内全部可见窗口（Quartz 优先，回退 osascript 单窗口）。"""
    windows = _quartz_windows(pids, frontmost_pid=_frontmost_pid())
    if windows:
        return windows
    fallback = _osascript_window(pids)
    return [fallback] if fallback is not None else []


def _frontmost_pid() -> int | None:
    """前台应用的 PID（``osascript``；无权限或失败返回 None）。"""
    osascript = shutil.which("osascript")
    if not osascript:
        return None
    completed = _run([
        osascript, "-e",
        'tell application "System Events" to get unix id of first process '
        'whose frontmost is true',
    ])
    if completed is None or completed.returncode != 0:
        return None
    numbers = _extract_int_list(completed.stdout)
    return numbers[0] if numbers else None


def find_platform_window(windows: list, info: WindowInfo):
    """按通用描述（句柄）回查平台窗口对象（句柄为 0 或不匹配时取第一个）。"""
    if info.handle:
        for window in windows:
            if int(window.number or 0) == info.handle:
                return window
    return windows[0]  # pragma: no cover - 句柄来自同一份枚举结果


def _find_by_info(windows: list[_MacWindow], info: WindowInfo) -> _MacWindow:
    """（内部别名）见 :func:`find_platform_window`。"""
    return find_platform_window(windows, info)


def list_windows(pid: int) -> list[WindowInfo]:
    """返回 ``pid`` 及其后代进程的可见窗口（通用描述，按枚举顺序）。"""
    pids = proctree.collect_process_tree(pid)
    if not pids:
        return []
    windows = _process_windows(pids)
    return mark_main([to_window_info(item, index)
                      for index, item in enumerate(windows)])


def control_window(pid: int, request: WindowControlRequest, *,
                   runner=None) -> dict:
    """对 ``pid`` 进程树中被选窗口执行状态 / 几何控制（System Events）。

    macOS 的窗口寻址经 System Events 完成，因此选择器最终都解析为「第 N 个
    窗口」：``main`` / ``active`` / ``#N`` / ``title:子串`` / ``popup`` 均可用；
    ``handle:`` 无法用 System Events 寻址，会给出改用建议。

    Raises:
        NoWindowError: 进程树内没有可用窗口。
        SelectorError: 窗口选择器非法或没有匹配窗口。
        ScreenshotError: 控制命令失败（通常是缺少「辅助功能」权限）。
    """
    run = runner or _run
    pids = proctree.collect_process_tree(pid)
    windows = _process_windows(pids) if pids else []
    if not windows:
        raise NoWindowError(
            f"进程 {pid} 及其子进程没有可操作窗口（纯命令行进程没有图形窗口）"
        )
    infos = mark_main([to_window_info(item, index)
                       for index, item in enumerate(windows)])
    target_info = pick_window(infos, request.selector)
    index = next((position for position, item in enumerate(windows)
                  if to_window_info(item).handle == target_info.handle), 0)
    window_expr = f"window {index + 1}"
    target = windows[index]
    before = _window_bounds(pid, target)
    statement = _control_statement(request.action, pid, window_expr, request)
    _run_apple_script(run, statement, f"窗口动作 {request.action}")
    time.sleep(_CONTROL_SETTLE_SECONDS)
    after = _window_bounds(pid, target)
    return {
        "window_action": request.action,
        "handle": target_info.handle,
        "handle_hex": target_info.handle_hex,
        "window_title": target_info.title,
        "window_index": index + 1,
        "before": before,
        "after": after,
    }


def _control_statement(action: str, pid: int, window_expr: str,
                       request: WindowControlRequest) -> str:
    """把窗口动作翻译为 AppleScript 语句。"""
    process = f'(first process whose unix id is {pid})'
    if action == "activate":
        return f'tell application "System Events" to set frontmost of {process} to true'
    if action == "maximize":
        return (
            'tell application "Finder" to set _screen to bounds of window of desktop\n'
            f'tell application "System Events" to tell {process}\n'
            f"  set position of {window_expr} to {{0, 0}}\n"
            f"  set size of {window_expr} to {{item 3 of _screen, item 4 of _screen}}\n"
            "end tell"
        )
    if action == "minimize":
        return (
            f'tell application "System Events" to tell {process} to '
            f'set value of attribute "AXMinimized" of {window_expr} to true'
        )
    if action == "restore":
        return (
            f'tell application "System Events" to tell {process} to '
            f'set value of attribute "AXMinimized" of {window_expr} to false\n'
            f'tell application "System Events" to set frontmost of {process} to true'
        )
    if action == "close":
        return (
            f'tell application "System Events" to tell {process} to '
            f"click button 1 of {window_expr}"
        )
    if action == "move":
        return (
            f'tell application "System Events" to tell {process} to '
            f"set position of {window_expr} to {{{request.x}, {request.y}}}"
        )
    if action == "resize":
        return (
            f'tell application "System Events" to tell {process} to '
            f"set size of {window_expr} to {{{request.width}, {request.height}}}"
        )
    return (  # pragma: no branch - 动作集合由上层校验（fit）
        f'tell application "System Events" to tell {process}\n'
        f"  set position of {window_expr} to {{{request.x}, {request.y}}}\n"
        f"  set size of {window_expr} to {{{request.width}, {request.height}}}\n"
        "end tell"
    )


def _run_apple_script(run, script: str, label: str) -> None:
    """执行 AppleScript（失败时附带辅助功能权限提示）。"""
    osascript = shutil.which("osascript")
    if not osascript:
        raise ScreenshotError(f"{label}失败：osascript 不可用")
    completed = run([osascript, "-e", script])
    if completed is None:
        raise ScreenshotError(f"{label}失败：osascript 执行超时或不可用")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        summary = detail[-1] if detail else f"退出码 {completed.returncode}"
        raise ScreenshotError(
            f"{label}失败: {summary}（通常是没有「辅助功能」权限："
            f"系统设置 → 隐私与安全性 → 辅助功能）"
        )


def _window_bounds(pid: int, window: _MacWindow) -> dict:
    """读取窗口几何（Quartz 按窗口号精确读取，回退 System Events）。"""
    bounds = None
    if window.number is not None:
        bounds = _quartz_bounds(window.number)
    if bounds is None:
        bounds = _osascript_bounds(pid)
    if bounds is None:
        bounds = (window.x, window.y, window.width, window.height)
    x, y, width, height = bounds
    return {"x": x, "y": y, "width": width, "height": height}


def _quartz_bounds(number: int) -> tuple[int, int, int, int] | None:
    """按 CGWindowNumber 读取窗口几何（无 pyobjc 返回 None）。"""
    try:
        import Quartz
    except ImportError:
        return None
    try:
        infos = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionIncludingWindow, int(number)
        )
    except Exception as exc:  # pragma: no cover - 依赖系统框架
        logger.debug("CGWindowListCopyWindowInfo(window) 失败: %s", exc)
        return None
    for info in infos or []:
        try:
            if int(info.get(Quartz.kCGWindowNumber, 0)) != int(number):
                continue
            bounds = info.get(Quartz.kCGWindowBounds) or {}
            return (int(bounds.get("X", 0)), int(bounds.get("Y", 0)),
                    int(bounds.get("Width", 0)), int(bounds.get("Height", 0)))
        except (TypeError, ValueError) as exc:
            logger.debug("窗口几何解析失败: %s", exc)
    return None


def _osascript_window(pids: list[int]) -> _MacWindow | None:
    """无 pyobjc 时用 osascript 探测「哪些 PID 有窗口」及其位置尺寸。"""
    for pid in pids:
        bounds = _osascript_bounds(pid)
        if bounds is None:
            continue
        x, y, width, height = bounds
        if width > 0 and height > 0:
            return _MacWindow(
                number=None, pid=pid, title="",
                width=width, height=height, x=x, y=y,
            )
    return None


def find_process_window(pid: int) -> _MacWindow | None:
    """返回 ``pid`` 及其后代进程的可见窗口（窗口输入 / 截图共用）。

    纯查询、无副作用；pyobjc（Quartz）优先，回退 osascript。无窗口返回 None。
    """
    pids = proctree.collect_process_tree(pid)
    if not pids:
        return None
    target = select_macos_window(_quartz_windows(pids))
    if target is not None:
        return target
    return _osascript_window(pids)


def _osascript_bounds(pid: int) -> tuple[int, int, int, int] | None:
    """取前台窗口的位置与尺寸（``osascript`` + System Events）。

    返回 ``(x, y, width, height)``；无窗口 / 无权限返回 None。
    """
    osascript = shutil.which("osascript")
    if not osascript:
        return None
    script = (
        'tell application "System Events" to tell '
        f'(first process whose unix id is {pid}) to get {{position, size}} of window 1'
    )
    completed = _run([osascript, "-e", script])
    if completed is None or completed.returncode != 0:
        return None
    numbers = _extract_int_list(completed.stdout)
    if len(numbers) != 4:
        return None
    x, y, width, height = numbers
    return x, y, width, height


def _extract_int_list(text: str) -> list[int]:
    """从 ``10, 20, 800, 600`` 之类的输出中提取整数序列。"""
    values: list[int] = []
    token = ""
    for char in text:
        if char.isdigit() or (char == "-" and not token):
            token += char
        else:
            if token and token != "-":
                values.append(int(token))
            token = ""
    if token and token != "-":
        values.append(int(token))
    return values


def _read_size(path: str, target: _MacWindow | None) -> tuple[int, int]:
    """读取产物尺寸；解析失败回退窗口几何尺寸。"""
    fallback = (target.width, target.height) if target is not None else (0, 0)
    return png.read_png_size_or(path, fallback)


def _run(command: list[str]):
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("命令执行失败 %s: %s", command[0], exc)
        return None


def _run_checked(command: list[str]) -> None:
    completed = _run(command)
    if completed is None:
        raise ScreenshotError(f"截图命令执行失败（不可用或超时）: {' '.join(command)}")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        summary = detail[-1] if detail else f"退出码 {completed.returncode}"
        raise ScreenshotError(f"screencapture 截图失败: {summary}")


__all__ = ["MacOSBackend", "control_window", "find_platform_window",
           "find_process_window", "list_windows", "select_macos_window",
           "to_window_info"]
