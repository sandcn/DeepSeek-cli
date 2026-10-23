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
from dataclasses import dataclass

from . import png, proctree, transform
from .grid import paint_grid_on_png_file
from .monitors import Monitor
from .result import CaptureResult, NoWindowError, ScreenshotError
from .transform import CropRegion
from .windows import (
    DEFAULT_SELECTOR,
    WindowInfo,
    indexed_summary,
    mark_main,
    pick_window,
    window_geometry,
)

logger = logging.getLogger(__name__)

_COMMAND_TIMEOUT = 30.0


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
        # macOS 没有 exe 名，用应用名（owner name，即 class_name）充当进程名
        process_name=window.class_name,
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
        # 产物图左上角对应的屏幕坐标（crop 后叠加偏移），便于把截图像素
        # 换算为屏幕坐标（screen = window_x + px）
        origin_x, origin_y = target_info.left, target_info.top
        if crop is not None:
            width, height = transform.apply_crop_to_png_file(path, crop)
            origin_x += crop.x
            origin_y += crop.y
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
            window_summary=indexed_summary(infos, target_info),
            window_x=origin_x,
            window_y=origin_y,
            window_rect=window_geometry(infos, target_info)["rect"],
        )

    def list_windows(self, pid: int) -> list[WindowInfo]:
        """枚举该进程树的全部可见窗口（截图 / 输入共用）。"""
        pids = proctree.collect_process_tree(pid)
        if not pids:
            return []
        windows = _process_windows(pids)
        return mark_main([to_window_info(item, index)
                          for index, item in enumerate(windows)])

    def list_monitors(self) -> list[Monitor]:
        """经 Quartz 枚举显示器（需 pyobjc；不可用返回空列表）。"""
        return list_monitors()

    def capture_screen(self, monitor: Monitor, path: str,
                       crop: CropRegion | None = None,
                       grid: int | None = None) -> CaptureResult:
        """截取显示器区域（``screencapture -R``）。"""
        screencapture = shutil.which("screencapture") or "/usr/sbin/screencapture"
        _run_checked([
            screencapture, "-x", "-R",
            f"{monitor.left},{monitor.top},{monitor.width},{monitor.height}", path,
        ])
        width, height = png.read_png_size_or(path, (monitor.width, monitor.height))
        origin_x, origin_y = monitor.left, monitor.top
        if crop is not None:
            width, height = transform.apply_crop_to_png_file(path, crop)
            origin_x += crop.x
            origin_y += crop.y
        if grid is not None:
            width, height, _step = paint_grid_on_png_file(path, int(grid))
        return CaptureResult(
            path=path, width=width, height=height, window_pid=0, window_title="",
            backend=self.name, window_handle=0, windows_total=0,
            window_selector="screen",
            window_summary=(f"显示器 {monitor.width}x{monitor.height}"
                            f"@({monitor.left},{monitor.top})"
                            + ("[primary]" if monitor.primary else "")),
            window_x=origin_x, window_y=origin_y,
            window_rect={"x": monitor.left, "y": monitor.top,
                         "width": monitor.width, "height": monitor.height},
        )


def select_macos_window(windows: list[_MacWindow]) -> _MacWindow | None:
    """选择最可能的窗口：有标题 > 面积大（纯函数，便于单测）。"""
    if not windows:
        return None
    return max(windows, key=lambda item: (bool(item.title.strip()), item.area))


def list_monitors() -> list[Monitor]:
    """经 Quartz 枚举活动显示器（需 pyobjc；不可用返回空列表）。"""
    try:
        import Quartz
    except ImportError:
        return []
    try:
        error, displays, count = Quartz.CGGetActiveDisplayList(32, None, None)
    except Exception:  # pragma: no cover - 依赖 pyobjc
        logger.debug("CGGetActiveDisplayList 失败", exc_info=True)
        return []
    if error:
        return []
    monitors: list[Monitor] = []
    for index in range(int(count)):
        display = displays[index]
        rect = Quartz.CGDisplayBounds(display)
        origin, size = rect.origin, rect.size
        monitors.append(Monitor(
            int(origin.x), int(origin.y), int(size.width), int(size.height),
            bool(Quartz.CGDisplayIsMain(display)),
        ))
    return monitors


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


__all__ = ["MacOSBackend", "find_platform_window",
           "find_process_window", "list_windows", "select_macos_window",
           "to_window_info"]
