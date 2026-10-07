"""X11 窗口输入后端（xdotool）。

窗口定位与截图后端共用同一套规则（``find_process_windows``：xdotool 搜索
优先、wmctrl 回退），坐标换算为屏幕像素后由 xdotool 注入。

能力映射：

  - move / click / drag / scroll → ``xdotool mousemove`` / ``click`` /
    ``mousedown`` / ``mouseup``（滚轮 = 按钮 4/5，水平滚轮 = 6/7）
  - key → ``xdotool keydown`` + ``xdotool keyup``（``ctrl+shift+s`` 语法；
    按下与弹起分开发送，``phase`` 可只发其中之一）
  - type → ``xdotool type --delay``（换行与制表符转成 ``key Return`` / ``key Tab``）
  - modifiers → ``xdotool keydown`` / ``keyup``

注入前用 ``xdotool windowactivate --sync`` 把目标窗口激活（部分 WM 拒绝
激活时仍继续，由后续命令的成败反映）。本后端不主动安装任何工具；缺
xdotool 时抛出带安装提示的错误。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .._screenshot.windows import DEFAULT_SELECTOR, pick_window
from .._screenshot.x11 import list_windows as list_platform_windows
from .action import (
    ClickAction,
    DragAction,
    InputAction,
    KeyAction,
    MoveAction,
    Point,
    ScrollAction,
    TextAction,
    interpolate,
    resolve_point,
    validate_point,
)
from .geometry import WindowFrame
from .keys import X11_KEYSYM
from .result import ActionError, InputError, InputResult, NoWindowError

logger = logging.getLogger(__name__)

#: 单条 xdotool 命令超时（秒）
_COMMAND_TIMEOUT = 30.0
#: type 逐字符延迟（毫秒）——过快时部分程序丢字符
_TYPE_DELAY_MS = 12
#: 双击间隔（毫秒）
_DOUBLE_CLICK_DELAY_MS = 80

#: 鼠标按钮 → xdotool 按钮号
_BUTTON_NUMBERS: dict[str, int] = {"left": 1, "middle": 2, "right": 3}

#: 滚动方向 → xdotool 按钮号（4/5 垂直，6/7 水平）
_SCROLL_BUTTONS: dict[str, int] = {"up": 4, "down": 5, "left": 6, "right": 7}


@dataclass
class _X11Target:
    """定位到的 X11 窗口（窗口 ID + 截图坐标系）。"""

    window_id: str
    pid: int
    title: str
    frame: WindowFrame


class X11InputBackend:
    """X11 / Wayland(XWayland) 平台窗口输入后端。"""

    name = "x11"

    def __init__(self, locator=None, runner=None):
        self._locate = locator or locate_window
        self._run = runner or run_command

    def supports(self) -> bool:
        return (
            not sys.platform.startswith(("win", "cygwin", "msys"))
            and sys.platform != "darwin"
        )

    def locate(self, pid: int, window: str | None = None) -> _X11Target | None:
        """定位 ``pid``（含子进程）的目标窗口（无副作用；供路由决策与注入复用）。

        ``window`` 为窗口选择器（见 ``_screenshot.windows``）；缺省取主窗口。
        """
        if window:
            return self._locate(pid, window, runner=self._run)
        return self._locate(pid, runner=self._run)

    def send(self, pid: int, action: InputAction) -> InputResult:
        """向 ``pid`` 的目标窗口注入 ``action``，返回注入结果。"""
        xdotool = require_xdotool()
        target = self.locate(pid, getattr(action, "window", "") or None)
        if target is None:
            raise NoWindowError(
                f"进程 {pid} 及其子进程没有可接收输入的可见窗口"
                f"（纯命令行进程没有图形窗口；Wayland 原生窗口无法按 PID 定位，"
                f"可在 XWayland 下运行程序）"
            )
        self._activate(target)
        detail = self._dispatch(xdotool, target, action)
        return InputResult(
            action=action.name,
            backend=self.name,
            window_pid=target.pid,
            window_title=target.title,
            detail=detail,
            window_selector=getattr(action, "window", "") or DEFAULT_SELECTOR,
            window_handle=str(target.window_id),
            window_frame=target.frame.to_dict(),
        )

    # ── 命令执行 ─────────────────────────────────────────

    def _activate(self, target: _X11Target) -> None:
        """激活目标窗口（失败仅记录日志，不中断注入）。"""
        completed = self._run(
            [require_xdotool(), "windowactivate", "--sync", target.window_id])
        if completed is None or completed.returncode != 0:
            logger.debug("xdotool windowactivate 失败: window=%s", target.window_id)

    def _checked(self, command: list[str], label: str) -> None:
        completed = self._run(command)
        if completed is None:
            raise InputError(f"{label} 执行失败（xdotool 不可用或超时）: {' '.join(command)}")
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            summary = detail[-1] if detail else f"退出码 {completed.returncode}"
            raise InputError(f"{label} 失败: {summary}")

    # ── 动作分发 ─────────────────────────────────────────

    def _dispatch(self, xdotool: str, target: _X11Target,
                  action: InputAction) -> dict:
        if isinstance(action, MoveAction):
            return self._move(xdotool, target, action)
        if isinstance(action, ClickAction):
            return self._click(xdotool, target, action)
        if isinstance(action, DragAction):
            return self._drag(xdotool, target, action)
        if isinstance(action, ScrollAction):
            return self._scroll(xdotool, target, action)
        if isinstance(action, KeyAction):
            return self._key(xdotool, action)
        if isinstance(action, TextAction):
            return self._type(xdotool, action)
        raise ActionError(f"X11 后端不支持的动作: {action.name}")  # pragma: no cover

    def _move(self, xdotool: str, target: _X11Target, action: MoveAction) -> dict:
        point = resolve_point(action.x, action.y, target.frame.width,
                              target.frame.height, label="移动坐标")
        screen = target.frame.to_screen(point)
        command = [xdotool, "mousemove", "--sync", str(screen[0]), str(screen[1])]
        with self._hold_modifiers(xdotool, action.modifiers):
            self._checked(command, "鼠标移动")
        detail = _point_detail(point, screen)
        return detail

    def _click(self, xdotool: str, target: _X11Target, action: ClickAction) -> dict:
        point = resolve_point(action.x, action.y, target.frame.width,
                              target.frame.height, label="点击坐标")
        screen = target.frame.to_screen(point)
        number = _BUTTON_NUMBERS[action.button]
        self._checked(
            [xdotool, "mousemove", "--sync", str(screen[0]), str(screen[1])],
            "鼠标移动",
        )
        command = [xdotool, "click"]
        if action.count > 1:
            command += ["--repeat", str(action.count),
                        "--delay", str(_DOUBLE_CLICK_DELAY_MS)]
        command.append(str(number))
        with self._hold_modifiers(xdotool, action.modifiers):
            self._checked(command, "鼠标点击")
        detail = _point_detail(point, screen)
        detail.update({"button": action.button, "count": action.count})
        return detail

    def _drag(self, xdotool: str, target: _X11Target, action: DragAction) -> dict:
        frame = target.frame
        start = resolve_point(action.from_x, action.from_y, frame.width, frame.height,
                              label="拖动起点")
        end = validate_point(Point(action.to_x, action.to_y), frame.width, frame.height,
                             label="拖动终点")
        start_screen = frame.to_screen(start)
        end_screen = frame.to_screen(end)
        number = _BUTTON_NUMBERS[action.button]
        interval = _drag_interval(action)
        command = [xdotool, "mousemove", "--sync", str(start_screen[0]), str(start_screen[1]),
                   "mousedown", str(number)]
        for point in interpolate(start, end, action.steps):
            screen = frame.to_screen(point)
            command += ["mousemove", "--sync", str(screen[0]), str(screen[1])]
            if interval > 0:
                command += ["sleep", f"{interval:.3f}"]
        command += ["mousemove", "--sync", str(end_screen[0]), str(end_screen[1]),
                    "mouseup", str(number)]
        with self._hold_modifiers(xdotool, action.modifiers):
            self._checked(command, "鼠标拖动")
        detail = _point_detail(start, start_screen)
        detail.update({
            "button": action.button,
            "duration": action.duration,
            "steps": action.steps,
            "to_x": end.x,
            "to_y": end.y,
            "to_screen_x": end_screen[0],
            "to_screen_y": end_screen[1],
        })
        return detail

    def _scroll(self, xdotool: str, target: _X11Target, action: ScrollAction) -> dict:
        point = resolve_point(action.x, action.y, target.frame.width,
                              target.frame.height, label="滚动坐标")
        screen = target.frame.to_screen(point)
        number = _SCROLL_BUTTONS[action.direction]
        command = [xdotool, "mousemove", "--sync", str(screen[0]), str(screen[1]),
                   "click", "--repeat", str(action.amount), str(number)]
        with self._hold_modifiers(xdotool, action.modifiers):
            self._checked(command, "滚轮滚动")
        detail = _point_detail(point, screen)
        detail.update({"direction": action.direction, "amount": action.amount})
        return detail

    def _key(self, xdotool: str, action: KeyAction) -> dict:
        combo = _xdotool_combo(action)
        # 分开发送按下与弹起（xdotool key 会把两者合并成一条命令）：
        # phase=down/up 可只发送其中之一，用于长按或单独释放。
        repeats = action.effective_repeat
        for _index in range(repeats):
            if action.phase in ("press", "down"):
                self._checked([xdotool, "keydown", combo], "按键按下")
            if action.phase in ("press", "up"):
                self._checked([xdotool, "keyup", combo], "按键弹起")
        return {
            "key": action.shortcut.display(),
            "xdotool_key": combo,
            "modifiers": list(action.shortcut.modifiers),
            "phase": action.phase,
            "repeat": repeats,
        }

    def _type(self, xdotool: str, action: TextAction) -> dict:
        """逐行调用 ``xdotool type``；换行 / 制表符转为对应按键。"""
        line = ""
        characters = 0
        for char in action.text:
            if char == "\r":
                continue
            if char == "\n":
                self._flush_line(xdotool, line)
                line = ""
                self._checked([xdotool, "key", X11_KEYSYM["enter"]], "回车键")
            elif char == "\t":
                self._flush_line(xdotool, line)
                line = ""
                self._checked([xdotool, "key", X11_KEYSYM["tab"]], "制表键")
            else:
                line += char
            characters += 1
        self._flush_line(xdotool, line)
        return {"text": action.text, "characters": characters}

    def _flush_line(self, xdotool: str, text: str) -> None:
        if not text:
            return
        self._checked(
            [xdotool, "type", "--delay", str(_TYPE_DELAY_MS), "--", text],
            "文本输入",
        )

    def _hold_modifiers(self, xdotool: str, modifiers: tuple[str, ...]):
        return _ModifierContext(self, xdotool, modifiers)


class _ModifierContext:
    """按住修饰键的上下文管理器（xdotool keydown/keyup）。"""

    def __init__(self, backend: X11InputBackend, xdotool: str,
                 modifiers: tuple[str, ...]):
        self._backend = backend
        self._xdotool = xdotool
        self._names = [X11_KEYSYM[name] for name in modifiers if name in X11_KEYSYM]

    def __enter__(self) -> None:
        for name in self._names:
            self._backend._checked([self._xdotool, "keydown", name], "按下修饰键")
        return None

    def __exit__(self, exc_type, exc, tb) -> None:
        for name in reversed(self._names):
            try:
                self._backend._checked([self._xdotool, "keyup", name], "释放修饰键")
            except InputError:
                logger.debug("释放修饰键失败: %s", name, exc_info=True)
        return None


# ── 定位与工具（模块级，便于复用与单测） ────────────────────

def locate_window(pid: int, window: str | None = None, *,
                  runner=None) -> _X11Target | None:
    """定位 ``pid``（含子进程）的目标窗口。

    Args:
        pid: 目标进程 PID（含子进程）。
        window: 窗口选择器（``main`` / ``active`` / ``#1`` / ``title:子串`` /
            ``popup`` 等）；``None`` = 主窗口。

    Raises:
        SelectorError: 选择器非法或没有匹配窗口。
    """
    infos = list_platform_windows(pid)
    if not infos:
        return None
    target = pick_window(infos, window)
    return _X11Target(
        window_id=str(target.handle),
        pid=target.pid,
        title=target.title,
        frame=WindowFrame(target.left, target.top, target.width, target.height),
    )


def require_xdotool() -> str:
    """返回 xdotool 可执行路径；未安装时抛出带安装提示的错误。"""
    path = shutil.which("xdotool")
    if not path:
        raise InputError(
            "窗口输入需要 xdotool（未安装）：Debian/Ubuntu 用 "
            "sudo apt install xdotool，Arch 用 sudo pacman -S xdotool，"
            "Fedora 用 sudo dnf install xdotool"
        )
    return path


def run_command(command: list[str]):
    """执行外部命令（失败返回 None，不抛异常）。"""
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=_COMMAND_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("命令执行失败 %s: %s", command[0], exc)
        return None


def _xdotool_combo(action: KeyAction) -> str:
    """把组合键转为 xdotool 语法（``ctrl+shift+s``）。"""
    names = [X11_KEYSYM[name] for name in action.shortcut.modifiers if name in X11_KEYSYM]
    if action.shortcut.is_character:
        names.append(action.shortcut.key)
    else:
        mapped = X11_KEYSYM.get(action.shortcut.key)
        if mapped is None:
            raise InputError(
                f"X11 后端不支持按键: {action.shortcut.key!r}"
            )
        names.append(mapped)
    return "+".join(names)


def _drag_interval(action: DragAction) -> float:
    """拖动轨迹每步间隔（秒）；duration 为 0 时不留额外延迟。"""
    if action.duration <= 0:
        return 0.0
    return action.duration / max(action.steps, 1)


def _point_detail(point: Point, screen: tuple[int, int]) -> dict:
    return {
        "x": point.x,
        "y": point.y,
        "screen_x": screen[0],
        "screen_y": screen[1],
    }


__all__ = ["X11InputBackend", "locate_window", "require_xdotool", "run_command"]
