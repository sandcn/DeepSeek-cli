"""X11 窗口输入后端（xdotool）。

窗口定位与截图后端共用同一套规则（``find_process_windows``：xdotool 搜索
优先、wmctrl 回退），坐标换算为屏幕像素后由 xdotool 注入。

能力映射：

  - move / hover / click / drag / scroll → ``xdotool mousemove`` /
    ``mousemove_relative``（相对移动）/ ``click`` / ``mousedown`` /
    ``mouseup``（滚轮 = 按钮 4/5，水平滚轮 = 6/7）；``hover`` 用 ``sleep``
    停留，``click`` 的 ``hold`` 用 ``mousedown`` + ``sleep`` + ``mouseup``
    实现长按
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
import threading
import time
from dataclasses import dataclass

from .._screenshot.windows import DEFAULT_SELECTOR, pick_window
from .._screenshot.x11 import list_windows as list_platform_windows
from .action import (
    DEFAULT_CLICK_INTERVAL,
    ClickAction,
    DragAction,
    HoverAction,
    InputAction,
    KeyAction,
    MoveAction,
    Point,
    ReleaseAction,
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
#: key 连发（repeat）的默认间隔（秒）——未显式指定 interval 时使用
_KEY_REPEAT_INTERVAL = 0.05

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
        #: 长按 / 分阶段按下留下的「仍处于按下状态」的键与鼠标按钮，
        #: 供 op=release 兜底释放。
        self._held_keys: dict[str, int] = {}
        self._held_buttons: set[str] = set()
        self._state_lock = threading.RLock()
        #: 输入会话（sequence / replay）期间缓存窗口定位结果。
        self._session_lock = threading.RLock()
        self._session_depth = 0
        self._locate_cache: dict[tuple, _X11Target] = {}

    def supports(self) -> bool:
        return (
            not sys.platform.startswith(("win", "cygwin", "msys"))
            and sys.platform != "darwin"
        )

    def begin_session(self, pid: int | None = None) -> bool:
        """开启输入会话（缓存窗口定位结果，减少 xdotool 定位 / 激活开销）。"""
        if not self.supports():
            return False
        with self._session_lock:
            self._session_depth += 1
        return True

    def end_session(self) -> None:
        """结束输入会话并清空缓存。"""
        with self._session_lock:
            if self._session_depth > 0:
                self._session_depth -= 1
            if self._session_depth == 0:
                self._locate_cache.clear()

    def locate(self, pid: int, window: str | None = None) -> _X11Target | None:
        """定位 ``pid``（含子进程）的目标窗口（无副作用；供路由决策与注入复用）。

        ``window`` 为窗口选择器（见 ``_screenshot.windows``）；缺省取主窗口。
        输入会话期间结果被缓存（序列里的连续动作不必每次重新搜索窗口）。
        """
        key = (pid, window or "")
        with self._session_lock:
            if self._session_depth > 0:
                cached = self._locate_cache.get(key)
                if cached is not None:
                    return cached
        target = (self._locate(pid, window, runner=self._run) if window
                  else self._locate(pid, runner=self._run))
        if target is not None:
            with self._session_lock:
                if self._session_depth > 0:
                    self._locate_cache[key] = target
        return target

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
        if isinstance(action, HoverAction):
            return self._hover(xdotool, target, action)
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
        if isinstance(action, ReleaseAction):
            return self._release(xdotool, action)
        raise ActionError(f"X11 后端不支持的动作: {action.name}")  # pragma: no cover

    def _move(self, xdotool: str, target: _X11Target, action: MoveAction) -> dict:
        if action.uses_relative_events:
            return self._move_relative_events(xdotool, action)
        if action.is_relative:
            dx = int(action.dx or 0)
            dy = int(action.dy or 0)
            start = self._current_location(xdotool)
            end = None if start is None else (start[0] + dx, start[1] + dy)
            with self._hold_all(xdotool, action.modifiers, action.hold_keys):
                self._perform_move(xdotool, start, end, action,
                                   relative=(dx, dy), absolute=None)
            detail = {"relative": True, "dx": dx, "dy": dy}
            if end is not None:
                detail["screen_x"], detail["screen_y"] = end
            self._annotate_smooth(detail, action)
            return detail
        point = resolve_point(action.x, action.y, target.frame.width,
                              target.frame.height, label="移动坐标")
        screen = target.frame.to_screen(point)
        start = self._current_location(xdotool)
        with self._hold_all(xdotool, action.modifiers, action.hold_keys):
            self._perform_move(xdotool, start, screen, action,
                               relative=None, absolute=screen)
        detail = _point_detail(point, screen)
        self._annotate_smooth(detail, action)
        return detail

    def _move_relative_events(self, xdotool: str,
                              action: MoveAction) -> dict:
        """发送纯相对位移（``xdotool mousemove_relative``），供游戏视角使用。

        把 ``(dx, dy)`` 拆成 ``steps`` 个等分相对移动，累积余数保持总量精确；
        ``interval`` 控制步间间隔。相对移动不依赖光标绝对位置。
        """
        dx = int(action.dx or 0)
        dy = int(action.dy or 0)
        steps = max(int(action.steps), 1)
        interval = max(float(action.interval or 0.0), 0.0)
        remaining_x, remaining_y = dx, dy
        command = [xdotool]
        for index in range(steps):
            left = steps - index
            step_x = int(round(remaining_x / left)) if left else remaining_x
            step_y = int(round(remaining_y / left)) if left else remaining_y
            remaining_x -= step_x
            remaining_y -= step_y
            command += ["mousemove_relative", "--sync", str(step_x), str(step_y)]
            if interval and index + 1 < steps:
                command += ["sleep", f"{interval:.3f}"]
        with self._hold_all(xdotool, action.modifiers, action.hold_keys):
            self._checked(command, "鼠标相对位移事件")
        return {
            "relative": True,
            "relative_event": True,
            "dx": dx,
            "dy": dy,
            "steps": steps,
            "events": steps,
        }

    def _perform_move(self, xdotool: str, start, end, action: MoveAction, *,
                      relative, absolute) -> None:
        """移动鼠标：可用起点且需平滑时按插值命令链分步移动，否则一步到位。"""
        if action.is_smooth and start is not None and end is not None:
            self._checked(self._move_command(xdotool, start, end, action),
                          "鼠标移动")
            return
        if relative is not None:
            self._checked([xdotool, "mousemove_relative", "--sync",
                           str(relative[0]), str(relative[1])],
                          "鼠标相对移动")
            return
        self._checked([xdotool, "mousemove", "--sync",
                       str(absolute[0]), str(absolute[1])], "鼠标移动")

    @staticmethod
    def _move_command(xdotool: str, start, end, action: MoveAction) -> list[str]:
        """构造分步移动的 xdotool 命令链（``mousemove --sync x y sleep t`` 重复）。"""
        steps = max(int(action.steps), 2)
        interval = (action.duration / steps) if action.duration > 0 else 0.0
        command = [xdotool]
        for point in interpolate(Point(start[0], start[1]), Point(end[0], end[1]),
                                 steps):
            command += ["mousemove", "--sync", str(point.x), str(point.y)]
            if interval > 0:
                command += ["sleep", f"{interval:.3f}"]
        return command

    def _current_location(self, xdotool: str) -> tuple[int, int] | None:
        """读取当前鼠标屏幕坐标（``xdotool getmouselocation --shell``）。"""
        completed = self._run([xdotool, "getmouselocation", "--shell"])
        if completed is None or completed.returncode != 0:
            return None
        x = y = None
        for line in (completed.stdout or "").splitlines():
            stripped = line.strip()
            try:
                if stripped.startswith("X="):
                    x = int(stripped[2:])
                elif stripped.startswith("Y="):
                    y = int(stripped[2:])
            except ValueError:
                continue
        if x is None or y is None:
            return None
        return x, y

    @staticmethod
    def _annotate_smooth(detail: dict, action: MoveAction) -> None:
        if action.is_smooth:
            detail["smooth"] = {"duration": action.duration, "steps": action.steps}

    def _hover(self, xdotool: str, target: _X11Target, action: HoverAction) -> dict:
        """悬停：``mousemove`` 后 ``sleep dwell``（单条 xdotool 命令链完成）。"""
        point = resolve_point(action.x, action.y, target.frame.width,
                              target.frame.height, label="悬停坐标")
        screen = target.frame.to_screen(point)
        command = [xdotool, "mousemove", "--sync", str(screen[0]), str(screen[1])]
        if action.dwell > 0:
            command += ["sleep", f"{action.dwell:.3f}"]
        with self._hold_all(xdotool, action.modifiers, action.hold_keys):
            self._checked(command, "鼠标悬停")
        detail = _point_detail(point, screen)
        detail.update({"hover": True, "dwell": action.dwell})
        return detail

    def _click(self, xdotool: str, target: _X11Target, action: ClickAction) -> dict:
        point = resolve_point(action.x, action.y, target.frame.width,
                              target.frame.height, label="点击坐标")
        screen = target.frame.to_screen(point)
        number = _BUTTON_NUMBERS[action.button]
        if action.phase == "press" or action.x is not None or action.y is not None:
            self._checked(
                [xdotool, "mousemove", "--sync", str(screen[0]), str(screen[1])],
                "鼠标移动",
            )
        count = action.effective_count
        delay_ms = (int(round(action.interval * 1000))
                    or _DOUBLE_CLICK_DELAY_MS)
        if action.phase == "down":
            command = [xdotool]
            for index in range(count):
                if index and action.interval > 0:
                    command += ["sleep", f"{action.interval:.3f}"]
                command += ["mousedown", str(number)]
        elif action.phase == "up":
            command = [xdotool]
            for index in range(count):
                if index and action.interval > 0:
                    command += ["sleep", f"{action.interval:.3f}"]
                command += ["mouseup", str(number)]
        elif action.hold > 0:
            # 长按：mousedown → sleep hold → mouseup（每次点击重复）
            command = [xdotool]
            for index in range(count):
                if index:
                    command += ["sleep", f"{action.interval:.3f}"]
                command += ["mousedown", str(number),
                            "sleep", f"{action.hold:.3f}",
                            "mouseup", str(number)]
        else:
            command = [xdotool, "click"]
            if count > 1:
                command += ["--repeat", str(count), "--delay", str(delay_ms)]
            command.append(str(number))
        with self._hold_all(xdotool, action.modifiers, action.hold_keys):
            self._checked(command, "鼠标点击")
        if action.phase == "down":
            self._remember_button(action.button, pressed=True)
        elif action.phase == "up":
            self._remember_button(action.button, pressed=False)
        detail = _point_detail(point, screen)
        detail.update({"button": action.button, "count": action.count})
        if action.phase != "press":
            detail["phase"] = action.phase
            if action.effective_count != action.count:
                detail["effective_count"] = action.effective_count
        if action.hold > 0:
            detail["hold"] = action.hold
        if action.interval != DEFAULT_CLICK_INTERVAL:
            detail["interval"] = action.interval
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
        with self._hold_all(xdotool, action.modifiers, action.hold_keys):
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
        with self._hold_all(xdotool, action.modifiers, action.hold_keys):
            self._checked(command, "滚轮滚动")
        detail = _point_detail(point, screen)
        detail.update({"direction": action.direction, "amount": action.amount})
        return detail

    def _key(self, xdotool: str, action: KeyAction) -> dict:
        combo = _xdotool_combo(action)
        # 分开发送按下与弹起（xdotool key 会把两者合并成一条命令）：
        # phase=down/up 可只发送其中之一，用于长按或单独释放。
        repeats = action.effective_repeat
        interval = action.interval if action.interval > 0 else _KEY_REPEAT_INTERVAL
        extra_hold = tuple(k for k in action.hold_keys
                           if k not in action.shortcut.modifiers)
        with self._hold_all(xdotool, (), extra_hold):
            for index in range(repeats):
                if index and interval:
                    time.sleep(interval)
                if action.phase in ("press", "down"):
                    self._checked([xdotool, "keydown", combo], "按键按下")
                if action.is_long_press:
                    time.sleep(action.hold)
                if action.phase in ("press", "up"):
                    self._checked([xdotool, "keyup", combo], "按键弹起")
        if action.phase == "down":
            self._remember_keys(action, pressed=True)
        elif action.phase == "up":
            self._remember_keys(action, pressed=False)
        detail = {
            "key": action.shortcut.display(),
            "xdotool_key": combo,
            "modifiers": list(action.shortcut.modifiers),
            "phase": action.phase,
            "repeat": repeats,
        }
        if action.hold:
            detail["hold"] = action.hold
        if action.interval:
            detail["interval"] = action.interval
        return detail

    def _remember_keys(self, action: KeyAction, *, pressed: bool) -> None:
        """记录 / 清除 ``down`` / ``up`` 阶段的按下状态（供 release 兜底）。"""
        names = [action.shortcut.key, *action.shortcut.modifiers]
        with self._state_lock:
            for name in names:
                if pressed:
                    self._held_keys[name] = 0
                else:
                    self._held_keys.pop(name, None)

    def _remember_button(self, button: str, *, pressed: bool) -> None:
        with self._state_lock:
            if pressed:
                self._held_buttons.add(button)
            else:
                self._held_buttons.discard(button)

    def _release(self, xdotool: str, action: ReleaseAction) -> dict:
        """释放按下的键与鼠标按钮（``op=release``，游戏长按后的兜底清理）。"""
        with self._state_lock:
            held_keys = list(self._held_keys)
            held_buttons = set(self._held_buttons)
        keys = list(action.keys) or held_keys
        buttons = list(action.buttons) or list(held_buttons)
        released_buttons: list[str] = []
        for button in buttons:
            number = _BUTTON_NUMBERS.get(button)
            if number is None:
                continue
            self._checked([xdotool, "mouseup", str(number)], "释放鼠标按钮")
            with self._state_lock:
                self._held_buttons.discard(button)
            released_buttons.append(button)
        released_keys: list[str] = []
        for name in keys:
            keysym = _x11_keysym(name)
            if keysym is None:
                logger.debug("release 无法解析键名: %s", name)
                continue
            self._checked([xdotool, "keyup", keysym], "释放按键")
            with self._state_lock:
                self._held_keys.pop(name, None)
            released_keys.append(name)
        return {
            "scope": "selected" if (action.keys or action.buttons) else "all",
            "released_keys": released_keys,
            "released_buttons": released_buttons,
        }

    def _type(self, xdotool: str, action: TextAction) -> dict:
        """逐行调用 ``xdotool type``；换行 / 制表符转为对应按键。"""
        line = ""
        characters = 0
        with self._hold_all(xdotool, (), action.hold_keys):
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
        return _KeyHoldContext(self, xdotool, modifiers, ())

    def _hold_all(self, xdotool: str, modifiers: tuple[str, ...] = (),
                  hold_keys: tuple[str, ...] = ()):
        """按住一组键（修饰键 + hold_keys，去重后按下、逆序释放）。"""
        return _KeyHoldContext(self, xdotool, modifiers, hold_keys)


class _KeyHoldContext:
    """按住一组键的上下文管理器（xdotool keydown/keyup）。

    支持任意键（修饰键与普通键 / 单字符），用于「动作期间按住 W / Shift」这类
    游戏组合键；未知键名跳过并记日志，不中断注入。
    """

    def __init__(self, backend: X11InputBackend, xdotool: str,
                 modifiers: tuple[str, ...] = (),
                 hold_keys: tuple[str, ...] = ()):
        self._backend = backend
        self._xdotool = xdotool
        names: list[str] = []
        for raw in list(modifiers or ()) + list(hold_keys or ()):
            keysym = _x11_keysym(raw)
            if keysym and keysym not in names:
                names.append(keysym)
        self._names = names

    def __enter__(self) -> None:
        for name in self._names:
            self._backend._checked([self._xdotool, "keydown", name], "按下按键")
        return None

    def __exit__(self, exc_type, exc, tb) -> None:
        for name in reversed(self._names):
            try:
                self._backend._checked([self._xdotool, "keyup", name], "释放按键")
            except InputError:
                logger.debug("释放按键失败: %s", name, exc_info=True)
        return None


#: 兼容旧名（工具层 / 测试此前引用 _ModifierContext）
_ModifierContext = _KeyHoldContext


def _x11_keysym(name: str) -> str | None:
    """规范键名或单字符 → xdotool keysym；无法映射返回 None。"""
    if len(name) == 1:
        return name
    return X11_KEYSYM.get(name)


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
