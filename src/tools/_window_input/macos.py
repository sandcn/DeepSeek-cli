"""macOS 窗口输入后端。

窗口定位与截图后端共用同一套规则（``find_process_window``：pyobjc Quartz
优先、osascript 回退），坐标为该窗口的屏幕坐标起点 + 窗口内像素。

注入实现按可用能力择优：

  - 鼠标（move / click / drag / scroll）：pyobjc ``Quartz.CGEventPost``
    精确合成鼠标事件（推荐）；无 pyobjc 时回退 ``cliclick``（需
    ``brew install cliclick``，其不支持滚轮 → 滚动改用翻页键近似）。
  - 键盘 / 文本（key / type）：pyobjc ``Quartz``（``CGEventCreateKeyboardEvent``）
    精确分离按下 / 弹起（推荐，``phase`` 才可用）；无 pyobjc 时回退
    ``osascript``（System Events 的 ``keystroke`` / ``key code``，仅能
    对修饰键 ``key down``/``key up``）。两条路径都需要「辅助功能」权限。

macOS 对合成输入有限制：无论哪条路径都必须在「系统设置 → 隐私与安全性 →
辅助功能」中为运行本程序的终端授予权限，否则事件会被系统丢弃。
"""

from __future__ import annotations

import ctypes
import logging
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from .._screenshot.macos import find_process_window
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
from .keys import (
    MACOS_KEYCODE,
    MACOS_MODIFIER_NAMES,
    macos_keycode,
    utf16_units,
)
from .result import ActionError, InputError, InputResult, NoWindowError

logger = logging.getLogger(__name__)

#: 单条外部命令超时（秒）
_COMMAND_TIMEOUT = 30.0
#: 拖动轨迹每步最小间隔（秒）
_DRAG_MIN_INTERVAL = 0.01
#: 双击两次点击的间隔（秒）
_DOUBLE_CLICK_INTERVAL = 0.08

#: 鼠标按钮 → Quartz 事件类型与按钮号（CGEvent 使用）
_QUARTZ_BUTTONS: dict[str, tuple[int, int, int, int]] = {
    # button: (down, up, dragged, mouseButton)
    "left": (1, 2, 6, 0),
    "right": (3, 4, 7, 1),
    "middle": (25, 26, 27, 2),
}

#: 鼠标按钮 → cliclick 点击命令
_CLICLICK_CLICK: dict[str, str] = {"left": "c", "right": "rc", "middle": "mc"}

#: 修饰键 → Quartz 事件标志（kCGEventFlagMask*）
_QUARTZ_FLAGS: dict[str, int] = {
    "ctrl": 0x00040000,
    "alt": 0x00080000,
    "shift": 0x00020000,
    "meta": 0x00100000,
}

#: 修饰键 → macOS 虚拟键码（分离按下/弹起时定位物理修饰键）
MACOS_MODIFIER_KEYCODES: dict[str, int] = {
    "ctrl": 59, "shift": 56, "alt": 58, "meta": 55,
}

#: 修饰键 → AppleScript（System Events）名称
MACOS_MODIFIER_APPLESCRIPT: dict[str, str] = {
    "ctrl": "control", "alt": "option", "shift": "shift", "meta": "command",
}


@dataclass
class _MacTarget:
    """定位到的 macOS 窗口（窗口号 + 截图坐标系）。"""

    number: int | None
    pid: int
    title: str
    frame: WindowFrame


class MacOSInputBackend:
    """macOS 平台窗口输入后端。"""

    name = "macos"

    def __init__(self, locator=None, runner=None, mouse=None, keyboard=None):
        self._locate = locator or locate_window
        self._run = runner or run_command
        self._mouse = mouse
        # 键盘驱动：Quartz（pyobjc）可分离按下/弹起，缺省回退 osascript
        self._keyboard = keyboard or resolve_keyboard_driver(self._run)

    def supports(self) -> bool:
        return sys.platform == "darwin"

    def send(self, pid: int, action: InputAction) -> InputResult:
        """向 ``pid`` 的窗口注入 ``action``，返回注入结果。"""
        target = self._locate(pid)
        if target is None:
            raise NoWindowError(
                f"进程 {pid} 及其子进程没有可接收输入的可见窗口"
                f"（纯命令行进程没有图形窗口）"
            )
        mouse = self._mouse or resolve_mouse_driver(self._run)
        detail = self._dispatch(mouse, target, action)
        return InputResult(
            action=action.name,
            backend=self.name,
            window_pid=target.pid,
            window_title=target.title,
            detail=detail,
        )

    # ── 动作分发 ─────────────────────────────────────────

    def _dispatch(self, mouse, target: _MacTarget, action: InputAction) -> dict:
        if isinstance(action, MoveAction):
            return self._move(mouse, target, action)
        if isinstance(action, ClickAction):
            return self._click(mouse, target, action)
        if isinstance(action, DragAction):
            return self._drag(mouse, target, action)
        if isinstance(action, ScrollAction):
            return self._scroll(mouse, target, action)
        if isinstance(action, KeyAction):
            return self._key(action)
        if isinstance(action, TextAction):
            return self._type(action)
        raise ActionError(f"macOS 后端不支持的动作: {action.name}")  # pragma: no cover

    def _move(self, mouse, target: _MacTarget, action: MoveAction) -> dict:
        point = resolve_point(action.x, action.y, target.frame.width,
                              target.frame.height, label="移动坐标")
        screen = target.frame.to_screen(point)
        mouse.move(*screen)
        return _point_detail(point, screen)

    def _click(self, mouse, target: _MacTarget, action: ClickAction) -> dict:
        point = resolve_point(action.x, action.y, target.frame.width,
                              target.frame.height, label="点击坐标")
        screen = target.frame.to_screen(point)
        mouse.click(screen[0], screen[1], action.button, action.count,
                    action.modifiers)
        detail = _point_detail(point, screen)
        detail.update({"button": action.button, "count": action.count})
        return detail

    def _drag(self, mouse, target: _MacTarget, action: DragAction) -> dict:
        frame = target.frame
        start = resolve_point(action.from_x, action.from_y, frame.width, frame.height,
                              label="拖动起点")
        end = validate_point(Point(action.to_x, action.to_y), frame.width, frame.height,
                             label="拖动终点")
        start_screen = frame.to_screen(start)
        end_screen = frame.to_screen(end)
        waypoints = [frame.to_screen(point)
                     for point in interpolate(start, end, action.steps)]
        interval = _drag_interval(action)
        mouse.drag(start_screen, end_screen, waypoints, action.button, interval,
                   action.modifiers)
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

    def _scroll(self, mouse, target: _MacTarget, action: ScrollAction) -> dict:
        point = resolve_point(action.x, action.y, target.frame.width,
                              target.frame.height, label="滚动坐标")
        screen = target.frame.to_screen(point)
        approximation = mouse.scroll(screen[0], screen[1], action.direction,
                                     action.amount, action.modifiers)
        detail = _point_detail(point, screen)
        detail.update({"direction": action.direction, "amount": action.amount})
        if approximation:
            detail["approximation"] = approximation
        return detail

    def _key(self, action: KeyAction) -> dict:
        driver = self._keyboard
        driver.key(action.shortcut, action.phase)
        return {
            "key": action.shortcut.display(),
            "modifiers": list(action.shortcut.modifiers),
            "phase": action.phase,
        }

    def _type(self, action: TextAction) -> dict:
        driver = self._keyboard
        driver.text(action.text)
        return {"text": action.text, "characters": len(action.text)}


# ── 鼠标驱动 ────────────────────────────────────────────

def resolve_mouse_driver(run):
    """选择可用的鼠标驱动：Quartz（pyobjc）优先，cliclick 回退。

    Raises:
        InputError: 两者都不可用，给出安装提示。
    """
    quartz = QuartzMouseDriver()
    if quartz.available():
        return quartz
    cliclick = shutil.which("cliclick")
    if cliclick:
        return CliclickMouseDriver(run, path=cliclick)
    raise InputError(
        "macOS 鼠标注入需要 pyobjc（pip install pyobjc，推荐）或 cliclick"
        "（brew install cliclick）；两者都未安装时无法注入鼠标事件"
    )


class QuartzMouseDriver:
    """基于 pyobjc Quartz 的鼠标注入（CGEventPost，精确定位与滚轮）。"""

    name = "quartz"

    def __init__(self):
        self._quartz = None
        self._loaded = False

    def _module(self):
        if not self._loaded:
            self._loaded = True
            try:
                import Quartz
            except ImportError:
                self._quartz = None
            else:
                self._quartz = Quartz
        return self._quartz

    def available(self) -> bool:
        return self._module() is not None

    def move(self, x: int, y: int) -> None:
        quartz = self._require()
        event = quartz.CGEventCreateMouseEvent(
            None, quartz.kCGEventMouseMoved, (x, y), quartz.kCGMouseButtonLeft)
        self._post(quartz, event)

    def click(self, x: int, y: int, button: str, count: int,
              modifiers: tuple[str, ...]) -> None:
        quartz = self._require()
        down, up, _dragged, mouse_button = _QUARTZ_BUTTONS[button]
        self._move(quartz, x, y, mouse_button)
        for index in range(count):
            if index:
                time.sleep(_DOUBLE_CLICK_INTERVAL)
            self._post(quartz, self._flag_event(
                quartz, quartz.CGEventCreateMouseEvent(None, down, (x, y), mouse_button),
                modifiers))
            self._post(quartz, self._flag_event(
                quartz, quartz.CGEventCreateMouseEvent(None, up, (x, y), mouse_button),
                modifiers))

    def drag(self, start: tuple[int, int], end: tuple[int, int],
             waypoints: list[tuple[int, int]], button: str, interval: float,
             modifiers: tuple[str, ...]) -> None:
        quartz = self._require()
        down_type, up_type, dragged, mouse_button = _QUARTZ_BUTTONS[button]
        self._move(quartz, start[0], start[1], mouse_button)
        self._post(quartz, self._flag_event(
            quartz,
            quartz.CGEventCreateMouseEvent(None, down_type, start, mouse_button),
            modifiers))
        for point in waypoints:
            self._post(quartz, self._flag_event(
                quartz,
                quartz.CGEventCreateMouseEvent(None, dragged, point, mouse_button),
                modifiers))
            if interval:
                time.sleep(interval)
        self._post(quartz, self._flag_event(
            quartz,
            quartz.CGEventCreateMouseEvent(None, dragged, end, mouse_button),
            modifiers))
        self._post(quartz, self._flag_event(
            quartz,
            quartz.CGEventCreateMouseEvent(None, up_type, end, mouse_button),
            modifiers))

    def scroll(self, x: int, y: int, direction: str, amount: int,
               modifiers: tuple[str, ...]) -> str | None:
        """注入滚轮事件，返回 None（精确滚动，无近似）。"""
        quartz = self._require()
        self.move(x, y)
        vertical = 0
        horizontal = 0
        if direction == "up":
            vertical = amount
        elif direction == "down":
            vertical = -amount
        elif direction == "right":
            horizontal = amount
        else:
            horizontal = -amount
        event = quartz.CGEventCreateScrollWheelEvent(
            None, quartz.kCGScrollEventUnitLine, 2, vertical, horizontal)
        self._post(quartz, self._flag_event(quartz, event, modifiers))
        return None

    # ── 内部 ─────────────────────────────────────────────

    @staticmethod
    def _flag_event(quartz, event, modifiers: tuple[str, ...]):
        """给事件附加修饰键标志（Ctrl+点击 / Shift+拖动等）。"""
        flags = _quartz_flags(modifiers)
        if flags and event is not None:
            try:
                quartz.CGEventSetFlags(event, flags)
            except AttributeError:  # pragma: no cover - 旧版 pyobjc
                logger.debug("CGEventSetFlags 不可用，忽略修饰键: %s", modifiers)
        return event

    def _require(self):
        quartz = self._module()
        if quartz is None:  # pragma: no cover - available() 已检查
            raise InputError("pyobjc（Quartz）不可用")
        return quartz

    def _move(self, quartz, x: int, y: int, mouse_button: int) -> None:
        self._post(quartz, quartz.CGEventCreateMouseEvent(
            None, quartz.kCGEventMouseMoved, (x, y), mouse_button))

    @staticmethod
    def _post(quartz, event) -> None:
        if event is None:
            raise InputError("Quartz 事件创建失败（可能是辅助功能权限被拒绝）")
        try:
            quartz.CGEventPost(quartz.kCGHIDEventTap, event)
        finally:
            try:
                quartz.CFRelease(event)
            except Exception:  # pragma: no cover - 释放失败不影响注入
                logger.debug("CFRelease 失败", exc_info=True)


class CliclickMouseDriver:
    """基于 cliclick 的鼠标注入（无 pyobjc 时的回退；不支持滚轮）。"""

    name = "cliclick"

    def __init__(self, run, path: str = "cliclick"):
        self._run = run
        self._path = path

    def available(self) -> bool:
        return True

    def move(self, x: int, y: int) -> None:
        self._call([f"m:{x},{y}"], "鼠标移动")

    def click(self, x: int, y: int, button: str, count: int,
              modifiers: tuple[str, ...]) -> None:
        actions = _cliclick_modifiers(modifiers, pressed=True)
        actions += [f"{_CLICLICK_CLICK[button]}:{x},{y}"] * max(count, 1)
        actions += _cliclick_modifiers(modifiers, pressed=False)
        self._call(actions, "鼠标点击")

    def drag(self, start: tuple[int, int], end: tuple[int, int],
             waypoints: list[tuple[int, int]], button: str, interval: float,
             modifiers: tuple[str, ...]) -> None:
        if button != "left":
            raise InputError(
                "cliclick 仅支持左键拖动；请安装 pyobjc（pip install pyobjc）"
                "以使用右键/中键拖动"
            )
        actions = _cliclick_modifiers(modifiers, pressed=True)
        actions.append(f"dd:{start[0]},{start[1]}")
        for point in waypoints:
            actions.append(f"dm:{point[0]},{point[1]}")
            if interval:
                actions.append(f"w:{int(interval * 1000)}")
        actions.append(f"dm:{end[0]},{end[1]}")
        actions.append(f"du:{end[0]},{end[1]}")
        actions += _cliclick_modifiers(modifiers, pressed=False)
        self._call(actions, "鼠标拖动")

    def scroll(self, x: int, y: int, direction: str, amount: int,
               modifiers: tuple[str, ...]) -> str | None:
        """cliclick 不支持滚轮：改用翻页键近似，并标记 approximation。"""
        self.move(x, y)
        key = "page_down" if direction in ("down", "right") else "page_up"
        script = (
            'tell application "System Events" to key code '
            f'{MACOS_KEYCODE[key]}'
        )
        self._call_script(script, "翻页")
        return "page_key"

    def _call(self, actions: list[str], label: str) -> None:
        if not actions:
            return
        command = [self._path] + actions
        completed = self._run(command)
        if completed is None:
            raise InputError(f"{label} 失败（cliclick 不可用或超时）: {' '.join(command)}")
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            summary = detail[-1] if detail else f"退出码 {completed.returncode}"
            raise InputError(f"{label} 失败: {summary}")

    def _call_script(self, script: str, label: str) -> None:
        osascript = shutil.which("osascript")
        if not osascript:
            raise InputError(f"{label} 失败：osascript 不可用")
        completed = self._run([osascript, "-e", script])
        if completed is None or completed.returncode != 0:
            raise InputError(
                f"{label} 失败（需要「辅助功能」权限，或 osascript 不可用）"
            )


class AppleScriptKeyboardDriver:
    """基于 osascript（System Events）的键盘/文本注入。"""

    name = "applescript"

    def __init__(self, run):
        self._run = run

    def key(self, shortcut, phase: str) -> None:
        if phase != "press":
            statement = self._phase_statement(shortcut, phase)
            if statement is None:
                raise InputError(
                    "osascript（System Events）只能对修饰键分离按下/弹起；"
                    "普通键请安装 pyobjc（pip install pyobjc）后使用 down/up 阶段"
                )
            self._run_script(statement,
                             "按键按下" if phase == "down" else "按键弹起")
            return
        modifiers = _applescript_modifiers(shortcut.modifiers)
        if shortcut.is_character:
            statement = (f'keystroke "{_escape_applescript(shortcut.key)}"'
                         + modifiers)
        else:
            key_code = (MACOS_KEYCODE.get(shortcut.key)
                        or MACOS_MODIFIER_KEYCODES.get(shortcut.key))
            if key_code is None:
                raise InputError(
                    f"macOS 后端不支持按键: {shortcut.key!r}"
                    f"（该键在 macOS 上无对应虚拟键码）"
                )
            statement = f"key code {key_code}" + modifiers
        self._run_script(statement, "键盘按键")

    def _phase_statement(self, shortcut, phase: str) -> str | None:
        """修饰键的按下/弹起 AppleScript 语句（主键必须是修饰键）。

        主键不是修饰键时返回 None——System Events 无法对普通键单独
        按下/弹起（只能用 ``key code`` 完成一次完整按键）。
        """
        labels: list[str] = []
        for name in shortcut.modifiers:
            label = MACOS_MODIFIER_APPLESCRIPT.get(name)
            if label is None:
                return None
            labels.append(label)
        key_label = MACOS_MODIFIER_APPLESCRIPT.get(shortcut.key)
        if key_label is None:
            return None
        labels.append(key_label)
        direction = "key down" if phase == "down" else "key up"
        return "\n".join(f"{direction} {label}" for label in labels)

    def text(self, text: str) -> None:
        line = ""
        for char in text:
            if char == "\r":
                continue
            if char == "\n":
                self._flush(line)
                line = ""
                self._run_script(f"key code {MACOS_KEYCODE['enter']}", "回车键")
            elif char == "\t":
                self._flush(line)
                line = ""
                self._run_script(f"key code {MACOS_KEYCODE['tab']}", "制表键")
            else:
                line += char
        self._flush(line)

    def _flush(self, text: str) -> None:
        if not text:
            return
        self._run_script(f'keystroke "{_escape_applescript(text)}"', "文本输入")

    def _run_script(self, statement: str, label: str) -> None:
        osascript = shutil.which("osascript")
        if not osascript:
            raise InputError(
                f"{label} 失败：osascript 不可用（macOS 系统自带，"
                f"请检查 PATH 与系统完整性）"
            )
        if "\n" in statement:
            script = 'tell application "System Events"\n' + statement + "\nend tell"
        else:
            script = f'tell application "System Events" to {statement}'
        completed = self._run([osascript, "-e", script])
        if completed is None:
            raise InputError(f"{label} 失败：osascript 执行超时或不可用")
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            summary = detail[-1] if detail else f"退出码 {completed.returncode}"
            raise InputError(
                f"{label} 失败: {summary}（通常是没有「辅助功能」权限："
                f"系统设置 → 隐私与安全性 → 辅助功能）"
            )


# ── 定位与辅助（模块级，便于复用与单测） ────────────────────

def locate_window(pid: int) -> _MacTarget | None:
    """定位 ``pid``（含子进程）的主窗口。"""
    window = find_process_window(pid)
    if window is None:
        return None
    return _MacTarget(
        number=window.number,
        pid=window.pid,
        title=window.title,
        frame=WindowFrame(window.x, window.y, window.width, window.height),
    )


def run_command(command: list[str]):
    """执行外部命令（失败返回 None，不抛异常）。"""
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=_COMMAND_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("命令执行失败 %s: %s", command[0], exc)
        return None


class QuartzKeyboardDriver:
    """基于 pyobjc Quartz 的键盘注入（分离按下/弹起，按物理键位发送）。

    与 osascript 不同，Quartz 可对任意键分别发送按下 / 弹起事件
    （``CGEventCreateKeyboardEvent``），因此 ``phase='down'/'up'`` 与长按
    在 macOS 上同样可用；字符键按 US 布局的物理键位发送，无法定位的字符
    （如中文）退化为 Unicode 事件（等价于 Windows 的 Unicode 注入）。
    """

    name = "quartz"

    def __init__(self):
        self._quartz = None
        self._loaded = False

    def _module(self):
        if not self._loaded:
            self._loaded = True
            try:
                import Quartz
            except ImportError:
                self._quartz = None
            else:
                self._quartz = Quartz
        return self._quartz

    def available(self) -> bool:
        return self._module() is not None

    def key(self, shortcut, phase: str) -> None:
        quartz = self._require()
        resolved = macos_keycode(shortcut.key)
        if resolved is None:
            raise InputError(
                f"macOS 后端不支持按键: {shortcut.key!r}（无对应虚拟键码）"
            )
        code, implicit_shift = resolved
        flags = _quartz_flags(shortcut.modifiers)
        if implicit_shift:
            flags |= _QUARTZ_FLAGS["shift"]
        if phase in ("press", "down"):
            for name in shortcut.modifiers:
                modifier_code = MACOS_MODIFIER_KEYCODES.get(name)
                if modifier_code is not None:
                    self._post(quartz, modifier_code, 0, True)
            self._post(quartz, code, flags, True)
        if phase in ("press", "up"):
            self._post(quartz, code, flags, False)
            for name in reversed(shortcut.modifiers):
                modifier_code = MACOS_MODIFIER_KEYCODES.get(name)
                if modifier_code is not None:
                    self._post(quartz, modifier_code, 0, False)

    def text(self, text: str) -> None:
        quartz = self._require()
        for char in text:
            if char == "\r":
                continue
            if char == "\n":
                self._press(quartz, MACOS_KEYCODE["enter"], 0)
            elif char == "\t":
                self._press(quartz, MACOS_KEYCODE["tab"], 0)
            else:
                self._character(quartz, char)

    def _press(self, quartz, code: int, flags: int) -> None:
        self._post(quartz, code, flags, True)
        self._post(quartz, code, flags, False)

    def _character(self, quartz, char: str) -> None:
        resolved = macos_keycode(char)
        if resolved is not None:
            code, needs_shift = resolved
            self._press(quartz, code, _QUARTZ_FLAGS["shift"] if needs_shift else 0)
            return
        for unit in utf16_units(char):
            self._post_unicode(quartz, unit)

    def _post(self, quartz, code: int, flags: int, key_down: bool) -> None:
        event = quartz.CGEventCreateKeyboardEvent(None, int(code), bool(key_down))
        if event is None:
            raise InputError("Quartz 键盘事件创建失败（可能是辅助功能权限被拒绝）")
        try:
            if flags:
                quartz.CGEventSetFlags(event, int(flags))
            quartz.CGEventPost(quartz.kCGHIDEventTap, event)
        finally:
            self._release(quartz, event)

    def _post_unicode(self, quartz, unit: int) -> None:
        for key_down in (True, False):
            event = quartz.CGEventCreateKeyboardEvent(None, 0, key_down)
            if event is None:
                raise InputError("Quartz 键盘事件创建失败（可能是辅助功能权限被拒绝）")
            try:
                buffer = (ctypes.c_uint16 * 1)(int(unit))
                quartz.CGEventKeyboardSetUnicodeString(event, 1, buffer)
                quartz.CGEventPost(quartz.kCGHIDEventTap, event)
            finally:
                self._release(quartz, event)

    @staticmethod
    def _release(quartz, event) -> None:
        try:
            quartz.CFRelease(event)
        except Exception:  # pragma: no cover - 释放失败不影响注入
            logger.debug("CFRelease 失败", exc_info=True)

    def _require(self):
        quartz = self._module()
        if quartz is None:  # pragma: no cover - available() 已检查
            raise InputError("pyobjc（Quartz）不可用")
        return quartz


def resolve_keyboard_driver(run):
    """选择可用的键盘驱动：Quartz（pyobjc，可分离按下/弹起）优先，osascript 回退。"""
    quartz = QuartzKeyboardDriver()
    if quartz.available():
        return quartz
    return AppleScriptKeyboardDriver(run)


def _quartz_flags(modifiers: tuple[str, ...]) -> int:
    """修饰键 → Quartz 事件标志位（可叠加）。"""
    flags = 0
    for name in modifiers:
        flags |= _QUARTZ_FLAGS.get(name, 0)
    return flags


def _applescript_modifiers(modifiers: tuple[str, ...]) -> str:
    names = [MACOS_MODIFIER_NAMES[name] for name in modifiers
             if name in MACOS_MODIFIER_NAMES]
    if not names:
        return ""
    return " using {" + ", ".join(names) + "}"


def _cliclick_modifiers(modifiers: tuple[str, ...], *, pressed: bool) -> list[str]:
    prefix = "kd" if pressed else "ku"
    mapping = {"ctrl": "ctrl", "alt": "alt", "shift": "shift", "meta": "cmd"}
    return [f"{prefix}:{mapping[name]}" for name in modifiers if name in mapping]


def _escape_applescript(text: str) -> str:
    """转义 AppleScript 字符串字面量中的反斜杠与双引号。"""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _drag_interval(action: DragAction) -> float:
    if action.duration <= 0:
        return 0.0
    return max(action.duration / max(action.steps, 1), _DRAG_MIN_INTERVAL)


def _point_detail(point: Point, screen: tuple[int, int]) -> dict:
    return {
        "x": point.x,
        "y": point.y,
        "screen_x": screen[0],
        "screen_y": screen[1],
    }


__all__ = [
    "AppleScriptKeyboardDriver",
    "CliclickMouseDriver",
    "MacOSInputBackend",
    "QuartzKeyboardDriver",
    "QuartzMouseDriver",
    "locate_window",
    "resolve_keyboard_driver",
    "resolve_mouse_driver",
    "run_command",
]
