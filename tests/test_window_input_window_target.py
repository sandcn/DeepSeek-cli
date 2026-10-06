"""窗口选择器在输入注入链路上的测试（Windows / X11 / macOS 后端）。

覆盖：动作构建携带 ``window`` 选择器（六种 op）、非法选择器在构建阶段报错、
动作摘要输出、后端按 ``action.window`` 选择目标窗口（含定位失败时转成参数
错误）、键盘注入带扫描码（浏览器 / 游戏可拿到物理键位）。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot.windows import SelectorError, WindowInfo
from src.tools._window_input import (
    build_action,
    describe_action,
    send_window_input,
)
from src.tools._window_input import win as win_module
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.result import ActionError, InputError, InputResult
from src.tools._window_input.win import WindowsInputBackend, Win32Driver, locate_window

from tests.test_window_input_win import FakeDriver


@pytest.fixture(autouse=True)
def _no_real_dpi_call(monkeypatch):
    """屏蔽真实 DPI 调用（Cygwin 下避免触碰系统 API）。"""
    monkeypatch.setattr(win_module.winapi, "ensure_process_dpi_aware", lambda: True)


# ── 动作构建 ─────────────────────────────────────────────

@pytest.mark.parametrize("op,params", [
    ("click", {"x": 1, "y": 2}),
    ("move", {"x": 1, "y": 2}),
    ("drag", {"to_x": 3, "to_y": 4}),
    ("scroll", {"amount": 2}),
    ("key", {"key": "enter"}),
    ("type", {"text": "hi"}),
])
def test_actions_carry_window_selector(op, params):
    action = build_action(op, {**params, "window": "#2"})
    assert action.window == "#2"
    describe = describe_action(action)
    assert describe["window"] == "#2"


def test_actions_default_to_empty_window_selector():
    action = build_action("click", {})
    assert action.window == ""
    assert "window" not in describe_action(action)


def test_build_rejects_invalid_window_selector():
    with pytest.raises(ActionError) as excinfo:
        build_action("click", {"window": "#0"})
    assert "窗口选择器非法" in str(excinfo.value)


def test_build_accepts_keyword_and_handle_selectors():
    assert build_action("click", {"window": "popup"}).window == "popup"
    assert build_action("click", {"window": "handle:0x1a2b"}).window == "handle:0x1a2b"
    assert build_action("click", {"window": "   "}).window == ""


# ── Windows 后端：按选择器定位 ───────────────────────────

def _info(handle, title, order=0):
    return WindowInfo(handle=handle, pid=99, title=title,
                      class_name="Chrome_WidgetWin_1", width=800, height=600,
                      order=order)


def test_windows_locate_window_uses_selector(monkeypatch):
    infos = [_info(0x1001, "主窗口", 0), _info(0x1002, "", 1)]
    monkeypatch.setattr(win_module, "enumerate_window_infos", lambda pids: infos)
    monkeypatch.setattr(win_module, "resolve_window_pids", lambda pid: {99})
    monkeypatch.setattr(win_module, "frame_of",
                        lambda candidate: WindowFrame(0, 0, candidate.width, candidate.height))

    target = locate_window(1234, "title:主窗口")
    assert target is not None and target.handle == 0x1001
    popup = locate_window(1234, "popup")
    assert popup is not None and popup.handle == 0x1002
    with pytest.raises(SelectorError):
        locate_window(1234, "title:不存在")


def test_windows_backend_send_uses_action_window(monkeypatch):
    seen = {}

    def _locate(pid, window=None):
        seen["pid"] = pid
        seen["window"] = window
        return win_module._TargetWindow(handle=0x20, pid=99, title="弹层",
                                        frame=WindowFrame(0, 0, 200, 100))

    driver = FakeDriver(foreground=True)
    backend = WindowsInputBackend(locator=_locate, driver=driver)
    result = backend.send(1234, build_action("click", {"window": "popup", "x": 5, "y": 5}))
    assert seen["window"] == "popup"
    assert result.detail["x"] == 5
    assert result.detail["delivery"] == "sendinput"


def test_windows_backend_send_without_selector_calls_single_arg_locator():
    """未指定选择器时保持旧调用形态（单参数定位器，兼容既有替身）。"""
    seen = {}

    def _locate(pid):
        seen["pid"] = pid
        return win_module._TargetWindow(handle=0x20, pid=99, title="主窗口",
                                        frame=WindowFrame(0, 0, 200, 100))

    backend = WindowsInputBackend(locator=_locate, driver=FakeDriver(foreground=True))
    backend.send(4321, build_action("click", {}))
    assert seen["pid"] == 4321


# ── 选择器错误转成动作错误 ───────────────────────────────

def test_send_window_input_converts_selector_error(monkeypatch):
    class _Backend:
        name = "fake"

        def supports(self):
            return True

        def send(self, pid, action):
            raise SelectorError("窗口选择器 'title:x' 没有匹配的窗口")

    monkeypatch.setattr("src.tools._window_input.resolve_backend", lambda: _Backend())
    with pytest.raises(ActionError) as excinfo:
        send_window_input(1234, build_action("click", {"window": "title:x"}))
    assert "没有匹配的窗口" in str(excinfo.value)


def test_send_window_input_keeps_input_error(monkeypatch):
    class _Backend:
        name = "fake"

        def supports(self):
            return True

        def send(self, pid, action):
            raise InputError("窗口输入需要 xdotool（未安装）")

    monkeypatch.setattr("src.tools._window_input.resolve_backend", lambda: _Backend())
    with pytest.raises(InputError):
        send_window_input(1234, build_action("click", {}))


# ── 键盘注入带扫描码 ─────────────────────────────────────

def test_key_event_includes_scan_code(monkeypatch):
    captured = []
    monkeypatch.setattr(win_module.winapi, "map_virtual_key", lambda vk, *a: 0x1E)
    monkeypatch.setattr(win_module.winapi, "send_inputs",
                        lambda items: captured.append(items) or len(items))

    Win32Driver().key_event(0x41, key_up=False)
    assert captured
    item = captured[0][0]
    assert item.ki.wVk == 0x41
    assert item.ki.wScan == 0x1E
    assert item.ki.dwFlags == 0


def test_key_event_marks_extended_keys_from_scan_code(monkeypatch):
    captured = []
    # 扩展键：MapVirtualKey 返回带 E0 前缀的扫描码
    monkeypatch.setattr(win_module.winapi, "map_virtual_key", lambda vk, *a: 0xE04B)
    monkeypatch.setattr(win_module.winapi, "send_inputs",
                        lambda items: captured.append(items) or len(items))

    Win32Driver().key_event(win_module.winapi.VK_LEFT, key_up=False)
    item = captured[0][0]
    assert item.ki.wScan == 0x4B
    assert item.ki.dwFlags & win_module.winapi.KEYEVENTF_EXTENDEDKEY


def test_key_event_keyup_flag_and_fallback_extended_set(monkeypatch):
    captured = []
    monkeypatch.setattr(win_module.winapi, "map_virtual_key", lambda vk, *a: 0)
    monkeypatch.setattr(win_module.winapi, "send_inputs",
                        lambda items: captured.append(items) or len(items))

    Win32Driver().key_event(win_module.winapi.VK_DELETE, key_up=True)
    item = captured[0][0]
    assert item.ki.dwFlags & win_module.winapi.KEYEVENTF_KEYUP
    assert item.ki.dwFlags & win_module.winapi.KEYEVENTF_EXTENDEDKEY
    assert item.ki.wScan == 0


# ── 结果对象 ─────────────────────────────────────────────

def test_input_result_carries_window_title():
    result = InputResult(action="click", backend="windows", window_pid=1,
                         window_title="弹层", detail={"x": 1})
    payload = result.to_dict()
    assert payload["window_title"] == "弹层"
    assert payload["x"] == 1
