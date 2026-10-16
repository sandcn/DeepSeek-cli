"""X11 / macOS 窗口输入后端测试（假 runner，不执行真实外部命令）。

覆盖：xdotool 命令构造（移动/点击/双击/拖动链/滚轮/组合键/文本与换行）、
修饰键按下与释放、工具缺失与命令失败的错误提示；macOS 的动作分发、
AppleScript 键盘/文本脚本构造、cliclick 鼠标命令构造与滚轮回落说明。
"""

from __future__ import annotations

import types

import pytest

from src.tools._window_input import macos as macos_module
from src.tools._window_input import x11 as x11_module
from src.tools._window_input.action import build_action
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.keys import parse_shortcut
from src.tools._window_input.macos import (
    AppleScriptKeyboardDriver,
    CliclickMouseDriver,
    MacOSInputBackend,
    resolve_mouse_driver,
)
from src.tools._window_input.result import InputError, NoWindowError
from src.tools._window_input.x11 import X11InputBackend, require_xdotool


class FakeRunner:
    """记录命令的假命令执行器。"""

    def __init__(self, returncode=0):
        self.commands = []
        self.returncode = returncode

    def __call__(self, command):
        self.commands.append(list(command))
        return types.SimpleNamespace(returncode=self.returncode, stdout="",
                                     stderr="")


@pytest.fixture(autouse=True)
def _fake_xdotool(monkeypatch):
    monkeypatch.setattr(x11_module, "require_xdotool",
                        lambda: "/usr/bin/xdotool")
    monkeypatch.setattr(
        x11_module.shutil, "which",
        lambda name: "/usr/bin/xdotool" if name == "xdotool" else None,
    )


def _x11_target():
    return x11_module._X11Target(
        window_id="12345", pid=999, title="Editor",
        frame=WindowFrame(screen_x=50, screen_y=60, width=400, height=300),
    )


def _x11_backend(runner):
    return X11InputBackend(locator=lambda pid, runner=None: _x11_target(),
                           runner=runner)


def _last_command(runner, contains):
    matches = [cmd for cmd in runner.commands if contains in cmd]
    assert matches, f"未找到包含 {contains} 的命令: {runner.commands}"
    return matches[-1]


# ── X11：鼠标 ───────────────────────────────────────────

def test_x11_move_uses_screen_coordinates():
    runner = FakeRunner()
    result = _x11_backend(runner).send(999, build_action("move", {"x": 10, "y": 20}))
    command = _last_command(runner, "mousemove")
    assert command[:5] == ["/usr/bin/xdotool", "mousemove", "--sync", "60", "80"]
    assert result.detail["screen_x"] == 60 and result.detail["screen_y"] == 80


def test_x11_click_center_and_button_3():
    runner = FakeRunner()
    _x11_backend(runner).send(999, build_action("click", {"button": "right"}))
    command = _last_command(runner, "click")
    assert command[-1] == "3"
    move = _last_command(runner, "mousemove")
    assert move[3:5] == ["250", "210"]  # 窗口中心 (50+200, 60+150)


def test_x11_double_click_uses_repeat():
    runner = FakeRunner()
    _x11_backend(runner).send(999, build_action("click", {"count": 2}))
    command = _last_command(runner, "click")
    assert "--repeat" in command
    assert command[command.index("--repeat") + 1] == "2"
    assert command[-1] == "1"


def test_x11_drag_command_chain():
    runner = FakeRunner()
    result = _x11_backend(runner).send(999, build_action("drag", {
        "from_x": 0, "from_y": 0, "to_x": 100, "to_y": 0, "steps": 4,
        "duration": 0.2,
    }))
    command = _last_command(runner, "mousedown")
    assert command[0] == "/usr/bin/xdotool"
    assert "mousedown" in command and "mouseup" in command
    assert command.count("mousemove") >= 5
    assert command.count("sleep") >= 1
    assert result.detail["to_screen_x"] == 150 and result.detail["to_screen_y"] == 60


def test_x11_scroll_uses_wheel_buttons():
    runner = FakeRunner()
    _x11_backend(runner).send(999, build_action("scroll", {"direction": "up", "amount": 4}))
    command = _last_command(runner, "click")
    assert command[command.index("--repeat") + 1] == "4"
    assert command[-1] == "4"


def test_x11_horizontal_scroll_button():
    runner = FakeRunner()
    _x11_backend(runner).send(999, build_action("scroll", {"direction": "right"}))
    assert _last_command(runner, "click")[-1] == "7"


def test_x11_click_with_modifiers_presses_and_releases():
    runner = FakeRunner()
    _x11_backend(runner).send(999, build_action("click", {"modifiers": ["ctrl"]}))
    down = _last_command(runner, "keydown")
    up = _last_command(runner, "keyup")
    assert down[-1] == "ctrl"
    assert up[-1] == "ctrl"
    assert runner.commands.index(down) < runner.commands.index(up)


# ── X11：键盘与文本 ─────────────────────────────────────

def test_x11_key_combo():
    runner = FakeRunner()
    result = _x11_backend(runner).send(999, build_action("key", {"key": "ctrl+shift+s"}))
    down = _last_command(runner, "keydown")
    up = _last_command(runner, "keyup")
    assert down[-1] == "ctrl+shift+s"
    assert up[-1] == "ctrl+shift+s"
    assert runner.commands.index(down) < runner.commands.index(up)
    assert result.detail["xdotool_key"] == "ctrl+shift+s"


def test_x11_key_named_uses_keysym():
    runner = FakeRunner()
    _x11_backend(runner).send(999, build_action("key", {"key": "page_down"}))
    assert _last_command(runner, "keydown")[-1] == "Next"
    assert _last_command(runner, "keyup")[-1] == "Next"


def test_x11_type_splits_lines():
    runner = FakeRunner()
    _x11_backend(runner).send(999, build_action("type", {"text": "ab\ncd"}))
    types_commands = [cmd for cmd in runner.commands if "type" in cmd]
    assert [cmd[-1] for cmd in types_commands] == ["ab", "cd"]
    assert any(cmd[-1] == "Return" for cmd in runner.commands)


def test_x11_type_uses_double_dash_separator():
    runner = FakeRunner()
    _x11_backend(runner).send(999, build_action("type", {"text": "-x"}))
    command = [cmd for cmd in runner.commands if "type" in cmd][-1]
    assert command[-2:] == ["--", "-x"]


# ── X11：错误路径 ───────────────────────────────────────

def test_x11_no_window_raises():
    backend = X11InputBackend(locator=lambda pid, runner=None: None,
                              runner=FakeRunner())
    with pytest.raises(NoWindowError):
        backend.send(999, build_action("click", {}))


def test_x11_command_failure_raises():
    runner = FakeRunner(returncode=1)
    with pytest.raises(InputError):
        _x11_backend(runner).send(999, build_action("click", {}))


def test_x11_missing_xdotool_message(monkeypatch):
    monkeypatch.setattr(x11_module.shutil, "which", lambda name: None)
    with pytest.raises(InputError) as excinfo:
        require_xdotool()
    assert "apt install xdotool" in str(excinfo.value)


# ── macOS ───────────────────────────────────────────────

class FakeMouseDriver:
    name = "fake"

    def __init__(self):
        self.calls = []

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def click(self, x, y, button, count, modifiers):
        self.calls.append(("click", x, y, button, count, tuple(modifiers)))

    def drag(self, start, end, waypoints, button, interval, modifiers):
        self.calls.append(("drag", start, end, len(waypoints), button, interval))

    def scroll(self, x, y, direction, amount, modifiers):
        self.calls.append(("scroll", x, y, direction, amount))
        return None


class FakeKeyboardDriver:
    name = "fake"

    def __init__(self):
        self.calls = []

    def key(self, shortcut, phase):
        self.calls.append(("key", shortcut.display(), phase))

    def text(self, text):
        self.calls.append(("text", text))


def _mac_backend(mouse, keyboard, mouse_frame=None):
    def locator(_pid):
        return macos_module._MacTarget(
            number=7, pid=555, title="Safari",
            frame=mouse_frame or WindowFrame(10, 20, 800, 600),
        )
    return MacOSInputBackend(locator=locator, runner=FakeRunner(),
                             mouse=mouse, keyboard=keyboard)


def test_macos_click_dispatches_with_screen_coordinates():
    mouse, keyboard = FakeMouseDriver(), FakeKeyboardDriver()
    result = _mac_backend(mouse, keyboard).send(
        555, build_action("click", {"button": "right", "count": 2, "x": 5, "y": 6}))
    assert mouse.calls == [("click", 15, 26, "right", 2, ())]
    assert result.window_pid == 555 and result.window_title == "Safari"


def test_macos_move_center_default():
    mouse, keyboard = FakeMouseDriver(), FakeKeyboardDriver()
    _mac_backend(mouse, keyboard).send(555, build_action("move", {"x": 0, "y": 0}))
    assert mouse.calls == [("move", 10, 20)]


def test_macos_drag_scroll_key_type_dispatch():
    mouse, keyboard = FakeMouseDriver(), FakeKeyboardDriver()
    backend = _mac_backend(mouse, keyboard)
    backend.send(555, build_action("drag", {
        "from_x": 0, "from_y": 0, "to_x": 10, "to_y": 10, "steps": 3,
        "duration": 0.3,
    }))
    assert mouse.calls[-1][0] == "drag"
    assert mouse.calls[-1][3] == 3

    backend.send(555, build_action("scroll", {"direction": "down", "amount": 2}))
    assert mouse.calls[-1] == ("scroll", 410, 320, "down", 2)

    backend.send(555, build_action("key", {"key": "cmd+s"}))
    assert keyboard.calls[-1] == ("key", "meta+s", "press")

    backend.send(555, build_action("type", {"text": "hi"}))
    assert keyboard.calls[-1] == ("text", "hi")


def test_macos_no_window_raises():
    backend = MacOSInputBackend(locator=lambda pid: None, runner=FakeRunner(),
                                mouse=FakeMouseDriver(),
                                keyboard=FakeKeyboardDriver())
    with pytest.raises(NoWindowError):
        backend.send(555, build_action("click", {}))


# ── macOS：AppleScript 键盘驱动 ─────────────────────────

def test_applescript_key_and_text_commands(monkeypatch):
    monkeypatch.setattr(macos_module.shutil, "which",
                        lambda name: "/usr/bin/osascript")
    runner = FakeRunner()
    driver = AppleScriptKeyboardDriver(runner)
    driver.key(parse_shortcut("ctrl+shift+s"), "press")
    driver.text("a\nb")
    driver.key(parse_shortcut("enter"), "press")

    scripts = [cmd[-1] for cmd in runner.commands]
    assert 'tell application "System Events" to keystroke "s" using ' \
           "{control down, shift down}" in scripts
    assert 'tell application "System Events" to keystroke "a"' in scripts
    assert "key code 36" in scripts[2]
    assert 'tell application "System Events" to keystroke "b"' in scripts


def test_applescript_escapes_quotes(monkeypatch):
    monkeypatch.setattr(macos_module.shutil, "which",
                        lambda name: "/usr/bin/osascript")
    runner = FakeRunner()
    AppleScriptKeyboardDriver(runner).text('say "hi"\\')
    script = runner.commands[-1][-1]
    assert '\\"hi\\"' in script


def test_applescript_unsupported_key_raises(monkeypatch):
    monkeypatch.setattr(macos_module.shutil, "which",
                        lambda name: "/usr/bin/osascript")
    with pytest.raises(InputError):
        AppleScriptKeyboardDriver(FakeRunner()).key(
            parse_shortcut("print_screen"), "press")


def test_applescript_failure_reports_permission(monkeypatch):
    monkeypatch.setattr(macos_module.shutil, "which",
                        lambda name: "/usr/bin/osascript")
    driver = AppleScriptKeyboardDriver(FakeRunner(returncode=1))
    with pytest.raises(InputError) as excinfo:
        driver.key(parse_shortcut("enter"), "press")
    assert "辅助功能" in str(excinfo.value)


# ── macOS：cliclick 鼠标驱动 ────────────────────────────

def test_cliclick_click_and_drag_commands():
    runner = FakeRunner()
    driver = CliclickMouseDriver(runner, path="/opt/cliclick")
    driver.click(10, 20, "right", 1, ())
    driver.drag((0, 0), (5, 5), [(2, 2), (4, 4)], "left", 0.0, ())
    assert runner.commands[0] == ["/opt/cliclick", "rc:10,20"]
    drag_command = runner.commands[1]
    assert drag_command[0] == "/opt/cliclick"
    assert "dd:0,0" in drag_command
    assert "dm:5,5" in drag_command
    assert drag_command[-1] == "du:5,5"


def test_cliclick_right_button_drag_rejected():
    driver = CliclickMouseDriver(FakeRunner(), path="/opt/cliclick")
    with pytest.raises(InputError):
        driver.drag((0, 0), (1, 1), [], "right", 0.0, ())


def test_cliclick_scroll_falls_back_to_page_key(monkeypatch):
    monkeypatch.setattr(macos_module.shutil, "which",
                        lambda name: "/usr/bin/osascript")
    runner = FakeRunner()
    driver = CliclickMouseDriver(runner, path="/opt/cliclick")
    approximation = driver.scroll(0, 0, "down", 3, ())
    assert approximation == "page_key"
    assert "key code" in runner.commands[-1][-1]


def test_resolve_mouse_driver_prefers_quartz(monkeypatch):
    monkeypatch.setattr(macos_module.QuartzMouseDriver, "available",
                        lambda self: True)
    driver = resolve_mouse_driver(FakeRunner())
    assert driver.name == "quartz"


def test_resolve_mouse_driver_falls_back_to_cliclick(monkeypatch):
    monkeypatch.setattr(macos_module.QuartzMouseDriver, "available",
                        lambda self: False)
    monkeypatch.setattr(macos_module.shutil, "which",
                        lambda name: "/opt/cliclick" if name == "cliclick" else None)
    assert isinstance(resolve_mouse_driver(FakeRunner()), CliclickMouseDriver)


def test_resolve_mouse_driver_missing_tools(monkeypatch):
    monkeypatch.setattr(macos_module.QuartzMouseDriver, "available",
                        lambda self: False)
    monkeypatch.setattr(macos_module.shutil, "which", lambda name: None)
    with pytest.raises(InputError) as excinfo:
        resolve_mouse_driver(FakeRunner())
    assert "pyobjc" in str(excinfo.value)


class _FakeQuartz:
    """极简 Quartz 桩：记录事件与修饰键标志。"""

    kCGEventMouseMoved = 5
    kCGEventLeftMouseDown = 1
    kCGEventLeftMouseUp = 2
    kCGEventLeftMouseDragged = 6
    kCGEventRightMouseDown = 3
    kCGEventRightMouseUp = 4
    kCGEventRightMouseDragged = 7
    kCGEventOtherMouseDown = 25
    kCGEventOtherMouseUp = 26
    kCGEventOtherMouseDragged = 27
    kCGMouseButtonLeft = 0
    kCGScrollEventUnitLine = 1
    kCGHIDEventTap = 0

    def __init__(self):
        self.events = []
        self.flags = []

    def CGEventCreateMouseEvent(self, proxy, event_type, position, button):
        return {"type": event_type, "pos": position, "button": button}

    def CGEventCreateScrollWheelEvent(self, proxy, unit, count, *deltas):
        return {"scroll": deltas}

    def CGEventSetFlags(self, event, flags):
        self.flags.append(flags)

    def CGEventPost(self, tap, event):
        self.events.append(event)

    def CFRelease(self, event):
        return None


def _quartz_driver(quartz):
    driver = macos_module.QuartzMouseDriver()
    driver._loaded = True
    driver._quartz = quartz
    return driver


def test_quartz_click_applies_modifier_flags():
    quartz = _FakeQuartz()
    _quartz_driver(quartz).click(10, 20, "left", 2, ("ctrl", "shift"))
    mouse_events = [e for e in quartz.events if "type" in e]
    assert len(mouse_events) == 5  # 1 次移动 + 2 次点击（down/up）
    expected = 0x00040000 | 0x00020000
    assert quartz.flags == [expected] * 4


def test_quartz_click_without_modifiers_sets_no_flags():
    quartz = _FakeQuartz()
    _quartz_driver(quartz).click(1, 1, "right", 1, ())
    assert quartz.flags == []
    assert any(e.get("type") == _FakeQuartz.kCGEventRightMouseDown
               for e in quartz.events)


def test_quartz_drag_and_scroll_events():
    quartz = _FakeQuartz()
    driver = _quartz_driver(quartz)
    driver.drag((0, 0), (10, 10), [(5, 5)], "left", 0.0, ("alt",))
    assert any(e.get("type") == _FakeQuartz.kCGEventLeftMouseDragged
               for e in quartz.events)
    assert quartz.flags == [0x00080000] * 4

    quartz.events.clear()
    assert driver.scroll(0, 0, "down", 3, ()) is None
    assert any("scroll" in e for e in quartz.events)


def test_quartz_unavailable_raises():
    driver = macos_module.QuartzMouseDriver()
    driver._loaded = True
    driver._quartz = None
    assert driver.available() is False
    with pytest.raises(InputError):
        driver.move(0, 0)
