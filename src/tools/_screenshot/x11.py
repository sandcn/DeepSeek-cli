"""Linux / POSIX 图形会话截图后端（X11 优先，Wayland 会话回退）。

窗口定位（按可用工具择优）：
  1. ``xdotool search --onlyvisible --pid``（按进程树逐个 PID 搜索）
  2. ``wmctrl -lp``（窗口列表含 PID）

像素获取：
  1. ImageMagick ``import -window <id>``（或新版 ``magick import``）
  2. ``xwd -id <id>`` + ``convert``（ImageMagick 转换）
  3. ``gnome-screenshot -w``（Wayland/无窗口 ID 时的活动窗口兜底）

裁剪：``crop`` 指定时对上述命令产出的 PNG 解码裁剪后原子重写。

本后端不主动安装任何工具；工具缺失时抛出带安装提示的错误，由使用方决定。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from . import png, proctree, transform
from .result import CaptureResult, NoWindowError, ScreenshotError
from .transform import CropRegion

logger = logging.getLogger(__name__)

#: 单条外部命令超时（秒）
_COMMAND_TIMEOUT = 30.0
#: 窗口搜索命令超时（秒）
_SEARCH_TIMEOUT = 15.0


@dataclass
class _X11Window:
    """X11 窗口条目。"""

    window_id: str
    pid: int
    title: str
    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height


class X11Backend:
    """X11/Wayland 会话截图后端。"""

    name = "x11"

    def supports(self) -> bool:
        return (
            not sys.platform.startswith(("win", "cygwin", "msys"))
            and sys.platform != "darwin"
        )

    def capture(self, pid: int, path: str,
                crop: CropRegion | None = None) -> CaptureResult:
        """截取 ``pid``（及其子进程）的窗口到 ``path``（PNG）。

        ``crop`` 非空时对截图工具产出的 PNG 做解码裁剪（
        :func:`transform.apply_crop_to_png_file`）；区域越界抛 ``CropError``。
        """
        pids = proctree.collect_process_tree(pid)
        if not pids:
            raise ScreenshotError(f"进程号非法，无法截图: {pid}")
        windows = self._find_windows(pids)
        if not windows:
            raise NoWindowError(self._no_window_hint(pid))
        target = max(windows, key=lambda item: (bool(item.title.strip()), item.area))
        self._grab(target.window_id, path)
        width, height = self._read_size(path, target)
        if crop is not None:
            width, height = transform.apply_crop_to_png_file(path, crop)
        return CaptureResult(
            path=path,
            width=width,
            height=height,
            window_pid=target.pid,
            window_title=target.title,
            backend=self.name,
        )

    # ── 窗口发现 ─────────────────────────────────────────

    def _find_windows(self, pids: list[int]) -> list[_X11Window]:
        windows = _xdotool_windows(pids)
        if windows:
            return windows
        return _wmctrl_windows(pids)

    def _no_window_hint(self, pid: int) -> str:
        missing = [
            name for name in ("xdotool", "wmctrl", "import", "xwd")
            if shutil.which(name) is None
        ]
        hint = ""
        if missing:
            hint = f"（当前会话缺少工具: {', '.join(missing)}，可安装 xdotool / wmctrl / imagemagick / x11-apps）"
        session = os.environ.get("XDG_SESSION_TYPE", "")
        wayland_note = "；Wayland 原生窗口无法按 PID 定位，可在 XWayland 下运行程序" if session == "wayland" else ""
        return (
            f"未找到进程 {pid} 及其子进程的可见窗口{hint}{wayland_note}。"
            f"纯命令行进程没有图形窗口"
        )

    # ── 像素获取 ─────────────────────────────────────────

    def _grab(self, window_id: str, path: str) -> None:
        import_bin = shutil.which("import")
        if import_bin:
            _run_checked([import_bin, "-window", window_id, path], "ImageMagick import")
            return
        magick_bin = shutil.which("magick")
        if magick_bin:
            _run_checked([magick_bin, "import", "-window", window_id, path], "ImageMagick magick import")
            return
        xwd_bin = shutil.which("xwd")
        convert_bin = shutil.which("convert")
        if xwd_bin and convert_bin:
            _xwd_grab(xwd_bin, convert_bin, window_id, path)
            return
        gnome = shutil.which("gnome-screenshot")
        if gnome:
            _run_checked([gnome, "-w", "-f", path], "gnome-screenshot")
            return
        raise ScreenshotError(
            "当前会话无可用截图工具：请安装 ImageMagick（import）或 x11-apps（xwd）+ convert，"
            "或 gnome-screenshot"
        )

    @staticmethod
    def _read_size(path: str, target: _X11Window) -> tuple[int, int]:
        """读取产物尺寸；文件解析失败时回退窗口几何尺寸。"""
        return png.read_png_size_or(path, (target.width, target.height))


def _xdotool_windows(pids: list[int]) -> list[_X11Window]:
    """用 xdotool 按 PID 搜索窗口（工具不可用返回空表）。"""
    xdotool = shutil.which("xdotool")
    if not xdotool:
        return []
    windows: list[_X11Window] = []
    seen: set[str] = set()
    for pid in pids:
        completed = _run([xdotool, "search", "--onlyvisible", "--pid", str(pid)], _SEARCH_TIMEOUT)
        if completed is None or completed.returncode != 0:
            continue
        for window_id in completed.stdout.split():
            window_id = window_id.strip()
            if not window_id or window_id in seen:
                continue
            seen.add(window_id)
            width, height = _window_geometry(xdotool, window_id)
            if width <= 0 or height <= 0:
                continue
            windows.append(_X11Window(
                window_id=window_id,
                pid=pid,
                title=_window_title(xdotool, window_id),
                width=width,
                height=height,
            ))
    return windows


def _wmctrl_windows(pids: list[int]) -> list[_X11Window]:
    """用 wmctrl 列出窗口并按 PID 过滤（工具不可用返回空表）。"""
    wmctrl = shutil.which("wmctrl")
    if not wmctrl:
        return []
    completed = _run([wmctrl, "-lp"], _SEARCH_TIMEOUT)
    if completed is None or completed.returncode != 0:
        return []
    xdotool = shutil.which("xdotool")
    windows: list[_X11Window] = []
    for line in completed.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 3:
            continue
        window_id, _desktop, pid_text = parts[0], parts[1], parts[2]
        try:
            window_pid = int(pid_text)
        except ValueError:
            continue
        if window_pid not in pids:
            continue
        title = parts[4] if len(parts) > 4 else ""
        width, height = _window_geometry(xdotool, window_id)
        if width <= 0 or height <= 0:
            continue
        windows.append(_X11Window(
            window_id=window_id,
            pid=window_pid,
            title=title,
            width=width,
            height=height,
        ))
    return windows


def _window_geometry(xdotool: str | None, window_id: str) -> tuple[int, int]:
    """读取窗口宽高（xdotool 优先，回退 xwininfo；都不可用返回 (0, 0)）。"""
    if xdotool:
        completed = _run([xdotool, "getwindowgeometry", "--shell", window_id], _SEARCH_TIMEOUT)
        if completed is not None and completed.returncode == 0:
            width = height = 0
            for token in completed.stdout.splitlines():
                if token.startswith("WIDTH="):
                    width = _safe_int(token[6:])
                elif token.startswith("HEIGHT="):
                    height = _safe_int(token[7:])
            if width > 0 and height > 0:
                return width, height
    xwininfo = shutil.which("xwininfo")
    if xwininfo:
        completed = _run([xwininfo, "-id", window_id], _SEARCH_TIMEOUT)
        if completed is not None and completed.returncode == 0:
            width = height = 0
            for token in completed.stdout.splitlines():
                stripped = token.strip()
                if stripped.startswith("Width:"):
                    width = _safe_int(stripped.split(":", 1)[1])
                elif stripped.startswith("Height:"):
                    height = _safe_int(stripped.split(":", 1)[1])
            return width, height
    return 0, 0


def _window_title(xdotool: str, window_id: str) -> str:
    completed = _run([xdotool, "getwindowname", window_id], _SEARCH_TIMEOUT)
    if completed is None or completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _xwd_grab(xwd_bin: str, convert_bin: str, window_id: str, path: str) -> None:
    """``xwd`` 导出窗口位图后由 ``convert`` 转 PNG（临时文件即用即删）。"""
    handle, temp_path = tempfile.mkstemp(suffix=".xwd", prefix="shot-")
    os.close(handle)
    try:
        _run_checked([xwd_bin, "-id", window_id, "-silent", "-out", temp_path], "xwd")
        _run_checked([convert_bin, temp_path, path], "convert")
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def _run(command: list[str], timeout: float = _COMMAND_TIMEOUT):
    """执行外部命令（不抛异常，失败返回 None）。"""
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("命令执行失败 %s: %s", command[0], exc)
        return None


def _run_checked(command: list[str], label: str) -> None:
    """执行外部命令，失败时抛出带 stderr 摘要的 ScreenshotError。"""
    completed = _run(command)
    if completed is None:
        raise ScreenshotError(f"{label} 执行失败（命令不可用或超时）: {' '.join(command)}")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        summary = detail[-1] if detail else f"退出码 {completed.returncode}"
        raise ScreenshotError(f"{label} 截图失败: {summary}")


def _safe_int(text: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        return 0


__all__ = ["X11Backend"]
