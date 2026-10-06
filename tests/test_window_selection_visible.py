"""不可见 / 最小化窗口的选择与展示（回归：弹层消失后截到全黑辅助窗口）。

缺陷背景（实机复现）：Chrome 的 ``Chrome_WidgetWin_0`` 辅助窗口
``IsWindowVisible`` 为假，却与真实弹出的右键菜单一样「无标题且非主窗口」，
因此 ``window='popup'`` 的图像 / 输入会命中它——截图是纯黑，输入也打不进去；
同理 ``#N`` 把隐藏窗口计入 Z 序，导致 ``#N`` 与 ``op=windows`` 清单编号错位。

修复：选择器优先（``popup`` / ``dialog`` 仅）在**可操作窗口**（可见且未最小化）
中匹配；``#N`` 只在可操作窗口中排序并与清单的 ``z_index`` 一致；``main`` 排序
把可见性放在首位；截图结果回填 ``window_selector`` / ``window_summary``；
「无法置前」的错误消息带出当前前台窗口。

覆盖：纯选择逻辑、清单展示、Windows 截图结果回填、置前失败提示、bash_opt
输入结果的 window 回传与选择器无匹配时的可读提示。
"""

from __future__ import annotations

import json

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot import png as png_mod
from src.tools._screenshot import win as win_mod
from src.tools._screenshot import winapi
from src.tools._screenshot.result import CaptureResult
from src.tools._screenshot.windows import (
    NO_SELECTABLE_INDEX,
    SelectorError,
    WindowInfo,
    describe_windows,
    filter_windows,
    is_selectable,
    main_window,
    mark_main,
    parse_selector,
    pick_window,
    selectable_index,
    selectable_windows,
    window_hint,
)
from src.tools._window_input import build_action
from src.tools._window_input import win as input_win_module
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.result import ActionError, InputError
from src.tools._window_input.win import WindowsInputBackend
from src.tools.bash_opt import BashOptFunc

from tests.test_window_input_win import FakeDriver


def _win(handle, *, pid=10, title="", class_name="Chrome_WidgetWin_1",
         width=800, height=600, left=0, top=0, tool=False, minimized=False,
         visible=True, foreground=False, order=0, main=False):
    return WindowInfo(
        handle=handle, pid=pid, title=title, class_name=class_name,
        width=width, height=height, left=left, top=top,
        tool_window=tool, minimized=minimized, visible=visible,
        foreground=foreground, order=order, main=main,
    )


@pytest.fixture
def chrome_like_windows():
    """Chrome 进程树的典型窗口集合：主窗口 + 真实弹层 + 隐藏辅助窗口。"""
    return [
        _win(0x1001, title="设置 - Google Chrome", width=1280, height=900,
             order=0, foreground=True),
        _win(0x1002, title="", width=1920, height=997, order=1, visible=False),
        _win(0x1003, title="", width=320, height=240, order=2, tool=True),
    ]


# ── 可操作窗口判定与编号 ─────────────────────────────────

def test_is_selectable_requires_visible_and_restored():
    assert is_selectable(_win(1))
    assert not is_selectable(_win(2, visible=False))
    assert not is_selectable(_win(3, minimized=True))


def test_selectable_windows_drops_hidden_and_minimized(chrome_like_windows):
    handles = [item.handle for item in selectable_windows(chrome_like_windows)]
    assert handles == [0x1001, 0x1003]


def test_selectable_index_numbers_only_operable_windows(chrome_like_windows):
    assert selectable_index(chrome_like_windows) == {0x1001: 1, 0x1003: 2}
    assert NO_SELECTABLE_INDEX == 0


# ── 主窗口排序：可见性优先 ───────────────────────────────

def test_main_window_prefers_visible_over_larger_hidden():
    hidden = _win(1, title="隐藏的大窗口", width=2000, height=1200, visible=False)
    visible = _win(2, title="可见的小窗口", width=400, height=300)
    assert main_window([hidden, visible]).handle == 2


def test_main_window_still_works_when_all_hidden():
    """全部窗口都不可见时仍要选出主窗口（不抛异常，退化为旧排序）。"""
    assert main_window([_win(1, title="a", visible=False),
                        _win(2, title="b", visible=False)]).handle == 1


# ── #N：只在可操作窗口中排序 ─────────────────────────────

def test_index_selector_skips_hidden_windows(chrome_like_windows):
    assert pick_window(chrome_like_windows, "#1").handle == 0x1001
    assert pick_window(chrome_like_windows, "#2").handle == 0x1003


def test_index_selector_out_of_operable_range_reports(chrome_like_windows):
    with pytest.raises(SelectorError) as excinfo:
        pick_window(chrome_like_windows, "#3")
    message = str(excinfo.value)
    assert "没有匹配的窗口" in message
    # 清单里隐藏窗口显示 #-（不会被 #N 选中）
    assert "#-" in message


def test_minimized_window_not_counted_in_index():
    minimized = _win(1, title="最小化", order=0, minimized=True)
    visible = _win(2, title="可见", order=1)
    assert pick_window([minimized, visible], "#1").handle == 2
    assert filter_windows([minimized], parse_selector("#1")) == []


# ── popup / dialog：不再命中隐藏辅助窗口 ─────────────────

def test_popup_ignores_hidden_helper_window(chrome_like_windows):
    """隐藏的 Chrome_WidgetWin_0（无标题、非主窗口）不应被当成弹层。"""
    marked = mark_main(chrome_like_windows)
    assert pick_window(marked, "popup").handle == 0x1003


def test_popup_reports_when_only_hidden_window_exists():
    windows = mark_main([
        _win(0x1001, title="设置 - Google Chrome", width=1280, height=900),
        _win(0x2002, title="", width=1920, height=997, order=1, visible=False),
    ])
    with pytest.raises(SelectorError) as excinfo:
        pick_window(windows, "popup")
    assert "0x1001" in str(excinfo.value)


def test_popup_reports_when_menu_already_closed(chrome_like_windows):
    """菜单关闭后的快照（只剩主窗口 + 隐藏辅助窗口）必须报错而不是选中隐藏窗口。"""
    closed = mark_main([item for item in chrome_like_windows
                        if item.handle != 0x1003])
    with pytest.raises(SelectorError):
        pick_window(closed, "popup")


def test_dialog_ignores_hidden_dialog_class_window():
    hidden_dialog = _win(1, title="隐藏对话框", class_name="#32770",
                         visible=False, order=0)
    visible_dialog = _win(2, title="参数设置", class_name="#32770", order=1)
    assert pick_window([hidden_dialog, visible_dialog], "dialog").handle == 2
    with pytest.raises(SelectorError):
        pick_window([hidden_dialog], "dialog")


# ── title / class：优先可操作窗口，必要时回退 ────────────

def test_title_prefers_operable_window():
    hidden = _win(1, title="辅助面板", visible=False, order=0)
    visible = _win(2, title="辅助面板", order=1)
    assert pick_window([hidden, visible], "title:辅助面板").handle == 2


def test_title_falls_back_to_hidden_window():
    """显式按标题查找隐藏窗口是合理需求（诊断场景），保留回退能力。"""
    hidden = _win(1, title="辅助面板", visible=False)
    assert pick_window([_win(2, title="其它"), hidden],
                       "title:辅助面板").handle == 1


def test_class_prefers_operable_window():
    hidden = _win(1, class_name="Chrome_WidgetWin_0", visible=False, order=0)
    visible = _win(2, class_name="Chrome_WidgetWin_0", order=1)
    assert pick_window([hidden, visible],
                       "class:Chrome_WidgetWin_0").handle == 2


def test_handle_selector_can_target_hidden_window():
    """显式句柄指定不受可见性限制（模型明确知道自己要哪个窗口）。"""
    hidden = _win(0x2222, visible=False)
    assert pick_window([_win(0x1111), hidden], "handle:0x2222").handle == 0x2222


# ── 清单展示：z_index 与 #- ──────────────────────────────

def test_describe_windows_exposes_z_index_and_selectable(chrome_like_windows):
    items = describe_windows(chrome_like_windows)
    assert [item["z_index"] for item in items] == [1, None, 2]
    assert [item["selectable"] for item in items] == [True, False, True]
    assert items[0]["summary"].startswith("#1")
    assert items[1]["summary"].startswith("#-")


def test_window_hint_marks_hidden_windows_without_index(chrome_like_windows):
    hint = window_hint(chrome_like_windows)
    assert "#1" in hint and "#2" in hint
    assert "#-" in hint
    assert "hidden" in hint


# ── 截图结果回填 selector / summary ──────────────────────

def test_capture_result_serializes_window_selector_and_summary():
    result = CaptureResult(
        path="a.png", width=10, height=10, window_pid=1, window_title="",
        backend="windows", window_selector="popup",
        window_summary="#2 0x222 「无标题」 320x240",
    )
    payload = result.to_dict()
    assert payload["window_selector"] == "popup"
    assert payload["window_summary"].startswith("#2 0x222")


def _install_capture(monkeypatch, candidate, *, bounds=(0, 0, 100, 100)):
    """把 Windows 截图的系统调用替换为桩（不触碰真实系统）。"""
    monkeypatch.setattr(winapi, "ensure_process_dpi_aware", lambda: True)
    monkeypatch.setattr(win_mod, "resolve_window_pids", lambda pid: {pid})
    monkeypatch.setattr(win_mod, "enumerate_candidates", lambda pids: [candidate])
    monkeypatch.setattr(
        win_mod, "capture_window_pixels",
        lambda c: (b"\x00" * (c.width * c.height * 4), c.width, c.height),
    )
    monkeypatch.setattr(winapi, "extended_frame_bounds", lambda hwnd: bounds)


def test_windows_capture_reports_selector_and_summary(monkeypatch, tmp_path):
    candidate = _win(0x2222, title="", width=100, height=100, tool=True)
    _install_capture(monkeypatch, candidate)
    backend = win_mod.WindowsBackend()

    result = backend.capture(42, str(tmp_path / "popup.png"), None, "popup")
    assert result.window_selector == "popup"
    assert "0x2222" in result.window_summary

    fallback = backend.capture(42, str(tmp_path / "main.png"))
    assert fallback.window_selector == "main"
    assert png_mod.read_png_size(str(tmp_path / "main.png")) == (100, 100)


def test_windows_capture_invisible_selector_reports(monkeypatch, tmp_path):
    """选择器无匹配时报 SelectorError（不再静默截隐藏窗口的黑图）。"""
    candidate = _win(0x1111, title="设置", width=100, height=100)
    _install_capture(monkeypatch, candidate)
    with pytest.raises(SelectorError):
        win_mod.WindowsBackend().capture(42, str(tmp_path / "x.png"), None, "popup")


# ── 置前失败：错误消息带出当前前台窗口 ───────────────────

def _locator(pid, window=None):
    return input_win_module._TargetWindow(
        handle=0x20, pid=99, title="复杂操作测试台 v2 - Google Chrome",
        frame=WindowFrame(0, 0, 200, 100),
    )


def _blocked_driver():
    """前台被别的程序占用：激活不授予前台。"""
    return FakeDriver(foreground=False, activate_grants_foreground=False)


@pytest.fixture(autouse=True)
def _stub_foreground_description(monkeypatch):
    monkeypatch.setattr(
        input_win_module.winapi, "foreground_description",
        lambda: "Minecraft（class=LWJGL, handle=0xD10C40）",
    )


def test_sendinput_foreground_failure_names_current_foreground():
    backend = WindowsInputBackend(locator=_locator, driver=_blocked_driver())
    with pytest.raises(InputError) as excinfo:
        backend.send(1234, build_action(
            "click", {"x": 5, "y": 5, "method": "sendinput"}))
    message = str(excinfo.value)
    assert "无法把窗口" in message and "置于前台" in message
    assert "当前前台窗口" in message and "Minecraft" in message
    assert "method='message'" in message


def test_keyboard_foreground_failure_names_current_foreground():
    backend = WindowsInputBackend(locator=_locator, driver=_blocked_driver())
    with pytest.raises(InputError) as excinfo:
        backend.send(1234, build_action(
            "key", {"key": "enter", "method": "sendinput"}))
    message = str(excinfo.value)
    assert "当前前台窗口" in message and "Minecraft" in message


def test_auto_mode_still_falls_back_to_message_when_foreground_blocked():
    """auto 模式前台拿不到时仍回退消息投递（不因新增提示而改变行为）。"""
    backend = WindowsInputBackend(locator=_locator, driver=_blocked_driver())
    result = backend.send(1234, build_action("click", {"x": 5, "y": 5}))
    assert result.detail["delivery"] == "message"


# ── bash_opt：结果回传选择器、可读错误 ───────────────────

class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321):
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


def _fake_send(monkeypatch, *, error=None):
    from src.tools._window_input.result import InputResult

    def _impl(pid, action):
        if error is not None:
            raise error
        return InputResult(action=action.name, backend="windows", window_pid=pid,
                           window_title="", detail={"delivery": "message"})
    monkeypatch.setattr(bash_opt_module, "send_window_input", _impl)


async def test_input_op_echoes_window_selector(monkeypatch):
    _fake_send(monkeypatch)
    func = BashOptFunc(task_id="bg-1", op="click", x=1, y=2, window="popup")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["window"] == "popup"
    assert "z_index" in payload["hint"]


async def test_input_op_without_selector_omits_window_field(monkeypatch):
    _fake_send(monkeypatch)
    func = BashOptFunc(task_id="bg-1", op="click", x=1, y=2)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert "window" not in payload


async def test_input_op_hints_when_selector_matches_nothing(monkeypatch):
    _fake_send(monkeypatch, error=ActionError(
        "窗口选择器 'popup' 没有匹配的窗口。当前可用窗口: #1 0x1001 「设置」"))
    func = BashOptFunc(task_id="bg-1", op="click", x=1, y=2, window="popup")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert result.startswith("(输入失败")
    assert "可能已关闭" in result
    assert "op=windows" in result


async def test_input_op_keeps_plain_error_without_selector_hint(monkeypatch):
    _fake_send(monkeypatch, error=InputError("SendInput 未能投递任何事件"))
    func = BashOptFunc(task_id="bg-1", op="click", x=1, y=2)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "可能已关闭" not in result
    assert "SendInput 未能投递任何事件" in result


# ── 注入结果回传实际命中的窗口 ───────────────────────────

def test_input_result_serializes_window_selector_and_handle():
    from src.tools._window_input.result import InputResult

    payload = InputResult(
        action="click", backend="windows", window_pid=1, window_title="",
        window_selector="popup", window_handle="0x4A0BF0",
    ).to_dict()
    assert payload["window_selector"] == "popup"
    assert payload["window_handle"] == "0x4A0BF0"


def test_input_result_defaults_selector_to_main():
    from src.tools._window_input.result import InputResult

    payload = InputResult(
        action="click", backend="windows", window_pid=1, window_title="",
    ).to_dict()
    assert payload["window_selector"] == "main"
    assert payload["window_handle"] == ""


def test_windows_backend_reports_hit_window(monkeypatch):
    """注入结果回传选择器与实际句柄，便于发现 '#N' 漂移到其它窗口。"""
    monkeypatch.setattr(input_win_module.winapi, "ensure_process_dpi_aware",
                        lambda: True)
    backend = WindowsInputBackend(locator=_locator, driver=FakeDriver(foreground=True))
    result = backend.send(1234, build_action(
        "click", {"x": 5, "y": 5, "window": "popup"}))
    payload = result.to_dict()
    assert payload["window_selector"] == "popup"
    assert payload["window_handle"] == "0x20"
    assert payload["delivery"] == "sendinput"
