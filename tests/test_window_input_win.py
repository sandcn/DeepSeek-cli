"""Windows 窗口输入后端测试（假定位器 + 假驱动，不触碰真实系统调用）。

覆盖：SendInput 路径的坐标换算与事件序列（左/右/中键、双击、修饰键、
拖动轨迹、滚轮、组合键、Unicode 文本）、PostMessage 路径的消息序列与
客户区坐标/屏幕坐标差异、投递方式决策（未取得前台时 auto 回退、显式
sendinput 报错）、无窗口与坐标越界错误。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot import winapi
from src.tools._window_input import win as win_module
from src.tools._window_input.action import build_action
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.result import ActionError, InputError, NoWindowError
from src.tools._window_input.win import WindowsInputBackend, resolve_windows_vk


class FakeDriver:
    """记录调用的假驱动（鸭子类型替身）。"""

    def __init__(self, *, foreground=True, client_origin=(0, 0),
                 activate_grants_foreground=True, child_handle=None,
                 sendinput_error=None):
        self.events = []
        self.foreground = foreground
        self._client_origin = client_origin
        self._activate_grants = activate_grants_foreground
        self._child_handle = child_handle
        self._sendinput_error = sendinput_error

    def is_foreground(self, handle):
        return self.foreground

    def activate(self, handle):
        self.events.append(("activate", handle))
        if self._activate_grants:
            self.foreground = True
        return True

    def child_at(self, handle, screen_x, screen_y):
        self.events.append(("child_at", handle, screen_x, screen_y))
        return self._child_handle or handle

    def move_to(self, x, y):
        if self._sendinput_error is not None:
            raise self._sendinput_error
        self.events.append(("move", x, y))

    def mouse_event(self, flags, data=0):
        self.events.append(("mouse", flags, data))

    def key_event(self, vk, *, key_up):
        self.events.append(("key", vk, key_up))

    def unicode_event(self, code_unit, *, key_up):
        self.events.append(("unicode", code_unit, key_up))

    def post(self, handle, msg, wparam=0, lparam=0):
        self.events.append(("post", handle, msg, wparam, lparam))
        return True

    def client_origin(self, handle):
        return self._client_origin

    def sleep(self, seconds):
        self.events.append(("sleep", seconds))


@pytest.fixture(autouse=True)
def _no_real_dpi_call(monkeypatch):
    """屏蔽真实 DPI 调用（Cygwin 下避免触碰系统 API）。"""
    monkeypatch.setattr(win_module.winapi, "ensure_process_dpi_aware",
                        lambda: True)


def _frame():
    return WindowFrame(screen_x=100, screen_y=200, width=800, height=600)


def _backend(driver, frame=None, pid=1234, title="Game"):
    def locator(_pid):
        return win_module._TargetWindow(handle=777, pid=pid, title=title,
                                        frame=frame or _frame())
    return WindowsInputBackend(locator=locator, driver=driver)


def _moves(driver):
    return [event for event in driver.events if event[0] == "move"]


def _mouses(driver):
    return [event for event in driver.events if event[0] == "mouse"]


def _keys(driver):
    return [event for event in driver.events if event[0] == "key"]


def _posts(driver):
    return [event for event in driver.events if event[0] == "post"]


# ── 定位与投递方式 ───────────────────────────────────────

def test_no_window_raises_no_window_error():
    backend = WindowsInputBackend(locator=lambda pid: None, driver=FakeDriver())
    with pytest.raises(NoWindowError):
        backend.send(1234, build_action("click", {}))


def test_uses_sendinput_when_already_foreground():
    driver = FakeDriver(foreground=True)
    result = _backend(driver).send(1234, build_action("click", {}))
    assert result.detail["delivery"] == "sendinput"
    assert result.window_pid == 1234
    assert result.window_title == "Game"


def test_auto_falls_back_to_message_without_foreground():
    driver = FakeDriver(foreground=False, activate_grants_foreground=False)
    result = _backend(driver).send(1234, build_action("click", {}))
    assert result.detail["delivery"] == "message"
    assert _posts(driver)


def test_explicit_sendinput_without_foreground_raises():
    driver = FakeDriver(foreground=False, activate_grants_foreground=False)
    with pytest.raises(InputError) as excinfo:
        _backend(driver).send(1234, build_action("click", {"method": "sendinput"}))
    assert "置于前台" in str(excinfo.value)


def test_auto_falls_back_to_message_when_sendinput_blocked():
    """SendInput 被系统拒绝（如 UIPI）时 auto 模式回退消息投递并如实标注。"""
    driver = FakeDriver(sendinput_error=InputError("SendInput 被拒绝"))
    result = _backend(driver).send(1234, build_action("click", {}))
    assert result.detail["delivery"] == "message"
    assert _posts(driver)


def test_explicit_sendinput_blocked_raises():
    driver = FakeDriver(sendinput_error=InputError("SendInput 被拒绝"))
    with pytest.raises(InputError):
        _backend(driver).send(1234, build_action("click", {"method": "sendinput"}))


# ── SendInput：鼠标 ─────────────────────────────────────

def test_click_center_moves_and_clicks():
    driver = FakeDriver()
    result = _backend(driver).send(1234, build_action("click", {}))
    assert _moves(driver) == [("move", 500, 500)]
    assert _mouses(driver) == [
        ("mouse", winapi.MOUSEEVENTF_LEFTDOWN, 0),
        ("mouse", winapi.MOUSEEVENTF_LEFTUP, 0),
    ]
    assert result.detail["x"] == 400 and result.detail["y"] == 300
    assert result.detail["button"] == "left"
    assert result.detail["count"] == 1


def test_right_double_click_sequence():
    driver = FakeDriver()
    _backend(driver).send(1234, build_action(
        "click", {"button": "right", "count": 2, "x": 10, "y": 20}))
    assert _moves(driver) == [("move", 110, 220)]
    assert _mouses(driver) == [
        ("mouse", winapi.MOUSEEVENTF_RIGHTDOWN, 0),
        ("mouse", winapi.MOUSEEVENTF_RIGHTUP, 0),
        ("mouse", winapi.MOUSEEVENTF_RIGHTDOWN, 0),
        ("mouse", winapi.MOUSEEVENTF_RIGHTUP, 0),
    ]
    assert ("sleep", win_module._DOUBLE_CLICK_INTERVAL) in driver.events


def test_middle_click_flags():
    driver = FakeDriver()
    _backend(driver).send(1234, build_action("click", {"button": "middle"}))
    assert _mouses(driver)[0] == ("mouse", winapi.MOUSEEVENTF_MIDDLEDOWN, 0)


def test_click_with_ctrl_modifier_holds_and_releases():
    driver = FakeDriver()
    _backend(driver).send(1234, build_action("click", {"modifiers": ["ctrl"]}))
    keys = _keys(driver)
    assert keys[0] == ("key", winapi.VK_CONTROL, False)
    assert keys[-1] == ("key", winapi.VK_CONTROL, True)
    # 按下修饰键后才是点击
    assert driver.events.index(keys[0]) < driver.events.index(_mouses(driver)[0])


def test_move_action_uses_given_point():
    driver = FakeDriver()
    _backend(driver).send(1234, build_action("move", {"x": 1, "y": 2}))
    assert _moves(driver) == [("move", 101, 202)]


def test_click_out_of_bounds_raises():
    driver = FakeDriver()
    with pytest.raises(ActionError):
        _backend(driver).send(1234, build_action("click", {"x": 800, "y": 5}))


# ── SendInput：拖动 ─────────────────────────────────────

def test_drag_sequence_and_endpoint():
    driver = FakeDriver()
    action = build_action("drag", {
        "from_x": 0, "from_y": 0, "to_x": 100, "to_y": 0,
        "steps": 4, "duration": 0.4,
    })
    result = _backend(driver).send(1234, action)
    moves = _moves(driver)
    assert moves[0] == ("move", 100, 200)
    assert moves[-1] == ("move", 200, 200)
    assert len(moves) >= 5
    mouses = _mouses(driver)
    assert mouses[0] == ("mouse", winapi.MOUSEEVENTF_LEFTDOWN, 0)
    assert mouses[-1] == ("mouse", winapi.MOUSEEVENTF_LEFTUP, 0)
    assert result.detail["to_screen_x"] == 200
    assert result.detail["steps"] == 4


def test_drag_from_center_default():
    driver = FakeDriver()
    _backend(driver).send(1234, build_action("drag", {"to_x": 0, "to_y": 0}))
    assert _moves(driver)[0] == ("move", 500, 500)


# ── SendInput：滚轮 ─────────────────────────────────────

def test_scroll_vertical_and_horizontal():
    driver = FakeDriver()
    _backend(driver).send(1234, build_action("scroll", {"direction": "up", "amount": 2}))
    assert _mouses(driver)[0] == (
        "mouse", winapi.MOUSEEVENTF_WHEEL, 2 * winapi.WHEEL_DELTA)

    driver2 = FakeDriver()
    _backend(driver2).send(1234, build_action("scroll", {"direction": "right"}))
    assert _mouses(driver2)[0] == (
        "mouse", winapi.MOUSEEVENTF_HWHEEL, 3 * winapi.WHEEL_DELTA)


def test_scroll_down_is_negative_delta():
    driver = FakeDriver()
    _backend(driver).send(1234, build_action("scroll", {}))
    assert _mouses(driver)[0][2] == -3 * winapi.WHEEL_DELTA


# ── SendInput：键盘与文本 ────────────────────────────────

def test_key_combo_sequence(monkeypatch):
    monkeypatch.setattr(win_module.winapi, "key_scan_code",
                        lambda char: (0x53, 0))
    driver = FakeDriver()
    _backend(driver).send(1234, build_action("key", {"key": "ctrl+s"}))
    assert _keys(driver) == [
        ("key", winapi.VK_CONTROL, False),
        ("key", 0x53, False),
        ("key", 0x53, True),
        ("key", winapi.VK_CONTROL, True),
    ]


def test_key_character_with_implicit_shift(monkeypatch):
    """大写字符隐含 shift：VkKeyScanW 返回 shift 状态位时自动补按 shift。"""
    monkeypatch.setattr(win_module.winapi, "key_scan_code",
                        lambda char: (0x53, 1))
    driver = FakeDriver()
    _backend(driver).send(1234, build_action("key", {"key": "S"}))
    assert _keys(driver) == [
        ("key", winapi.VK_SHIFT, False),
        ("key", 0x53, False),
        ("key", 0x53, True),
        ("key", winapi.VK_SHIFT, True),
    ]


def test_key_named_key(monkeypatch):
    monkeypatch.setattr(win_module.winapi, "key_scan_code", lambda char: None)
    driver = FakeDriver()
    _backend(driver).send(1234, build_action("key", {"key": "alt+f4"}))
    assert _keys(driver) == [
        ("key", winapi.VK_MENU, False),
        ("key", 0x73, False),
        ("key", 0x73, True),
        ("key", winapi.VK_MENU, True),
    ]


def test_key_unmappable_character_raises(monkeypatch):
    monkeypatch.setattr(win_module.winapi, "key_scan_code", lambda char: None)
    driver = FakeDriver()
    with pytest.raises(InputError) as excinfo:
        _backend(driver).send(1234, build_action("key", {"key": "\u00b5"}))
    assert "op=type" in str(excinfo.value)


def test_type_sends_unicode_and_enter():
    driver = FakeDriver()
    result = _backend(driver).send(1234, build_action("type", {"text": "a你\n"}))
    unicode_events = [event for event in driver.events if event[0] == "unicode"]
    assert unicode_events == [
        ("unicode", ord("a"), False), ("unicode", ord("a"), True),
        ("unicode", ord("你"), False), ("unicode", ord("你"), True),
    ]
    assert ("key", winapi.VK_RETURN, False) in _keys(driver)
    assert ("key", winapi.VK_RETURN, True) in _keys(driver)
    assert result.detail["characters"] == 3


def test_type_handles_supplementary_character():
    driver = FakeDriver()
    _backend(driver).send(1234, build_action("type", {"text": "😀"}))
    units = [event[1] for event in driver.events if event[0] == "unicode"]
    assert len(units) == 4  # 代理对：down/up × 2 个码元


# ── PostMessage 路径 ────────────────────────────────────

def test_message_click_uses_client_coordinates():
    driver = FakeDriver(foreground=False, activate_grants_foreground=False,
                        client_origin=(10, 20))
    _backend(driver).send(1234, build_action(
        "click", {"x": 5, "y": 5, "method": "message"}))
    posts = _posts(driver)
    # 屏幕坐标 (105, 205) → 客户区 (95, 185)
    expected_lparam = ((185 & 0xFFFF) << 16) | (95 & 0xFFFF)
    moves = [p for p in posts if p[2] == winapi.WM_MOUSEMOVE]
    downs = [p for p in posts if p[2] == winapi.WM_LBUTTONDOWN]
    ups = [p for p in posts if p[2] == winapi.WM_LBUTTONUP]
    assert moves and moves[0][4] == expected_lparam
    assert downs and downs[0][4] == expected_lparam
    assert ups and ups[0][4] == expected_lparam
    assert downs[0][3] == winapi.MK_LBUTTON


def test_message_double_click_uses_dblclk():
    driver = FakeDriver(foreground=False, activate_grants_foreground=False)
    _backend(driver).send(1234, build_action(
        "click", {"count": 2, "method": "message"}))
    messages = [p[2] for p in _posts(driver)]
    assert winapi.WM_LBUTTONDBLCLK in messages
    assert messages.count(winapi.WM_LBUTTONDOWN) == 1


def test_message_right_click_messages():
    driver = FakeDriver(foreground=False, activate_grants_foreground=False)
    _backend(driver).send(1234, build_action(
        "click", {"button": "right", "method": "message"}))
    messages = [p[2] for p in _posts(driver)]
    assert winapi.WM_RBUTTONDOWN in messages
    assert winapi.WM_RBUTTONUP in messages


def test_message_drag_sequence():
    driver = FakeDriver(foreground=False, activate_grants_foreground=False)
    _backend(driver).send(1234, build_action("drag", {
        "from_x": 0, "from_y": 0, "to_x": 10, "to_y": 0,
        "steps": 2, "duration": 0, "method": "message",
    }))
    messages = [p[2] for p in _posts(driver)]
    assert messages[0] == winapi.WM_MOUSEMOVE
    assert winapi.WM_LBUTTONDOWN in messages
    assert messages[-1] == winapi.WM_LBUTTONUP
    assert messages.count(winapi.WM_MOUSEMOVE) >= 3


def test_message_scroll_uses_screen_coordinates_and_delta():
    driver = FakeDriver(foreground=False, activate_grants_foreground=False,
                        client_origin=(10, 20))
    _backend(driver).send(1234, build_action(
        "scroll", {"direction": "up", "amount": 2, "x": 5, "y": 5,
                   "method": "message"}))
    wheel = [p for p in _posts(driver) if p[2] == winapi.WM_MOUSEWHEEL]
    assert wheel
    delta = (wheel[0][3] >> 16) & 0xFFFF
    assert delta == 2 * winapi.WHEEL_DELTA
    # lParam 为屏幕坐标（不是客户区坐标）
    lparam = wheel[0][4]
    assert (lparam & 0xFFFF) == 105
    assert ((lparam >> 16) & 0xFFFF) == 205


def test_message_key_sequence_includes_char():
    driver = FakeDriver(foreground=False, activate_grants_foreground=False)
    _backend(driver).send(1234, build_action(
        "key", {"key": "a", "method": "message"}))
    posts = _posts(driver)
    messages = [p[2] for p in posts]
    assert messages == [winapi.WM_KEYDOWN, winapi.WM_CHAR, winapi.WM_KEYUP]
    assert posts[1][3] == ord("a")


def test_message_type_sends_wm_char():
    driver = FakeDriver(foreground=False, activate_grants_foreground=False)
    _backend(driver).send(1234, build_action(
        "type", {"text": "hi\n", "method": "message"}))
    posts = _posts(driver)
    chars = [p[3] for p in posts if p[2] == winapi.WM_CHAR]
    assert chars == [ord("h"), ord("i"), 0x0D]
    assert winapi.WM_KEYDOWN in [p[2] for p in posts]


def test_message_without_client_origin_raises():
    driver = FakeDriver(foreground=False, activate_grants_foreground=False,
                        client_origin=None)
    with pytest.raises(InputError):
        _backend(driver).send(1234, build_action(
            "click", {"method": "message"}))


def test_message_click_targets_child_control():
    """命中点下的子控件（如输入框）时，鼠标消息投递给该控件。"""
    driver = FakeDriver(foreground=False, activate_grants_foreground=False,
                        child_handle=5555)
    _backend(driver).send(1234, build_action(
        "click", {"x": 5, "y": 5, "method": "message"}))
    posts = _posts(driver)
    assert posts
    assert all(post[1] == 5555 for post in posts)
    assert ("child_at", 777, 105, 205) in driver.events


def test_message_key_reuses_last_clicked_control():
    """先点击输入框，随后的 type 复用该控件（否则控件收不到 WM_CHAR）。"""
    driver = FakeDriver(foreground=False, activate_grants_foreground=False,
                        child_handle=5555)
    backend = _backend(driver)
    backend.send(1234, build_action("click", {"x": 5, "y": 5, "method": "message"}))
    backend.send(1234, build_action("type", {"text": "ab", "method": "message"}))
    type_posts = [p for p in _posts(driver) if p[2] == winapi.WM_CHAR]
    assert type_posts and all(post[1] == 5555 for post in type_posts)


def test_message_key_falls_back_to_top_window():
    """未发生鼠标交互时，键盘消息投递给顶层窗口。"""
    driver = FakeDriver(foreground=False, activate_grants_foreground=False,
                        child_handle=5555)
    _backend(driver).send(1234, build_action(
        "type", {"text": "a", "method": "message"}))
    assert _posts(driver)[0][1] == 777


# ── 键码解析 ────────────────────────────────────────────

def test_resolve_windows_vk_named_and_character(monkeypatch):
    monkeypatch.setattr(win_module.winapi, "key_scan_code", lambda char: (0x41, 0))
    assert resolve_windows_vk("enter") == (winapi.VK_RETURN, set())
    assert resolve_windows_vk("a") == (0x41, set())


def test_resolve_windows_vk_unknown_named_key():
    with pytest.raises(InputError):
        resolve_windows_vk("page_sideways")
