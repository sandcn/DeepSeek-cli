"""窗口描述的 topmost / owner 扩展字段测试（_screenshot/windows.py）。

覆盖：默认值、summary 中的 topmost 标记、to_dict 输出 owner_hex、
describe_windows 透传，以及缺省（非 Windows 平台）时字段为「未提供」。
"""

from __future__ import annotations

from src.tools._screenshot.windows import (
    WindowInfo,
    describe_windows,
    window_geometry,
)


def _window(**kwargs) -> WindowInfo:
    base = dict(handle=0x100, pid=10, title="t", class_name="C",
                width=100, height=50, left=1, top=2, order=0)
    base.update(kwargs)
    return WindowInfo(**base)


def test_default_fields_are_unset():
    info = _window()
    assert info.topmost is None
    assert info.owner == 0
    assert info.owner_hex == ""
    assert "topmost" not in info.summary()


def test_topmost_flag_in_summary():
    info = _window(topmost=True)
    assert "topmost" in info.summary()
    assert "topmost" not in _window(topmost=False).summary()


def test_to_dict_exposes_owner_and_topmost():
    info = _window(topmost=True, owner=0xABC)
    payload = info.to_dict()
    assert payload["topmost"] is True
    assert payload["owner"] == 0xABC
    assert payload["owner_hex"] == "0xABC"


def test_describe_windows_passes_fields_through():
    windows = [_window(handle=1, topmost=True, owner=0x20, order=0),
               _window(handle=2, topmost=False, order=1)]
    described = describe_windows(windows)
    assert described[0]["topmost"] is True
    assert described[0]["owner_hex"] == "0x20"
    assert described[1]["topmost"] is False
    assert described[1]["owner_hex"] == ""


def test_window_geometry_unaffected_by_new_fields():
    target = _window(handle=7, topmost=True, owner=3, left=10, top=20)
    geometry = window_geometry([target], target)
    assert geometry["rect"] == {"x": 10, "y": 20, "width": 100, "height": 50}
