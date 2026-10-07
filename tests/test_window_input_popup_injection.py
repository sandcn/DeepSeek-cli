"""弹层窗口（tool window / 下拉浮层 / 右键菜单）的输入投递决策测试。

背景（真实环境踩到的坑）：Chrome 的原生 ``<select>`` 下拉与右键浮层是
``WS_EX_TOOLWINDOW`` 窗口，**不参与前台切换**。旧实现对此一律回退
PostMessage，而这类窗口没有可换算的客户区，于是直接失败并报出晦涩的
「无法读取目标窗口客户区原点，PostMessage 投递无法换算坐标」。

现在按「该窗口**所属应用**是否在前台」判定通道，并对光标可达的弹层直接用
合成输入投递。覆盖：

  - 属主窗口在前台 → SendInput，并回报 foreground_window；
  - 同进程树内其它窗口在前台 → SendInput；
  - 整个应用都不在前台、但光标位置命中目标窗口（含其子窗口）→ SendInput；
  - 光标被其它窗口占用 → 回退 PostMessage（不误投递）；
  - 弹层没有可换算客户区时的错误文案可操作（建议去掉 method='message'）；
  - 工具窗口的激活转向属主；显式 sendinput 且无前台时仍如实报错；
  - ``_TargetWindow`` 新增字段的默认值与定位填充。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot import winapi
from src.tools._screenshot.windows import WindowInfo
from src.tools._window_input import win as win_module
from src.tools._window_input.action import build_action
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.result import InputError
from src.tools._window_input.win import WindowsInputBackend

from tests.test_window_input_win import FakeDriver

POPUP = 0x20
OWNER = 0x10
SIBLING = 0x30


@pytest.fixture(autouse=True)
def _no_real_dpi_call(monkeypatch):
    """屏蔽真实 DPI 调用（Cygwin 下避免触碰系统 API）。"""
    monkeypatch.setattr(win_module.winapi, "ensure_process_dpi_aware",
                        lambda: True)


class PopupDriver(FakeDriver):
    """弹层场景假驱动：可指定「应用前台窗口」「光标命中窗口」。"""

    def __init__(self, *, foreground_handle=0, topmost=None,
                 client_origin=(0, 0)):
        super().__init__(foreground=False, client_origin=client_origin)
        self._foreground_handle = foreground_handle
        self._topmost = topmost
        self.activations: list[int] = []

    def is_foreground(self, handle):
        target = winapi.hwnd_value(handle)
        return bool(self._foreground_handle) and target == self._foreground_handle

    def activate(self, handle):
        self.activations.append(winapi.hwnd_value(handle))
        return True

    def foreground_in_tree(self, pid):
        return self._foreground_handle

    def topmost_at(self, screen_x, screen_y):
        return self._topmost


def _popup_target(handle=POPUP, owner=OWNER, tool=True):
    return win_module._TargetWindow(
        handle=handle, pid=99, title="", frame=WindowFrame(100, 200, 300, 200),
        owner_handle=owner, tool_window=tool,
    )


def _backend(driver, target=None):
    target = target or _popup_target()
    return WindowsInputBackend(locator=lambda pid, window=None: target,
                               driver=driver)


def _click(**params):
    return build_action("click", {"x": 5, "y": 5, **params})


# ── 应用前台判定 ─────────────────────────────────────────

def test_popup_uses_sendinput_when_owner_is_foreground():
    driver = PopupDriver(foreground_handle=OWNER)
    result = _backend(driver).send(1234, _click())
    assert result.detail["delivery"] == "sendinput"
    assert result.detail["foreground_window"] == f"0x{OWNER:X}"
    assert [event[0] for event in driver.events].count("mouse") == 2
    # 应用已在前台：不需要任何激活
    assert driver.activations == []


def test_popup_uses_sendinput_when_sibling_window_is_foreground():
    """属主不在前台，但同进程树里的另一个窗口在前台（多进程 GUI 常见）。"""
    driver = PopupDriver(foreground_handle=SIBLING)
    result = _backend(driver).send(1234, _click())
    assert result.detail["delivery"] == "sendinput"
    assert result.detail["foreground_window"] == f"0x{SIBLING:X}"


def test_tool_window_activation_targets_owner_first():
    """弹层激活时先激活属主（弹层自身不参与前台切换）。"""
    driver = PopupDriver(foreground_handle=0)
    _backend(driver)._ensure_foreground(_popup_target())
    assert driver.activations == [OWNER, POPUP] * win_module._FOREGROUND_ATTEMPTS


# ── 光标可达（整应用不在前台） ───────────────────────────

def test_popup_sendinput_when_cursor_reaches_target(monkeypatch):
    monkeypatch.setattr(win_module.winapi, "window_root",
                        lambda hwnd: winapi.hwnd_value(hwnd))
    driver = PopupDriver(foreground_handle=0, topmost=POPUP)
    result = _backend(driver).send(1234, _click())
    assert result.detail["delivery"] == "sendinput"
    assert [event[0] for event in driver.events].count("mouse") == 2


def test_popup_sendinput_when_cursor_hits_child_window(monkeypatch):
    """光标命中的是目标窗口的子窗口（window_root 归一到目标）也算可达。"""
    monkeypatch.setattr(win_module.winapi, "window_root",
                        lambda hwnd: POPUP if winapi.hwnd_value(hwnd) == 0x21
                        else winapi.hwnd_value(hwnd))
    driver = PopupDriver(foreground_handle=0, topmost=0x21)
    result = _backend(driver).send(1234, _click())
    assert result.detail["delivery"] == "sendinput"


def test_popup_falls_back_to_message_when_cursor_covered(monkeypatch):
    """光标位置最上层是别的窗口：不能投递合成鼠标事件（会点错窗口）。"""
    monkeypatch.setattr(win_module.winapi, "window_root",
                        lambda hwnd: winapi.hwnd_value(hwnd))
    driver = PopupDriver(foreground_handle=0, topmost=0x99)
    result = _backend(driver).send(1234, _click())
    assert result.detail["delivery"] == "message"
    assert [event[0] for event in driver.events].count("post") > 0


def test_popup_falls_back_to_message_without_pointer_probe():
    """驱动不提供光标探测能力时保持旧行为（回退消息投递）。"""
    driver = FakeDriver(foreground=False, activate_grants_foreground=False)
    result = _backend(driver).send(1234, _click())
    assert result.detail["delivery"] == "message"


# ── 错误文案 ─────────────────────────────────────────────

def test_message_delivery_without_client_area_reports_actionable_error():
    """消息投递 + 无可换算客户区 → 提示改用合成输入，而不是晦涩的换算失败。"""
    driver = PopupDriver(foreground_handle=0, client_origin=None)
    with pytest.raises(InputError) as excinfo:
        _backend(driver).send(1234, _click(method="message"))
    message = str(excinfo.value)
    assert "客户区" in message
    assert "method='message'" in message
    assert "SendInput" in message


def test_explicit_sendinput_on_popup_without_foreground_raises():
    driver = PopupDriver(foreground_handle=0, topmost=None)
    with pytest.raises(InputError) as excinfo:
        _backend(driver).send(1234, _click(method="sendinput"))
    assert "置于前台" in str(excinfo.value)


# ── 键盘 / 文本 ──────────────────────────────────────────

def test_keyboard_on_popup_reports_receiving_window():
    """弹层键盘注入：按键由同应用的前台窗口接收，结果里如实标注。"""
    driver = PopupDriver(foreground_handle=OWNER)
    result = _backend(driver).send(1234, build_action("key", {"key": "escape"}))
    assert result.detail["delivery"] == "sendinput"
    assert result.detail["keyboard_window"] == f"0x{OWNER:X}"
    assert "keyboard_via" in result.detail
    assert [event[0] for event in driver.events].count("key") == 2


def test_keyboard_on_own_foreground_window_not_annotated():
    """目标窗口自己就是前台时不产生多余标注。"""
    driver = PopupDriver(foreground_handle=POPUP)
    result = _backend(driver).send(1234, build_action("key", {"key": "enter"}))
    assert result.detail["delivery"] == "sendinput"
    assert "keyboard_window" not in result.detail


def test_type_on_popup_reports_receiving_window():
    driver = PopupDriver(foreground_handle=OWNER)
    result = _backend(driver).send(1234, build_action("type", {"text": "ab"}))
    assert result.detail["delivery"] == "sendinput"
    assert result.detail["keyboard_window"] == f"0x{OWNER:X}"


# ── 定位填充 ─────────────────────────────────────────────

def test_target_window_defaults_keep_previous_shape():
    target = win_module._TargetWindow(handle=1, pid=2, title="t",
                                      frame=WindowFrame(0, 0, 10, 10))
    assert target.owner_handle == 0
    assert target.tool_window is False


def test_locate_window_fills_owner_and_tool_flag(monkeypatch):
    info = WindowInfo(handle=0x50, pid=99, title="", class_name="Chrome_WidgetWin_1",
                      width=100, height=80, tool_window=True)
    monkeypatch.setattr(win_module, "enumerate_window_infos", lambda pids: [info])
    monkeypatch.setattr(win_module, "resolve_window_pids", lambda pid: {99})
    monkeypatch.setattr(win_module, "frame_of",
                        lambda candidate: WindowFrame(0, 0, candidate.width,
                                                      candidate.height))
    monkeypatch.setattr(win_module.winapi, "window_owner", lambda hwnd: 0x40)

    target = win_module.locate_window(1234, "popup")
    assert target is not None
    assert target.handle == 0x50
    assert target.owner_handle == 0x40
    assert target.tool_window is True
