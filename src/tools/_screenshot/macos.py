"""macOS 截图后端。

窗口定位：
  1. ``Quartz``（pyobjc）：``CGWindowListCopyWindowInfo`` 按 ``kCGWindowOwnerPID``
     匹配进程树成员，取普通层（``kCGWindowLayer == 0``）中面积最大的窗口号；
  2. 无 pyobjc 时用 ``osascript``（System Events）取窗口位置与尺寸。

像素获取：系统自带 ``/usr/sbin/screencapture``——有窗口号用 ``-l <id>``
精确截窗口；仅有位置尺寸时用 ``-R<x,y,w,h>`` 区域截图。

两次截图都需要「屏幕录制」权限（macOS 10.15+），权限缺失时系统返回的图
为桌面壁纸，本后端无法区分，仅在命令失败时抛出错误。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass

from . import png, proctree
from .result import CaptureResult, NoWindowError, ScreenshotError

logger = logging.getLogger(__name__)

_COMMAND_TIMEOUT = 30.0


@dataclass
class _MacWindow:
    """macOS 窗口条目（``number`` 为 CGWindowNumber，无 pyobjc 时为 None）。"""

    number: int | None
    pid: int
    title: str
    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height


class MacOSBackend:
    """macOS 平台截图后端。"""

    name = "macos"

    def supports(self) -> bool:
        return sys.platform == "darwin"

    def capture(self, pid: int, path: str) -> CaptureResult:
        screencapture = shutil.which("screencapture") or "/usr/sbin/screencapture"
        pids = proctree.collect_process_tree(pid)
        if not pids:
            raise ScreenshotError(f"进程号非法，无法截图: {pid}")
        target = select_macos_window(_quartz_windows(pids))
        if target is not None and target.number is not None:
            _run_checked([screencapture, "-x", "-o", "-l", str(target.number), path])
        else:
            target = target or _osascript_window(pids)
            if target is None:
                raise NoWindowError(
                    f"未找到进程 {pid} 及其子进程的可见窗口（纯命令行进程没有图形窗口）"
                )
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
        return CaptureResult(
            path=path,
            width=width,
            height=height,
            window_pid=target.pid,
            window_title=target.title,
            backend=self.name,
        )


def select_macos_window(windows: list[_MacWindow]) -> _MacWindow | None:
    """选择最可能的窗口：有标题 > 面积大（纯函数，便于单测）。"""
    if not windows:
        return None
    return max(windows, key=lambda item: (bool(item.title.strip()), item.area))


def _quartz_windows(pids: list[int]) -> list[_MacWindow]:
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
            ))
        except (TypeError, ValueError, KeyError) as exc:
            logger.debug("窗口信息解析失败: %s", exc)
    return windows


def _osascript_window(pids: list[int]) -> _MacWindow | None:
    """无 pyobjc 时用 osascript 探测「哪些 PID 有窗口」及其尺寸。"""
    for pid in pids:
        bounds = _osascript_bounds(pid)
        if bounds is None:
            continue
        x, y, width, height = bounds
        if width > 0 and height > 0:
            return _MacWindow(number=None, pid=pid, title="", width=width, height=height)
    return None


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


__all__ = ["MacOSBackend", "select_macos_window"]
