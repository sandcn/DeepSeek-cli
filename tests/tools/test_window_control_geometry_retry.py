"""窗口几何动作被目标程序改写时的自动补偿测试。

背景（真实用例）：对泰拉瑞亚执行 ``window_action=fit``（x=120, y=80,
1240x740）后，窗口尺寸正确但位置变成 (660,350)——游戏在收到 ``WM_SIZE``
时按自己的逻辑把窗口重新居中了。手动再 ``move`` 一次才摆到目标位置。

修复后：``move`` / ``resize`` / ``fit`` 执行完会读回实际几何，与请求偏差超过
2px 时自动补发一次修正（先尺寸、后位置），结果里用 ``geometry_retry`` 如实
回报补发步骤与最终是否一致；``requested`` 字段给出请求的几何值。
"""

from __future__ import annotations

from src.tools._screenshot import win as win_module
from src.tools._screenshot import winapi
from src.tools._screenshot.windows import WindowControlRequest, WindowInfo


class _FakeWindow:
    """可被 mock 的窗口状态：改尺寸时按「程序自身居中」模拟真实游戏行为。"""

    def __init__(self, x: int, y: int, width: int, height: int, *,
                 recenter_on_resize: bool = True,
                 screen: tuple[int, int] = (2560, 1440)):
        self.state = {"x": x, "y": y, "width": width, "height": height}
        self.recenter_on_resize = recenter_on_resize
        self.screen = screen
        self.calls: list[tuple] = []

    def set_window_pos(self, handle, x, y, width, height, flags=0):
        self.calls.append((x, y, width, height, flags))
        resized = not (flags & winapi.SWP_NOSIZE)
        if resized:
            self.state["width"], self.state["height"] = int(width), int(height)
            if self.recenter_on_resize:
                self.state["x"] = (self.screen[0] - int(width)) // 2
                self.state["y"] = (self.screen[1] - int(height)) // 2
        if not (flags & winapi.SWP_NOMOVE) and not (
                resized and self.recenter_on_resize):
            self.state["x"], self.state["y"] = int(x), int(y)
        return True

    def window_state(self, _handle):
        return {"exists": True, "minimized": False, "visible": True,
                "foreground": True, "topmost": False, **self.state}


def _setup(monkeypatch, window: _FakeWindow) -> None:
    monkeypatch.setattr(win_module.winapi, "ensure_process_dpi_aware",
                        lambda: True)
    monkeypatch.setattr(win_module, "resolve_window_pids", lambda pid: {1})
    info = WindowInfo(handle=0x10, pid=1, title="Game", class_name="GameWindow",
                      left=window.state["x"], top=window.state["y"],
                      width=window.state["width"],
                      height=window.state["height"], main=True)
    monkeypatch.setattr(win_module, "enumerate_window_infos",
                        lambda pids: [info])
    monkeypatch.setattr(win_module, "window_state", window.window_state)
    monkeypatch.setattr(win_module.winapi, "set_window_pos",
                        window.set_window_pos)
    monkeypatch.setattr(win_module, "_CONTROL_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(win_module.time, "sleep", lambda seconds: None)


def test_fit_compensates_after_program_recenters(monkeypatch):
    window = _FakeWindow(100, 60, 1462, 866)
    _setup(monkeypatch, window)
    detail = win_module.control_window(
        1, WindowControlRequest("fit", x=120, y=80, width=1240, height=740))
    assert (detail["after"]["x"], detail["after"]["y"]) == (120, 80)
    assert (detail["after"]["width"], detail["after"]["height"]) == (1240, 740)
    assert detail["geometry_retry"]["steps"] == ["move"]
    assert detail["geometry_retry"]["matched"] is True
    assert detail["requested"] == {"window_action": "fit", "x": 120, "y": 80,
                                   "width": 1240, "height": 740}


def test_resize_restores_original_position(monkeypatch):
    """resize 只应改尺寸：程序把窗口居中后，位置还原到动作前。"""
    window = _FakeWindow(300, 200, 800, 600)
    _setup(monkeypatch, window)
    detail = win_module.control_window(
        1, WindowControlRequest("resize", width=1000, height=700))
    assert (detail["after"]["width"], detail["after"]["height"]) == (1000, 700)
    assert (detail["after"]["x"], detail["after"]["y"]) == (300, 200)
    assert detail["geometry_retry"]["steps"] == ["move"]


def test_move_without_interference_has_no_retry(monkeypatch):
    window = _FakeWindow(100, 60, 800, 600)
    _setup(monkeypatch, window)
    detail = win_module.control_window(
        1, WindowControlRequest("move", x=50, y=40))
    assert (detail["after"]["x"], detail["after"]["y"]) == (50, 40)
    assert "geometry_retry" not in detail
    assert detail["requested"] == {"window_action": "move", "x": 50, "y": 40}


def test_move_retries_when_program_changes_position(monkeypatch):
    """程序持续改写位置时：补发一次，如实报告仍未匹配（不再死循环）。"""
    window = _FakeWindow(100, 60, 800, 600)
    _setup(monkeypatch, window)

    def _stubborn(handle, x, y, width, height, flags=0):
        window.calls.append((x, y, width, height, flags))
        window.state.update(x=7, y=9)  # 无论请求什么位置都改写成 (7, 9)
        return True

    monkeypatch.setattr(win_module.winapi, "set_window_pos", _stubborn)
    detail = win_module.control_window(
        1, WindowControlRequest("move", x=50, y=40))
    assert detail["geometry_retry"]["steps"] == ["move"]
    assert detail["geometry_retry"]["matched"] is False
    assert "hint" in detail["geometry_retry"]
    assert len(window.calls) == 2  # 原请求 + 一次补偿，不会无限重试
