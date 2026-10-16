"""UI Automation 控件枚举（``_screenshot.uia``）与集成测试。

覆盖：可用性探测（非 Windows 直接不可用）、``uia`` 枚举结果接入
``win.list_child_elements``（优先 UIA、异常 / 空结果回退经典 Win32 枚举）、
``ElementInfo`` 的显式类型提示与来源字段。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot import uia, win as win_module
from src.tools._screenshot.elements import ElementInfo, item_to_dict
from src.tools._screenshot.windows import WindowInfo


def _reset_uia_availability():
    uia._AVAILABLE = None


@pytest.fixture(autouse=True)
def _restore_availability():
    yield
    _reset_uia_availability()


def _target():
    return WindowInfo(handle=0x1234, pid=1, title="App", class_name="AppClass",
                      width=800, height=600)


def _fake_element(handle=0x1, text="OK", *, top=20, source="uia"):
    return ElementInfo(handle=handle, pid=1, class_name="Button", text=text,
                       left=10, top=top, width=30, height=10,
                       control_type_hint="button", source=source)


# ── 可用性 ──────────────────────────────────────────────

def test_available_false_off_windows(monkeypatch):
    _reset_uia_availability()
    monkeypatch.setattr(uia.winapi, "is_windows_platform", lambda: False)
    assert uia.available() is False


def test_enumerate_rejects_bad_handle():
    with pytest.raises(ValueError):
        uia.enumerate_elements(0)
    with pytest.raises(ValueError):
        uia.enumerate_elements("123")  # type: ignore[arg-type]


# ── 集成到 win.list_child_elements ──────────────────────

def test_list_child_elements_prefers_uia(monkeypatch):
    monkeypatch.setattr(uia, "available", lambda: True)
    monkeypatch.setattr(uia, "enumerate_elements",
                        lambda hwnd: [_fake_element(top=5), _fake_element(handle=2, top=1)])
    result = win_module.list_child_elements(_target())
    assert [item.top for item in result] == [1, 5]
    assert all(item.source == "uia" for item in result)


def test_list_child_elements_falls_back_when_uia_empty(monkeypatch):
    monkeypatch.setattr(uia, "available", lambda: True)
    monkeypatch.setattr(uia, "enumerate_elements", lambda hwnd: [])
    monkeypatch.setattr(win_module, "_list_elements_via_win32",
                        lambda target: [_fake_element(source="win32")])
    result = win_module.list_child_elements(_target())
    assert result and result[0].source == "win32"


def test_list_child_elements_falls_back_when_uia_raises(monkeypatch):
    monkeypatch.setattr(uia, "available", lambda: True)

    def _boom(hwnd):
        raise uia.UiaError("boom")

    monkeypatch.setattr(uia, "enumerate_elements", _boom)
    monkeypatch.setattr(win_module, "_list_elements_via_win32",
                        lambda target: [_fake_element(source="win32")])
    result = win_module.list_child_elements(_target())
    assert result and result[0].source == "win32"


def test_list_child_elements_falls_back_when_uia_unavailable(monkeypatch):
    monkeypatch.setattr(uia, "available", lambda: False)
    monkeypatch.setattr(win_module, "_list_elements_via_win32",
                        lambda target: [_fake_element(source="win32")])
    assert win_module.list_child_elements(_target())[0].source == "win32"


# ── ElementInfo 扩展字段 ────────────────────────────────

def test_control_type_hint_overrides_class():
    item = ElementInfo(handle=1, pid=1, class_name="Custom", text="x",
                       left=0, top=0, width=1, height=1,
                       control_type_hint="edit")
    assert item.control_type == "edit"
    plain = ElementInfo(handle=1, pid=1, class_name="Button", text="x",
                        left=0, top=0, width=1, height=1)
    assert plain.control_type == "button"


def test_item_to_dict_includes_source():
    payload = item_to_dict(_fake_element())
    assert payload["source"] == "uia"
    assert payload["type"] == "button"
